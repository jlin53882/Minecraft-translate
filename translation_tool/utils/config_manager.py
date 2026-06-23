"""config_manager.py（設定讀寫與合併）

提供：
- DEFAULT_CONFIG：缺檔/缺欄位時的保底值（不是要覆蓋使用者設定）。
- load_config()：讀取 `config.json`，並用深度合併補齊新欄位，維持向後相容。
- save_config()：寫回設定並做基本可讀性驗證（避免寫出壞 JSON）。

維護注意：
- `lm_translator.models` 視為「使用者資料」，刻意不做 deep merge；
  以避免預設模型列表與使用者設定混在一起造成誤啟用。
- 本模組應避免在 import 時就改動全域 logging；logging 初始化交由 entry point 決定。
"""

# /minecraft_translator_flet/translator_tool/utils/config_manager.py (最終修正版)

import os
import json
import logging
from datetime import datetime
from pathlib import Path
import copy

# PR27：統一路徑解析基準，避免 legacy cwd 依賴造成找不到 config / 資源檔。
def get_project_root() -> Path:
    """取得專案根目錄路徑。"""
    return Path(__file__).resolve().parents[2]

PROJECT_ROOT = get_project_root()
CONFIG_PATH = PROJECT_ROOT / "config.json"
EXAMPLE_PATH = PROJECT_ROOT / "config.example.json"

def load_config_example() -> dict:
    """讀取 config.example.json，不存在或解析失敗時回傳空 dict。

    注意：config.example.json 屬於 repo 原始碼的一部分，
    不會被複製進使用者的實際工作目錄。只有 load_config() 在
    Layer 2 fallback 時會嘗試讀取它。

    用途：新版本安裝時補足 config.json 缺少的新欄位。
    """
    if not EXAMPLE_PATH.exists():
        return {}
    try:
        with EXAMPLE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def get_default_block(name: str):
    """取得 DEFAULT_CONFIG 中指定區塊（如 'lang_merger', 'extractor'）。"""
    return copy.deepcopy(DEFAULT_CONFIG.get(name, {}))

def get_default(path: str, default=None):
    """依路徑讀取 DEFAULT_CONFIG 中的值，例如 'lang_merger.pending_folder_name'。
    
    參數：
        path: dot-separated path，如 'lang_merger.pending_folder_name'
        default: 找不到時的回傳值
    返回：
        DEFAULT_CONFIG 中該路徑的值，或 default
    """
    keys = path.split(".")
    val = DEFAULT_CONFIG
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val


def resolve_project_path(path_like: str | os.PathLike | None) -> Path:
    """解析專案相對路徑為絕對路徑。

    參數：
        path_like: 相對路徑字串或 None

    回傳：
        Path: 絕對路徑
    """
    if path_like is None:
        return PROJECT_ROOT

    p = Path(path_like)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p

# DEFAULT_CONFIG 是「缺檔或缺欄位時的保底值」，不是要取代使用者設定；
# load_config() 會用它做深度合併，讓新欄位可以向後相容地補進舊 config.json。
DEFAULT_CONFIG = {
    "logging": {
        "log_level": "INFO",
        "log_format": "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s",
        "log_dir": "logs",
    },
    "translator": {
        "output_dir_name": "zh_tw_generated",
        "replace_rules_path": "replace_rules.json",
        "cache_directory": "快取資料",
        "enable_cache_saving": True,
        "parallel_execution_workers": 4,
        "custom_translator_folder": "custom_translators",
        "cjk_ratio_threshold": 0.7,
    },
    "ftb_translator": {
        "output_dir_name": "FTB任務翻譯輸出",
    },
    "species_cache": {
        "cache_directory": "學名資料庫",
        "cache_filename": "species_cache.tsv",
        "wikipedia_language": "zh",
        "wikipedia_rate_limit_delay": 0.5,
    },
    "lm_translator": {
        "temperature": 0.3,
        "lm_translate_folder_name": "LM翻譯後",
        "initial_batch_size_patchouli": 100,
        "initial_batch_size_lang": 300,
        "initial_batch_size_ftb": 200,
        "initial_batch_size_kubejs": 200,
        "initial_batch_size_md": 100,
        "min_batch_size": 50,
        "batch_shrink_factor": 0.5,
        "batch_write_interval": 2,  # 每 N 個批次寫一次快取（避免超限觸發 overflow）
        "rate_limit": {
            "timeout": 600,
            "sleep_seconds_between_batches": 0.0,
        },
        "models": {
            "gemini-2.5-flash": {"enabled": True},
        },
        "keys": [
            "YOUR_GEMINI_API_KEY_1",
            "YOUR_GEMINI_API_KEY_2",
        ],
        "patchouli_system_prompt": (
            "你是專業的 Minecraft Patchouli 手冊翻譯員。\n\n"
            "你正在翻譯一個「ID → Value 對照表」。\n\n"
            "⚠️【極重要規則 — ID 不可變】⚠️\n"
            "- items[].id 是不可變的識別符號\n"
            "- id 不具有任何語意，也不對應任何 JSON 結構\n"
            "- id 只能被視為純文字索引\n"
            "- 絕對禁止：\n"
            "  - 修改、重寫、補零、轉型、排序、重編任何 id\n"
            "  - 新增或刪除任何 id\n"
            "  - 嘗試推測 id 與內容的關聯\n\n"
            "📌 任務規則：\n"
            "1. 只允許修改 items[].value 的字串內容\n"
            "2. items[].id 必須與輸入完全一字不差\n"
            "3. items 的數量與順序必須與輸入完全一致\n"
            "4. 如果你不確定如何翻譯，請原樣回傳 value\n"
            "5. 回傳必須是合法 JSON，且格式與輸入完全一致\n"
            "6. 僅翻譯為繁體中文（台灣用語）\n"
            "7. 保留 §, %, {}, $(...) 等所有符號與格式\n"
            "8. 單位（mb、tick 等）請保留原文\n"
            "9. Minecraft 請保持原文，不要翻譯成「當個創世神」\n"
            "10. 每一筆 value 必須只根據該筆原文自身內容翻譯\n"
            "11. 只要 value 包含人類語言就必須翻譯\n"
            "12. 學名請翻譯為台灣常用語（如 Creeper → 苦力怕）,(Spawn Egg-> 生怪蛋),(cobblestone->鵝卵石)"
        ),
        "lang_system_prompt": (
            "你正在翻譯 Minecraft 語言檔案（JSON 格式）。\n\n"
            "你收到的是一個「ID → value 對照表」。\n\n"
            "⚠️【極重要規則 — ID 不可變】⚠️\n"
            "- items[].id 是唯一識別符號\n"
            "- id 不具有任何語意\n"
            "- 絕對禁止：\n"
            "  - 修改、轉型、補零、重排、推測或重寫任何 id\n"
            "  - 新增或刪除任何 item\n\n"
            "📌 任務規則：\n"
            "1. 只允許修改 items[].value 的字串內容\n"
            "2. items[].id 必須與輸入完全一字不差\n"
            "3. items 的數量與順序必須與輸入完全一致\n"
            "4. 如果你不確定如何翻譯，請原樣回傳 value\n"
            "5. 回傳必須是合法 JSON，格式必須為 {\"items\":[{\"id\":...,\"value\":...}, ...]}\n"
            "6. 僅翻譯為繁體中文（台灣用語）\n"
            "7. 保留 §, %, {}, $(...) 等所有符號與格式\n"
            "8. 單位（mb、tick 等）請保留原文\n"
            "9. Minecraft 請保持原文\n"
            "10. 每一筆 value 只依該筆原文翻譯\n"
            "11. 只要 value 包含人類語言就必須翻譯\n"
        ),
        "translator": {
            "skip_terms": [
                "api documentation",
                "api docs",
                "documentation",
                "discord",
                "github",
                "homepage",
                "mod page",
                "modpack",
                "official website",
                "patreon",
                "Twitter",
                "Modrinth",
                "CurseForge",
                "Crowdin",
                "Twitch",
                "Wiki",
                "Minecraft",
                "Forge",
                "YouTube",
                "Reddit",
                "Ko-fi",
                "Flattr",
            ],
            "translatable_keywords": [
                "text",
                "name",
                "title",
                "description",
                "subtitle",
                "hover",
                "note",
                "warning",
                "quote",
                "paragraph",
                "body",
                "header",
                "footer",
                "heading",
                "effects",
                "category",
                "link_text",
                "pages.title",
            ],
        },
        "patchouli": {
            "dir_names": ["patchouli_books", "book", "manual", "guidebook"],
        },
    },
    "output_bundler": {
        "output_zip_name": "可使用翻譯.zip"
    },
    "jar_extractor": {
        "lang_codes": ["en_us", "zh_cn", "zh_tw"],
    },
    "lang_merger": {
        "pending_folder_name": "待翻譯",
        "pending_organized_folder_name": "待翻譯整理需翻譯",
        "filtered_pending_min_count": 3,
        "quarantine_folder_name": "問題檔案skipped_json",
        "process_zh_cn_files": True,
        "skip_zh_cn_when_only_process_lang": False,
        "patchouli_skip_en_us_when_zh_cn_exists": False,
        "patchouli_effective_translation_threshold": 0.5,
        "zh_en_letter_threshold": 2,
    },
    "extractor": {
        "output_folder_names": {
            "lang_extract": "_提取lang_輸出",
            "book_extract": "_提取book_輸出",
            "lang_preview": "_預覽lang_輸出",
            "book_preview": "_預覽book_輸出",
            "dual_extract": "_提取both_輸出",
            "dual_preview": "_預覽both_輸出",
        },
        "target_language": ["zh_tw"],
        "skip_zh_cn_extract": False,
    },
}
def load_config(config_path: str | os.PathLike | None = None) -> dict:
    """
    載入並合併設定檔，實作三層 fallback 機制。

    三層 priority（高 → 低）：
      Layer 1: config.json       — 用戶實際值（最高優先，單一 json 檔）
      Layer 2: config.example.json — 文件預設值（新版本補欄位用）
      Layer 3: DEFAULT_CONFIG    — 程式碼 fallback（最終保底，唯一真相來源）

    合併順序：deep_merge(deep_merge(DEFAULT_CONFIG, example), user_config)
    - user_config 覆蓋 example 覆蓋 DEFAULT_CONFIG
    - `lm_translator.models` 不做 deep merge（視為使用者資料，完全替換）

    适用场景：
    - 新安裝：config.json 不存在 → 吃到 example + default 的值
    - 升級：config.json 少新欄位 → example 補上缺失欄位
    - 使用者自訂：config.json 有值 → 以使用者為準

    回傳：合併後的新 dict（避免直接回傳 DEFAULT_CONFIG 物件被外部修改）。
    """
    resolved_config_path = resolve_project_path(config_path or CONFIG_PATH)

    # Layer 3: DEFAULT_CONFIG as base
    # 為什麼用 DEFAULT_CONFIG 而不是空 dict 作為起點？
    # 因為 DEFAULT_CONFIG 是「唯一真相來源」——所有欄位都應該有定義值，
    # example 只是用來補新欄位（example 多的欄位），不是用來覆蓋 DEFAULT 已有的值。
    base = copy.deepcopy(DEFAULT_CONFIG)

    # Layer 2: config.example.json
    example = load_config_example()
    if example:
        base = deep_merge(base, example)

    # Layer 1: config.json (user values) — only if file exists
    user_config = {}
    if resolved_config_path.exists():
        try:
            with resolved_config_path.open("r", encoding="utf-8") as f:
                user_config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"錯誤：讀取設定檔 {resolved_config_path} 失敗: {e}，將使用預設設定。")
            return base

    # Merge: user (Layer 1) > example (Layer 2) > default (Layer 3)
    # 全部用 deep_merge 一次搞定，確保 config.example.json 新增的 top-level key
    # 也會被正確合併進來，不只限於 DEFAULT_CONFIG 定義的 keys。
    config = deep_merge(base, user_config)

    # lm_translator.models 不允許 deep merge（視為使用者資料，完全替換）
    if "models" in user_config.get("lm_translator", {}):
        config["lm_translator"]["models"] = user_config["lm_translator"]["models"]

    # ATK-C-2: 對最終結果做驗證
    _validate_lm_translator_config(config["lm_translator"])
    if isinstance(config.get("translator"), dict):
        _validate_translator_config(config["translator"])
    return config

def save_config(config, config_path: str | os.PathLike | None = None):
    """
    儲存設定並檢查是否成功寫入。
    回傳 True = 寫入成功
          False = 寫入失敗
    """
    resolved_config_path = resolve_project_path(config_path or CONFIG_PATH)
    try:
        resolved_config_path.parent.mkdir(parents=True, exist_ok=True)
        with resolved_config_path.open("w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

        with resolved_config_path.open("r", encoding="utf-8") as f:
            written_data = json.load(f)

        # 能 dump 代表結構是乾淨的
        json.dumps(written_data, sort_keys=True)

        logging.info(f"設定已成功儲存並驗證至 {resolved_config_path}")
        return True

    except Exception as e:
        logging.error(f"錯誤：儲存或驗證設定檔失敗: {e}")
        return False

def setup_logging(config):
    """根據設定檔配置 logging。"""
    # 這個函式只做 logging 初始化本身；
    # 何時呼叫它，交給 main.bootstrap_runtime() 等 entry point 決定，
    # 避免 import module 時就把全域 logger 狀態改掉。
    # 🔥 關鍵修正：將 flet 模組的日誌級別提高 🔥
    flet_logger = logging.getLogger("flet")
    flet_logger.setLevel(logging.WARNING)  # 或 logging.ERROR

    # 🔥🔥🔥 正確地從 config["logging"] 讀取，而不是 config["log_level"] 🔥🔥🔥
    logging_cfg = config.get("logging", {})

    log_level = getattr(
        logging, logging_cfg.get("log_level", "INFO").upper(), logging.INFO
    )
    log_format = logging_cfg.get(
        "log_format", "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
    )
    log_dir = logging_cfg.get("log_dir", "logs")
    resolved_log_dir = resolve_project_path(log_dir)

    # 清理舊 handler
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 建立 log 資料夾
    today = datetime.now().strftime("%Y%m%d")
    log_folder = resolved_log_dir / today
    log_folder.mkdir(parents=True, exist_ok=True)
    log_file = log_folder / "app.log"

    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]

    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)
    logging.info("日誌系統已成功設定。")

def get_models_config(cfg: dict) -> dict[str, dict]:
    """
    安全取得 models 設定
    - 確保一定回傳 dict[str, dict]
    - 外部亂寫 list / str 都會被忽略
    """
    lm_cfg = cfg.get("lm_translator", {})
    models = lm_cfg.get("models", {})

    if not isinstance(models, dict):
        logging.warning("models 設定型別錯誤，已忽略（需為 dict）")
        return {}

    safe_models: dict[str, dict] = {}

    for model_name, model_cfg in models.items():
        if not isinstance(model_name, str):
            continue
        if not isinstance(model_cfg, dict):
            continue

        safe_models[model_name] = {"enabled": bool(model_cfg.get("enabled", False))}

    return safe_models

class ConfigValidationError(ValueError):
    """Config 欄位驗證失敗時拋出。"""
    pass


def _validate_lm_translator_config(lm: dict) -> None:
    """驗證 lm_translator 關鍵欄位的型別（ATK-C-2）。

    若驗證失敗，拋出 ConfigValidationError。
    這樣當使用者編輯 config.json 打錯時，錯誤會在啟動時就爆炸，
    而不是在翻譯到一半時才出現在奇怪的地方。

    驗證規則：
    - keys: 必須是 list（不接受 str）
    - initial_batch_size_*: 必須是 int（不接受 str）
    - parallel_execution_workers: 必須是 int > 0
    """
    # ⚠️ iniital 棄用警告（iniital 是拼寫錯誤，正確為 initial）
    iniital_keys = [k for k in lm if k.startswith("iniital_")]
    if iniital_keys:
        logging.warning(
            f"[iniital-deprecation] ⚠️ 偵測到已棄用的 iniital_* 設定鍵：{iniital_keys}。"
            f" 正確拼寫為 initial_batch_size_*，請更新 config.json。"
            f" iniital_* 鍵已不再被翻譯引擎讀取，將使用內建預設值。"
        )

    # 1. keys 必須是 list
    keys_val = lm.get("keys")
    if keys_val is not None and not isinstance(keys_val, list):
        raise ConfigValidationError(
            f"lm_translator.keys 必須為 list，"
            f"目前為 {type(keys_val).__name__}：'{keys_val}'"
        )

    # 2. initial_batch_size_* 必須是 int
    for key, value in lm.items():
        if key.startswith("initial_batch_size_") and value is not None:
            if not isinstance(value, int):
                raise ConfigValidationError(
                    f"lm_translator.{key} 必須為 int，"
                    f"目前為 {type(value).__name__}：'{value}'"
                )

    # 3. parallel_execution_workers 必須是 int > 0
    workers = lm.get("parallel_execution_workers")
    if workers is not None:
        if not isinstance(workers, int) or workers <= 0:
            raise ConfigValidationError(
                f"lm_translator.parallel_execution_workers 必須為正整數，"
                f"目前為 {type(workers).__name__}：{workers}"
            )

    # 4. temperature 必須是 0.0~2.0 的 float
    temp = lm.get("temperature")
    if temp is not None:
        if not isinstance(temp, (int, float)):
            raise ConfigValidationError(
                f"lm_translator.temperature 必須為數字，"
                f"目前為 {type(temp).__name__}：'{temp}'"
            )
        if not (0.0 <= float(temp) <= 2.0):
            raise ConfigValidationError(
                f"lm_translator.temperature 必須在 0.0~2.0 範圍內，目前為 {temp}"
            )

    # 5. models 必須是 dict（不接受 list）
    models_val = lm.get("models")
    if models_val is not None and not isinstance(models_val, dict):
        raise ConfigValidationError(
            f"lm_translator.models 必須為 dict，"
            f"目前為 {type(models_val).__name__}"
        )


def _validate_translator_config(translator: dict) -> None:
    """驗證 translator 區塊的型別（ATK-C-2）。"""
    workers = translator.get("parallel_execution_workers")
    if workers is not None and (not isinstance(workers, int) or workers <= 0):
        raise ConfigValidationError(
            f"translator.parallel_execution_workers 必須為正整數，"
            f"目前為 {type(workers).__name__}：{workers}"
        )



def deep_merge(default: dict, override: dict) -> dict:
    """

    """
    result = default.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result

class LazyConfigProxy:
    """延遲讀取 config，避免 module import 時就觸發 I/O 與 logging 初始化。"""

    # 這個 proxy 的目的是「保留舊介面相容性」：
    # 舊模組仍可用 `from config_manager import config`，
    # 但實際讀檔時機延後到真正取值的那一刻，而不是 import 當下。

    def _current(self) -> dict:
        """取得目前設定。"""
        return load_config()

    def get(self, key, default=None):
        """取得指定鍵的值。"""
        return self._current().get(key, default)

    def __getitem__(self, key):
        """取得鍵對應的值。"""
        return self._current()[key]

    def __contains__(self, key):
        """檢查鍵是否存在。"""
        return key in self._current()

    def __iter__(self):
        """回傳迭代器。"""
        return iter(self._current())

    def __len__(self):
        """回傳鍵的數量。"""
        return len(self._current())

    def items(self):
        """回傳鍵值對。"""
        return self._current().items()

    def keys(self):
        """回傳所有鍵。"""
        return self._current().keys()

    def values(self):
        """回傳所有值。"""
        return self._current().values()

    def copy(self):
        """複製目前設定。"""
        return self._current().copy()

    def __repr__(self):
        """回傳字串表示。"""
        return repr(self._current())

# 對外仍維持 `config` 這個名稱，讓既有呼叫點不用一次大改；
# 真正的目標是先移除 import-time side effect，再逐步收斂舊依賴。
config = LazyConfigProxy()
