"""translation_tool/utils/species_cache.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/translation_tool/utils/species_cache.py (僅儲存成功查詢的修正版)

from __future__ import annotations

import csv
import logging
import re
import time
from pathlib import Path

import requests
from opencc import OpenCC

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

_SPECIES_NAME_REGEX = re.compile(
    r"^[A-Z][a-z-]+ (?:× )?[a-z][a-z-]+"
    r"(?: (?:(?:subsp|ssp|var|f)\. )?[a-z][a-z-]+)?$"
)
_WIKI_LANGUAGE_REGEX = re.compile(r"^[a-z][a-z0-9-]{0,19}$")
_WIKI_REQUEST_TIMEOUT_SECONDS = 15
_WIKI_REQUEST_ATTEMPTS = 3
_WIKI_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_WIKI_USER_AGENT = (
    "Minecraft-translate/0.8.0 "
    "(https://github.com/jlin53882/Minecraft-translate; species lookup)"
)

# --- 核心快取操作函式 ---
_CACHE_DIR: Path | None = None
_CACHE_FILE: Path | None = None
_species_cache_data: dict[str, str] | None = None
_tw_converter: OpenCC | None = None

# 防止 initialize_species_cache() 被重複執行
_initialized = False


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

        configured_language = (
            str(species_config.get("wikipedia_language", _WIKI_LANG)).strip().lower()
        )
        if _WIKI_LANGUAGE_REGEX.fullmatch(configured_language):
            _WIKI_LANG = configured_language
        else:
            log.warning(
                "Wikipedia 語言代碼 %r 無效，沿用 %r。",
                configured_language,
                _WIKI_LANG,
            )
        _RATE_LIMIT_DELAY = max(
            0.0,
            float(species_config.get("wikipedia_rate_limit_delay", _RATE_LIMIT_DELAY)),
        )
        log.info(
            "Wikipedia 語言已設定為 '%s'，請求延遲為 %s 秒。",
            _WIKI_LANG,
            _RATE_LIMIT_DELAY,
        )

        _initialized = True
        log.info("學名快取系統初始化完成。")
        return True

    except Exception:  # 初始化失敗時必須降級為不可用。
        log.exception("初始化學名快取時發生嚴重錯誤")
        _initialized = False
        return False


def is_potential_species_name(name: str) -> bool:
    """判斷是否可能是物種名稱。"""
    if not isinstance(name, str):
        return False
    return bool(_SPECIES_NAME_REGEX.fullmatch(_normalize_species_name(name)))


def _normalize_species_name(name: str) -> str:
    """移除使用者輸入前後與重複空白，保留學名本身的大小寫。"""
    return " ".join(name.split())


def _wikipedia_api_url() -> str:
    """回傳已驗證語言代碼對應的 MediaWiki Action API URL。"""
    if not _WIKI_LANGUAGE_REGEX.fullmatch(_WIKI_LANG):
        raise ValueError(f"無效的 Wikipedia 語言代碼: {_WIKI_LANG!r}")
    return f"https://{_WIKI_LANG}.wikipedia.org/w/api.php"


def _convert_wikipedia_title(title: str) -> str:
    """中文 Wikipedia 使用單一域名時，將條目標題正規化為台灣繁體。"""
    global _tw_converter
    if _WIKI_LANG != "zh":
        return title
    try:
        if _tw_converter is None:
            _tw_converter = OpenCC("s2twp")
        return _tw_converter.convert(title)
    except Exception as exc:  # noqa: BLE001 - 轉換失敗時仍可使用原始條目標題
        log.warning("Wikipedia 條目標題轉為台灣繁體失敗，沿用原文: %r", exc)
        return title


def _query_wikipedia_title(species_name: str) -> str | None:
    """透過 MediaWiki Action API 解析學名轉址並回傳條目標題。"""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "info",
        "redirects": "1",
        "titles": species_name,
    }
    headers = {"User-Agent": _WIKI_USER_AGENT}

    for attempt in range(_WIKI_REQUEST_ATTEMPTS):
        if attempt == 0:
            time.sleep(_RATE_LIMIT_DELAY)
        else:
            time.sleep(min(2 ** (attempt - 1), 2))

        try:
            response = requests.get(
                _wikipedia_api_url(),
                params=params,
                headers=headers,
                timeout=_WIKI_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if (
                status_code not in _WIKI_RETRYABLE_STATUS_CODES
                or attempt == _WIKI_REQUEST_ATTEMPTS - 1
            ):
                raise
            log.warning(
                "Wikipedia API 暫時回傳 HTTP %s，準備重試 '%s'。",
                status_code,
                species_name,
            )
            continue
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.JSONDecodeError,
        ) as exc:
            if attempt == _WIKI_REQUEST_ATTEMPTS - 1:
                raise
            log.warning(
                "Wikipedia API 暫時無法回應，準備重試 '%s': %r",
                species_name,
                exc,
            )
            continue

        if error := payload.get("error"):
            raise RuntimeError(f"Wikipedia API 錯誤: {error}")

        pages = payload.get("query", {}).get("pages", [])
        if isinstance(pages, dict):
            pages = list(pages.values())
        if not pages or pages[0].get("missing"):
            return None

        title = pages[0].get("title")
        if not isinstance(title, str):
            return None
        normalized_title = title.split("(", 1)[0].strip()
        return _convert_wikipedia_title(normalized_title) or None

    return None


def query_wikipedia_and_update_cache(species_name: str) -> str | None:
    """線上查詢維基百科並更新快取。"""
    if _species_cache_data is None:
        return None

    normalized_name = _normalize_species_name(species_name)
    log.debug(f"開始線上查詢: {normalized_name}")
    try:
        common_name = _query_wikipedia_title(normalized_name)
        if common_name is None:
            log.warning(
                "線上查詢失敗：找不到 '%s' 的 Wikipedia 頁面。",
                normalized_name,
            )
            return None
        log.info(f"線上查詢成功: {normalized_name} -> {common_name}")

        _species_cache_data[normalized_name] = common_name
        if _CACHE_FILE:
            try:
                with open(_CACHE_FILE, "a", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f, delimiter="\t")
                    writer.writerow([normalized_name, common_name])
            except OSError as e:
                log.error(f"寫入快取檔案 {_CACHE_FILE} 失敗: {e!r}")

        return common_name
    except Exception as e:  # noqa: BLE001 - 外部 API 與回應解析錯誤需降級為未找到
        log.error(
            "線上查詢 '%s' 時發生網路或 API 錯誤: %r",
            normalized_name,
            e,
        )
        return None


def lookup_species_name(name: str) -> str | None:
    """查詢物種名稱。"""
    if not _initialized and not initialize_species_cache():
        log.error("學名快取系統初始化失敗，查詢功能無法使用。")
        return None

    if _species_cache_data is None:
        return None

    normalized_name = _normalize_species_name(name)
    if normalized_name in _species_cache_data:
        cached_result = _species_cache_data[normalized_name]
        log.debug(f"從快取中找到 '{normalized_name}': '{cached_result}'")
        return cached_result if cached_result else None

    if is_potential_species_name(normalized_name):
        return query_wikipedia_and_update_cache(normalized_name)

    return None
