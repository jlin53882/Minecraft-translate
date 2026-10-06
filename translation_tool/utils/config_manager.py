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

import copy
import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from translation_tool.utils.app_paths import get_data_root, get_resource_root
from translation_tool.utils.config_schema import (
    build_default_config,
    get_path,
    sensitive_paths,
)
from translation_tool.utils.fs_utils import fsync_directory
from translation_tool.utils.redaction import RedactingFormatter, register_secrets
from translation_tool.utils.ui_mirror import install_task_record_factory

log = logging.getLogger(__name__)


# PR27：統一路徑解析基準，避免 legacy cwd 依賴造成找不到 config / 資源檔。
def get_project_root() -> Path:
    """取得可寫資料的根目錄（原始碼模式為專案根目錄；打包後為 exe 所在資料夾）。"""
    return get_data_root()


PROJECT_ROOT = get_project_root()
CONFIG_PATH = PROJECT_ROOT / "config.json"
# config.example.json 隨程式附帶（唯讀），打包後在資源根目錄，不在可寫的資料根目錄
EXAMPLE_PATH = get_resource_root() / "config.example.json"

# PR-A：這些欄位仍由三層合併保留，避免舊 config 在升級時遺失；
# 但歷史追查沒有找到正式 runtime caller，因此不在新 UI 中宣稱可調整。
DEPRECATED_CONFIG_KEYS = frozenset(
    {
        "extractor.target_language",
        "translator.cjk_ratio_threshold",
    }
)


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
    except (OSError, json.JSONDecodeError):
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
# 預設值的唯一來源是 config_schema.SETTINGS（每個設定的 default），這裡只是由它建出。
DEFAULT_CONFIG = build_default_config()


# load_config 快取：以設定檔的 (mtime_ns, size) 判斷是否需要重新讀取。
# 翻譯流程每筆資料都會讀設定（11 萬筆約多花 100 秒），檔案未變就直接用快取。
_CONFIG_CACHE: dict = {"key": None, "config": None}
_CONFIG_CACHE_LOCK = threading.Lock()


def _sensitive_values(config: dict):
    """schema 標示為 sensitive 的設定值（字串，或字串清單裡的每一項）。"""
    for path in sensitive_paths():
        value = get_path(config, path)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str) and not item.startswith("YOUR_"):
                yield item


def _file_sig(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def clear_config_cache() -> None:
    """清除 load_config 快取（寫入設定後呼叫）。"""
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE["key"] = None
        _CONFIG_CACHE["config"] = None


def load_config_shared(config_path: str | os.PathLike | None = None) -> dict:
    """回傳快取中的設定物件（唯讀，呼叫端不可修改）；供高頻讀取使用。"""
    resolved_config_path = resolve_project_path(config_path or CONFIG_PATH)
    key = (
        str(resolved_config_path),
        _file_sig(resolved_config_path),
        str(EXAMPLE_PATH),
        _file_sig(EXAMPLE_PATH),
        id(DEFAULT_CONFIG),
    )
    with _CONFIG_CACHE_LOCK:
        if _CONFIG_CACHE["key"] == key:
            return _CONFIG_CACHE["config"]
    config, cacheable = _load_config_uncached(resolved_config_path)
    if cacheable:
        with _CONFIG_CACHE_LOCK:
            _CONFIG_CACHE["key"] = key
            _CONFIG_CACHE["config"] = config
    return config


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
    檔案未變動時使用快取，並回傳複本讓呼叫端可自由修改。
    """
    return copy.deepcopy(load_config_shared(config_path))


DEFAULT_BATCH_WRITE_INTERVAL = 2


def get_batch_write_interval(config: dict | None = None) -> int:
    """回傳 lm_translator.batch_write_interval 的實際生效值（至少為 1）。

    執行時寫入頻率與 UI 顯示共用這個函式，確保兩者一致：
    缺漏 / 無法轉成整數 → 預設 2；小於 1 → 1。
    """
    cfg = load_config() if config is None else config
    raw = (cfg.get("lm_translator") or {}).get(
        "batch_write_interval", DEFAULT_BATCH_WRITE_INTERVAL
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_BATCH_WRITE_INTERVAL


def _load_config_uncached(resolved_config_path: Path) -> tuple[dict, bool]:
    """實際讀檔與合併；回傳 (config, 是否可快取)。"""
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
        except (OSError, json.JSONDecodeError) as e:
            log.error(
                "讀取設定檔 %s 失敗: %s，將使用預設設定。", resolved_config_path, e
            )
            return base, False

    # Merge: user (Layer 1) > example (Layer 2) > default (Layer 3)
    # 全部用 deep_merge 一次搞定，確保 config.example.json 新增的 top-level key
    # 也會被正確合併進來，不只限於 DEFAULT_CONFIG 定義的 keys。
    config = deep_merge(base, user_config)

    # lm_translator.models 不允許 deep merge（視為使用者資料，完全替換）
    if "models" in user_config.get("lm_translator", {}):
        config["lm_translator"]["models"] = user_config["lm_translator"]["models"]

    # schema 標示為 sensitive 的設定值（目前是 API 金鑰）登錄為「已知機密」，
    # 之後任何輸出出口都會遮蔽（#125）；佔位字串不登錄
    register_secrets(_sensitive_values(config))

    # ATK-C-2: 對最終結果做驗證
    _validate_lm_translator_config(config["lm_translator"])
    if isinstance(config.get("translator"), dict):
        _validate_translator_config(config["translator"])
    return config, True


# 同步時不寫入使用者 config.json 的路徑：
# - lm_translator.keys：範本是佔位字串，不是使用者的金鑰，不能寫進使用者的設定檔。
# - lm_translator.models：使用者自訂的名單，缺少的模型是使用者刻意移除，不能補回去。
_SYNC_SKIP_PATHS = frozenset({"lm_translator.keys", "lm_translator.models"})


def _collect_missing_keys(
    template: dict, user: dict, prefix: str = ""
) -> list[tuple[str, dict, str, object]]:
    """比對範本與使用者設定，回傳缺少的 (路徑, 使用者端的父 dict, key, 要補的值)。"""
    missing: list[tuple[str, dict, str, object]] = []
    for key, default_value in template.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if path in _SYNC_SKIP_PATHS or path in DEPRECATED_CONFIG_KEYS:
            continue
        if key not in user:
            missing.append((path, user, key, copy.deepcopy(default_value)))
        elif isinstance(default_value, dict) and isinstance(user[key], dict):
            missing.extend(_collect_missing_keys(default_value, user[key], path))
        # 使用者值型別與範本不同、或是 list：一律維持使用者的值
    return missing


def sync_missing_config_keys(
    config_path: str | os.PathLike | None = None,
) -> list[str]:
    """把範本（DEFAULT_CONFIG + config.example.json）新增的設定寫進使用者的 config.json。

    讀取時三層合併本來就會讓新設定生效；這個函式讓新欄位**實際出現在使用者的檔案裡**，
    使用者看得到也改得到。規則：

    - 只補「缺少的 key」，**絕不改動使用者已有的值**（包含 ``false``、``0``、空字串、``null``）。
    - config.json 不存在、讀不到或不是 JSON object 時不做任何事（新安裝仍由合併機制提供預設值）。
    - list 不碰；使用者值與範本型別不同時維持使用者的值。
    - ``lm_translator.keys`` 與 ``lm_translator.models`` 不補（見 ``_SYNC_SKIP_PATHS``）；
      已標示為 deprecated 的欄位不補。
    - 有變更時先留一份 ``<檔名>.pre-sync.bak``，再用 ``save_config`` 原子寫入。
    - 沒有缺少的欄位時不寫檔；重複執行是冪等的。

    回傳：實際補上的設定路徑（例如 ``["lm_translator.max_input_token_budget"]``）。
    """
    resolved = resolve_project_path(config_path or CONFIG_PATH)
    if not resolved.is_file():
        return []
    try:
        with resolved.open("r", encoding="utf-8") as f:
            user_config = json.load(f)
    except (OSError, ValueError):
        log.warning("同步設定欄位：無法讀取 %s，略過", resolved, exc_info=True)
        return []
    if not isinstance(user_config, dict):
        return []

    template = deep_merge(copy.deepcopy(DEFAULT_CONFIG), load_config_example() or {})
    missing = _collect_missing_keys(template, user_config)
    if not missing:
        return []

    backup = resolved.with_name(resolved.name + ".pre-sync.bak")
    try:
        shutil.copy2(resolved, backup)
    except OSError:
        # 沒有備份就不動使用者的檔案
        log.warning("同步設定欄位：無法建立備份 %s，略過", backup, exc_info=True)
        return []
    for _path, parent, key, value in missing:
        parent[key] = value
    if not save_config(user_config, resolved):
        log.error("同步設定欄位：寫入失敗，原檔保持不變（備份在 %s）", backup)
        return []
    added = [path for path, *_ in missing]
    log.info("已把新設定欄位補進 %s：%s", resolved, ", ".join(added))
    return added


def save_config(config, config_path: str | os.PathLike | None = None) -> bool:
    """以同目錄暫存檔與原子替換儲存設定。

    替換前的序列化、flush、fsync 或 ``os.replace`` 失敗時，既有設定檔
    保持不變。替換後仍會讀回並解析；若讀回失敗則回傳 ``False``，但不
    盲目回滾，避免覆蓋另一個合法 writer 在提交點後完成的更新。

    Args:
        config: 要寫入的 JSON object。
        config_path: 目標設定檔；省略時使用 ``CONFIG_PATH``。

    Returns:
        寫入且讀回驗證成功時為 ``True``，否則為 ``False``。
    """
    resolved_config_path = resolve_project_path(config_path or CONFIG_PATH)
    # 同一秒內連續寫入時 mtime 可能不變，直接清掉快取
    clear_config_cache()
    temp_path: Path | None = None
    try:
        resolved_config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=resolved_config_path.parent,
            prefix=f".{resolved_config_path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())

        # 提交前先驗證暫存內容，避免發布不可解析的 JSON。
        with temp_path.open("r", encoding="utf-8") as f:
            staged_data = json.load(f)
        json.dumps(staged_data, sort_keys=True)

        os.replace(temp_path, resolved_config_path)
        temp_path = None
        fsync_directory(resolved_config_path.parent)

        with resolved_config_path.open("r", encoding="utf-8") as f:
            written_data = json.load(f)

        # 能 dump 代表結構是乾淨的
        json.dumps(written_data, sort_keys=True)

        clear_config_cache()
        logging.info(f"設定已成功儲存並驗證至 {resolved_config_path}")  # noqa: LOG015
        return True

    except Exception as e:  # noqa: BLE001
        logging.error(f"錯誤：儲存或驗證設定檔失敗: {e!r}")  # noqa: LOG015
        return False
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                log.warning("無法清理設定暫存檔: %s", temp_path)


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
    today = datetime.now().astimezone().strftime("%Y%m%d")  # 本地日期
    log_folder = resolved_log_dir / today
    log_folder.mkdir(parents=True, exist_ok=True)
    log_file = log_folder / "app.log"

    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    # 所有日誌輸出（含 traceback）都經過遮蔽，避免金鑰出現在日誌檔或終端機（#125）
    for handler in handlers:
        handler.setFormatter(RedactingFormatter(log_format))

    # 每筆記錄在寫 log 的執行緒帶上任務標籤，app.log 才分得出同時執行的各任務（見 ui_mirror）
    install_task_record_factory()
    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)
    logging.info("日誌系統已成功設定。")  # noqa: LOG015


def get_models_config(cfg: dict) -> dict[str, dict]:
    """
    安全取得 models 設定
    - 確保一定回傳 dict[str, dict]
    - 外部亂寫 list / str 都會被忽略
    """
    lm_cfg = cfg.get("lm_translator", {})
    models = lm_cfg.get("models", {})

    if not isinstance(models, dict):
        logging.warning("models 設定型別錯誤，已忽略（需為 dict）")  # noqa: LOG015
        return {}

    safe_models: dict[str, dict] = {}

    for model_name, model_cfg in models.items():
        if not isinstance(model_name, str):
            continue
        if not isinstance(model_cfg, dict):
            continue

        safe_model = {"enabled": bool(model_cfg.get("enabled", False))}
        model_cap = model_cfg.get("max_output_tokens")
        if (
            model_cap is not None
            and isinstance(model_cap, int)
            and not isinstance(model_cap, bool)
        ):
            safe_model["max_output_tokens"] = model_cap
        safe_models[model_name] = safe_model

    return safe_models


class ConfigValidationError(ValueError):
    """Config 欄位驗證失敗時拋出。"""


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
        logging.warning(  # noqa: LOG015
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
        if (
            key.startswith("initial_batch_size_")
            and value is not None
            and not isinstance(value, int)
        ):
            raise ConfigValidationError(
                f"lm_translator.{key} 必須為 int，"
                f"目前為 {type(value).__name__}：'{value}'"
            )

    # 3. parallel_execution_workers 必須是 int > 0
    workers = lm.get("parallel_execution_workers")
    if workers is not None and (not isinstance(workers, int) or workers <= 0):
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
            f"lm_translator.models 必須為 dict，目前為 {type(models_val).__name__}"
        )
    if isinstance(models_val, dict):
        for model_name, model_cfg in models_val.items():
            if not isinstance(model_name, str) or not isinstance(model_cfg, dict):
                raise ConfigValidationError(
                    "lm_translator.models 必須是 model name -> object 的 mapping"
                )
            model_cap = model_cfg.get("max_output_tokens")
            if model_cap is not None and (
                isinstance(model_cap, bool)
                or not isinstance(model_cap, int)
                or model_cap < 0
            ):
                raise ConfigValidationError(
                    f"lm_translator.models.{model_name}.max_output_tokens "
                    f"必須為非負整數或 null，目前為 {model_cap!r}"
                )

    # 6. token 預算切批設定（issue #108）
    _validate_token_budget_config(lm)

    # 7. API Key 失敗冷卻（issue #113）：數字且 >= 0
    cooldown = lm.get("key_failure_cooldown_sec")
    if cooldown is not None and (
        isinstance(cooldown, bool)
        or not isinstance(cooldown, (int, float))
        or cooldown < 0
    ):
        raise ConfigValidationError(
            f"lm_translator.key_failure_cooldown_sec 必須為 >= 0 的數字（0 = 不記憶），"
            f"目前為 {type(cooldown).__name__}：'{cooldown}'"
        )


def _validate_token_budget_config(lm: dict) -> None:
    """驗證 token 預算相關欄位（型別與範圍）；不合法時拋出 ConfigValidationError。"""

    def _typed(key: str, kinds: tuple, label: str):
        value = lm.get(key)
        if value is None:
            return None
        # bool 是 int 的子類別，但不是合法數字
        if isinstance(value, bool) or not isinstance(value, kinds):
            raise ConfigValidationError(
                f"lm_translator.{key} 必須為{label}，"
                f"目前為 {type(value).__name__}：'{value}'"
            )
        return value

    enabled = lm.get("token_budget_enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ConfigValidationError(
            f"lm_translator.token_budget_enabled 必須為 true/false，"
            f"目前為 {type(enabled).__name__}：'{enabled}'"
        )

    for key in ("max_output_token_budget", "max_input_token_budget"):
        value = _typed(key, (int,), "正整數")
        if value is not None and value <= 0:
            raise ConfigValidationError(
                f"lm_translator.{key} 必須為正整數，目前為 {value}"
            )

    out_tokens = _typed("max_output_tokens", (int,), "整數（0 = 不設定）")
    if out_tokens is not None and out_tokens < 0:
        raise ConfigValidationError(
            f"lm_translator.max_output_tokens 不可為負數，目前為 {out_tokens}"
        )

    factor = _typed("output_token_factor", (int, float), "數字")
    if factor is not None and not (0.3 <= factor <= 6.0):
        raise ConfigValidationError(
            f"lm_translator.output_token_factor 必須在 0.3~6.0 範圍內，目前為 {factor}"
        )

    min_scale = _typed("budget_min_scale", (int, float), "數字")
    if min_scale is not None and not (0.0 < min_scale <= 1.0):
        raise ConfigValidationError(
            f"lm_translator.budget_min_scale 必須大於 0 且不超過 1，目前為 {min_scale}"
        )

    recover_after = _typed("budget_recover_after", (int,), "正整數")
    if recover_after is not None and recover_after < 1:
        raise ConfigValidationError(
            f"lm_translator.budget_recover_after 必須為正整數，目前為 {recover_after}"
        )

    recover_factor = _typed("budget_recover_factor", (int, float), "數字")
    if recover_factor is not None and not (1.0 < recover_factor <= 4.0):
        raise ConfigValidationError(
            f"lm_translator.budget_recover_factor 必須大於 1 且不超過 4，目前為 {recover_factor}"
        )

    budget = lm.get("max_output_token_budget")
    if (
        isinstance(budget, int)
        and isinstance(out_tokens, int)
        and out_tokens > 0
        and budget > out_tokens
    ):
        # 不阻擋啟動：預算超過 API 上限只是會讓單批更容易被截斷，屬於設定不合理
        logging.warning(  # noqa: LOG015
            f"lm_translator.max_output_token_budget ({budget}) 大於 max_output_tokens "
            f"({out_tokens})：單批預期輸出可能超過 API 上限，建議調低預算。"
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
    """遞迴合併兩個 dict，override 的值優先（回傳新 dict）。"""
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
