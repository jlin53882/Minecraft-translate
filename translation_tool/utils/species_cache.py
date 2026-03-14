"""translation_tool/utils/species_cache.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/translation_tool/utils/species_cache.py (僅儲存成功查詢的修正版)

import csv
import time
import re
import logging
from pathlib import Path
from typing import Dict, Optional

from .config_manager import load_config, resolve_project_path

log = logging.getLogger(__name__)

# --- 全域變數 ---
# _CACHE_DIR_NAME = "學名資料庫"
# 設定學名資料庫
_CACHE_DIR_NAME = (
    load_config().get("species_cache", {}).get("cache_directory", "學名資料庫")
)
# _CACHE_FILENAME = "species_cache.tsv"

# 設定學名快取檔名
_CACHE_FILENAME = (
    load_config().get("species_cache", {}).get("cache_filename", "species_cache.tsv")
)
# _WIKI_LANG = "zh"

# 設定語言
_WIKI_LANG = load_config().get("species_cache", {}).get("wikipedia_language", "zh")
# _RATE_LIMIT_DELAY = 0.5

# 設定延遲時間
_RATE_LIMIT_DELAY = (
    load_config().get("species_cache", {}).get("wikipedia_rate_limit_delay", 0.5)
)

_SPECIES_NAME_REGEX = re.compile(r"^[A-Z][a-z]+ [a-z]+$")

# 表示是否成功 import wikipedia
_WIKIPEDIA_AVAILABLE = False

# --- 核心快取操作函式 ---
_CACHE_DIR: Optional[Path] = None
_CACHE_FILE: Optional[Path] = None
_species_cache_data: Optional[Dict[str, str]] = None

# 防止 initialize_species_cache() 被重複執行
_initialized = False

try:
    import wikipedia

    _WIKIPEDIA_AVAILABLE = True
    log.info("Wikipedia 函式庫已成功載入。")
except ImportError:
    log.warning(
        "未找到 Wikipedia 函式庫 (請執行 pip install wikipedia)。線上查詢功能將不可用。"
    )
except Exception as e:
    log.error(f"載入 Wikipedia 函式庫時發生未知錯誤: {e}")

def initialize_species_cache():
    """初始化學名快取系統。"""
    global \
        _species_cache_data, \
        _CACHE_DIR, \
        _CACHE_FILE, \
        _WIKI_LANG, \
        _RATE_LIMIT_DELAY, \
        _initialized
    if _initialized:
        return True

    log.info("正在初始化學名快取系統...")
    try:
        species_config = load_config().get("species_cache", {})

        cache_dir_name = species_config.get("cache_directory", _CACHE_DIR_NAME)
        _CACHE_DIR = resolve_project_path(cache_dir_name)
        cache_filename = species_config.get("cache_filename", _CACHE_FILENAME)
        _CACHE_FILE = _CACHE_DIR / cache_filename
        _CACHE_DIR.mkdir(exist_ok=True)

        _species_cache_data = {}
        if _CACHE_FILE.exists():
            with open(_CACHE_FILE, "r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f, delimiter="\t")
                for row in reader:
                    if len(row) >= 2:
                        _species_cache_data[row[0]] = row[1]
            log.info(
                f"成功從 {_CACHE_FILE} 載入 {len(_species_cache_data)} 條學名快取。"
            )
        else:
            log.info(f"快取檔案 {_CACHE_FILE} 不存在，將在查詢後自動建立。")

        if _WIKIPEDIA_AVAILABLE:
            _WIKI_LANG = species_config.get("wikipedia_language", _WIKI_LANG)
            _RATE_LIMIT_DELAY = float(
                species_config.get("wikipedia_rate_limit_delay", _RATE_LIMIT_DELAY)
            )
            wikipedia.set_lang(_WIKI_LANG)
            log.info(
                f"Wikipedia 語言已設定為 '{_WIKI_LANG}'，請求延遲為 {_RATE_LIMIT_DELAY} 秒。"
            )

        _initialized = True
        log.info("學名快取系統初始化完成。")
        return True

    except Exception as e:
        log.error(f"初始化學名快取時發生嚴重錯誤: {e}", exc_info=True)
        _initialized = False
        return False

def is_potential_species_name(name: str) -> bool:
    """判斷是否可能是物種名稱。"""
    if not isinstance(name, str):
        return False
    return bool(_SPECIES_NAME_REGEX.match(name))

def query_wikipedia_and_update_cache(species_name: str) -> Optional[str]:
    """線上查詢維基百科並更新快取。"""
    if not _WIKIPEDIA_AVAILABLE or _species_cache_data is None:
        return None

    log.debug(f"開始線上查詢: {species_name}")
    try:
        time.sleep(_RATE_LIMIT_DELAY)
        page = wikipedia.page(species_name, auto_suggest=False)
        common_name = page.title.split("(")[0].strip()
        log.info(f"線上查詢成功: {species_name} -> {common_name}")

        # *** 修改點：只有在查詢成功時，才更新記憶體快取並寫入檔案 ***
        _species_cache_data[species_name] = common_name
        if _CACHE_FILE:
            try:
                with open(_CACHE_FILE, "a", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f, delimiter="\t")
                    writer.writerow([species_name, common_name])
            except IOError as e:
                log.error(f"寫入快取檔案 {_CACHE_FILE} 失敗: {e}")

        return common_name

    except wikipedia.exceptions.PageError:
        log.warning(f"線上查詢失敗：找不到 '{species_name}' 的 Wikipedia 頁面。")
        _species_cache_data[species_name] = ""  # 僅更新記憶體快取，避免重複查詢
        return None
    except wikipedia.exceptions.DisambiguationError as e:
        log.warning(
            f"線上查詢 '{species_name}' 時遇到消歧義頁面，將嘗試選擇第一個選項: {e.options[0] if e.options else '無選項'}"
        )
        _species_cache_data[species_name] = ""  # 僅更新記憶體快取
        if e.options:
            return query_wikipedia_and_update_cache(e.options[0])
        return None
    except Exception as e:
        log.error(f"線上查詢 '{species_name}' 時發生未知網路或API錯誤: {e}")
        _species_cache_data[species_name] = ""  # 僅更新記憶體快取
        return None

def lookup_species_name(name: str) -> Optional[str]:
    """查詢物種名稱。"""
    if not _initialized:
        if not initialize_species_cache():
            log.error("學名快取系統初始化失敗，查詢功能無法使用。")
            return None

    if _species_cache_data is None:
        return None

    if name in _species_cache_data:
        cached_result = _species_cache_data[name]
        log.debug(f"從快取中找到 '{name}': '{cached_result}'")
        return cached_result if cached_result else None

    if is_potential_species_name(name):
        return query_wikipedia_and_update_cache(name)

    return None

initialize_species_cache()
