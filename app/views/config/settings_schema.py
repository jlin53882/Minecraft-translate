"""設定頁的 schema：設定頁的唯一來源（#134）。

設定頁的控制項、版面、載入、儲存、說明文字與套用時機，都由這份資料產生
（見 ``settings_form.py`` 與 ``config_actions.py``）。新增一個一般設定只需要：

1. 在 ``translation_tool/utils/config_manager.py`` 的 ``DEFAULT_CONFIG`` 加預設值；
2. 在下方 ``SETTINGS`` 加一個 ``Setting``（指定 ``page`` 與 ``card``）。

沒有出現在 ``LAYOUT`` 的設定會自動接在所屬卡片最後面，不需要再手寫控制項或轉換程式。
需要特殊排版時才在 ``LAYOUT`` 指定位置。測試（``tests/test_config_settings_coverage.py``）
會檢查：每個 ``DEFAULT_CONFIG`` 葉節點都有對應的 ``Setting``、型別與預設值相符、
版面只引用存在的設定且沒有重複。

預設值不重複存放在這裡：``DEFAULT_CONFIG`` 仍是預設值的唯一來源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 設定項目
# ---------------------------------------------------------------------------

# kind:
#   str      單行文字（直接存字串）
#   int      整數（空白依 ``blank`` 處理）
#   float    浮點數
#   bool     勾選
#   text     多行文字（提示詞）
#   lines    多行文字 ↔ list[str]（每行一個項目，去除空白行）
#   choice   下拉選單（需 ``choices``）
#   custom   由設定頁專用元件處理（API 金鑰列、模型列），不由 schema 產生控制項
#   none     不在設定頁編輯（需 ``reason``）


@dataclass(frozen=True)
class Setting:
    path: str
    kind: str
    label: str = ""
    page: str | None = None
    card: str | None = None
    help: str = ""
    weight: int = 1  # 同一列內的欄寬比例
    minimum: float | None = None  # 數值下限（儲存時夾住）
    blank: str = "default"  # 數值欄位留空：default=使用預設值 / zero=0 / error=報錯
    choices: tuple[str, ...] = ()
    validator: str | None = None  # VALIDATORS 內的名稱；儲存前驗證並可正規化
    label_template: str | None = None  # 載入時用目前值組出標籤，{value} 與 refs
    label_refs: tuple[tuple[str, str], ...] = ()  # (模板內名稱, 設定路徑)
    reason: str = ""  # kind="none" 時說明為何不在設定頁


NAV_PAGES: tuple[dict[str, str], ...] = (
    {"id": "general", "label": "一般設定", "icon": "SETTINGS"},
    {"id": "api_models", "label": "API & 模型設定", "icon": "KEY"},
    {"id": "translation_behavior", "label": "翻譯行為設定", "icon": "TRANSLATE"},
    {"id": "merger", "label": "語言合併器設定", "icon": "MERGE_TYPE"},
    {"id": "prompts", "label": "提示詞管理", "icon": "MESSAGE"},
    {"id": "species_lookup", "label": "學名查詢管理", "icon": "SEARCH"},
    {"id": "batch_limits", "label": "批次與限制", "icon": "DEVELOPER_BOARD"},
    {"id": "extractor", "label": "Jar 提取設定", "icon": "FOLDER_OPEN"},
)

C_LOGGING = "日誌設定 (Logging)"
C_TRANSLATOR = "翻譯與處理設定 (Translator)"
C_BUNDLER = "成品打包器 (Output Bundler)"
C_KEYS = "API 金鑰設定"
C_MODELS = "模型設定"
C_LM_BASIC = "基本設定"
C_LM_FILTER = "過濾條件與目錄"
C_PROMPTS = "提示詞 (System Prompts)"
C_SPECIES = "學名查詢設定 (Species Cache)"
C_BATCH = "批次大小與限制"
C_MERGER = "語言合併器設定 (Lang Merger)"
C_EXTRACTOR = "JAR 輸出資料夾命名"

_FOLDER_HELP = "未填入輸出路徑時自動帶入此名稱"
_BATCH_HELP = "用於：批次翻譯請求"

SETTINGS: tuple[Setting, ...] = (
    # --- 一般設定：日誌 ---------------------------------------------------
    Setting(
        "logging.log_level",
        "choice",
        "日誌等級",
        "general",
        C_LOGGING,
        "用於：logging module",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    Setting(
        "logging.log_dir",
        "str",
        "日誌資料夾名稱",
        "general",
        C_LOGGING,
        "用於：logging module",
    ),
    Setting(
        "logging.log_format",
        "str",
        "日誌格式",
        "general",
        C_LOGGING,
        "使用 Python logging 格式欄位",
        validator="log_format",
    ),
    # --- 一般設定：翻譯與處理 ---------------------------------------------
    Setting(
        "translator.output_dir_name",
        "str",
        "主要輸出資料夾名稱",
        "general",
        C_TRANSLATOR,
        "用於：翻譯結果輸出",
    ),
    Setting(
        "ftb_translator.output_dir_name",
        "str",
        "FTB 任務輸出資料夾名稱",
        "general",
        C_TRANSLATOR,
        "用於：FTB任務翻譯輸出",
    ),
    Setting(
        "translator.replace_rules_path",
        "str",
        "替換規則檔案名稱",
        "general",
        C_TRANSLATOR,
        "用於：replace_rules_loader",
    ),
    Setting(
        "translator.cache_directory",
        "str",
        "快取資料夾名稱",
        "general",
        C_TRANSLATOR,
        "用於：翻譯快取系統",
    ),
    Setting(
        "translator.parallel_execution_workers",
        "int",
        "檔案處理多執行緒數量",
        "general",
        C_TRANSLATOR,
        "用於：平行執行器",
        blank="error",
    ),
    Setting(
        "translator.enable_cache_saving",
        "bool",
        "啟用通用翻譯快取",
        "general",
        C_TRANSLATOR,
    ),
    Setting(
        "translator.custom_translator_folder",
        "str",
        "自訂翻譯資料夾",
        "general",
        C_TRANSLATOR,
        "下次 FTB 任務讀取；相對路徑以專案根目錄為基準",
    ),
    # --- 一般設定：打包器 ---------------------------------------------------
    Setting(
        "output_bundler.output_zip_name",
        "str",
        "最終打包 ZIP 檔名",
        "general",
        C_BUNDLER,
        "用於：BundlerView自動帶入",
    ),
    # --- API & 模型（專用元件）----------------------------------------------
    Setting("lm_translator.keys", "custom", "API 金鑰", "api_models", C_KEYS),
    Setting("lm_translator.models", "custom", "模型清單", "api_models", C_MODELS),
    # --- 翻譯行為 -----------------------------------------------------------
    Setting(
        "lm_translator.temperature",
        "float",
        "模型溫度 (Temperature)",
        "translation_behavior",
        C_LM_BASIC,
        "用於：LM翻譯請求",
        blank="error",
    ),
    Setting(
        "lm_translator.rate_limit.timeout",
        "int",
        "API 請求 Timeout",
        "translation_behavior",
        C_LM_BASIC,
        "用於：API超時控制",
        blank="error",
    ),
    Setting(
        "lm_translator.rate_limit.sleep_seconds_between_batches",
        "float",
        "批次間延遲 (秒)",
        "translation_behavior",
        C_LM_BASIC,
        "用於：翻譯批次間延遲",
        blank="error",
    ),
    Setting(
        "lm_translator.lm_translate_folder_name",
        "str",
        "LM 翻譯輸出資料夾",
        "translation_behavior",
        C_LM_BASIC,
        "用於：翻譯結果輸出",
        weight=2,
    ),
    Setting(
        "lm_translator.translator.skip_terms",
        "lines",
        "略過翻譯 (Skip Terms)",
        "translation_behavior",
        C_LM_FILTER,
        "用於：翻譯時略過含關鍵字的項目",
    ),
    Setting(
        "lm_translator.translator.translatable_keywords",
        "lines",
        "可翻譯欄位 (Keywords)",
        "translation_behavior",
        C_LM_FILTER,
        "用於：判斷哪些JSON欄位需翻譯",
    ),
    Setting(
        "lm_translator.patchouli.dir_names",
        "lines",
        "Patchouli 資料夾",
        "translation_behavior",
        C_LM_FILTER,
        "用於：find_patchouli_json 掃描目錄",
    ),
    Setting(
        "lm_translator.translator.short_text_skip_len",
        "int",
        "短字串略過長度",
        "translation_behavior",
        C_LM_FILTER,
        "lang 值 ≤ 此長度且無空白時不翻譯（0 = 不略過，例如 Axe、Ore 也會翻）",
        weight=2,
        minimum=0,
        blank="zero",
    ),
    # --- 提示詞 -------------------------------------------------------------
    Setting(
        "lm_translator.patchouli_system_prompt",
        "text",
        "Patchouli 提示詞 (System Prompt)",
        "prompts",
        C_PROMPTS,
        "用於：Patchouli翻譯請求",
    ),
    Setting(
        "lm_translator.lang_system_prompt",
        "text",
        "Lang 提示詞 (System Prompt)",
        "prompts",
        C_PROMPTS,
        "用於：Lang檔案翻譯請求",
    ),
    # --- 學名查詢 -----------------------------------------------------------
    Setting(
        "species_cache.cache_directory",
        "str",
        "學名快取資料夾",
        "species_lookup",
        C_SPECIES,
        "用於：學名查詢系統",
    ),
    Setting(
        "species_cache.cache_filename",
        "str",
        "學名存放檔案名稱",
        "species_lookup",
        C_SPECIES,
        "用於：學名TSV快取",
    ),
    Setting(
        "species_cache.wikipedia_language",
        "str",
        "Wiki 查詢語言",
        "species_lookup",
        C_SPECIES,
        "用於：維基百科API",
    ),
    Setting(
        "species_cache.wikipedia_rate_limit_delay",
        "float",
        "查詢延遲(秒)",
        "species_lookup",
        C_SPECIES,
        "用於：API速率限制",
        blank="error",
    ),
    # --- 批次與限制 ---------------------------------------------------------
    Setting(
        "lm_translator.initial_batch_size_patchouli",
        "int",
        "Patchouli 請求大小",
        "batch_limits",
        C_BATCH,
        _BATCH_HELP,
    ),
    Setting(
        "lm_translator.initial_batch_size_lang",
        "int",
        "Lang 請求大小",
        "batch_limits",
        C_BATCH,
        _BATCH_HELP,
    ),
    Setting(
        "lm_translator.initial_batch_size_ftb",
        "int",
        "FTB Quests 請求大小",
        "batch_limits",
        C_BATCH,
        _BATCH_HELP,
    ),
    Setting(
        "lm_translator.initial_batch_size_kubejs",
        "int",
        "KubeJS 請求大小",
        "batch_limits",
        C_BATCH,
        _BATCH_HELP,
    ),
    Setting(
        "lm_translator.initial_batch_size_md",
        "int",
        "MD 請求大小",
        "batch_limits",
        C_BATCH,
        _BATCH_HELP,
    ),
    Setting(
        "lm_translator.min_batch_size",
        "int",
        "最小錯誤請求大小",
        "batch_limits",
        C_BATCH,
        "用於：錯誤時批次縮小",
    ),
    Setting(
        "lm_translator.batch_shrink_factor",
        "float",
        "錯誤縮小比例",
        "batch_limits",
        C_BATCH,
        "用於：批次失敗時縮小率",
    ),
    Setting(
        "lm_translator.rpm_cooldown_sec",
        "float",
        "每批翻譯後等待秒數",
        "batch_limits",
        C_BATCH,
        "0 = 不等待；免費層常遇 429 時可調高",
        weight=2,
        minimum=0.0,
        blank="zero",
    ),
    Setting(
        "lm_translator.max_output_tokens",
        "int",
        "全域最大輸出 Tokens",
        "batch_limits",
        C_BATCH,
        weight=2,
        blank="zero",
    ),
    Setting(
        "lm_translator.key_failure_cooldown_sec",
        "float",
        "API Key 失敗冷卻秒數",
        "batch_limits",
        C_BATCH,
        weight=2,
        minimum=0.0,
        blank="zero",
    ),
    Setting(
        "lm_translator.max_output_token_budget",
        "int",
        "單批輸出預算",
        "batch_limits",
        C_BATCH,
        blank="zero",
    ),
    Setting(
        "lm_translator.max_input_token_budget",
        "int",
        "單批輸入預算",
        "batch_limits",
        C_BATCH,
        blank="zero",
    ),
    Setting(
        "lm_translator.token_budget_enabled",
        "bool",
        "啟用 Token 預算切批",
        "batch_limits",
        C_BATCH,
        "關閉後回到固定批次大小",
    ),
    Setting(
        "lm_translator.output_token_factor",
        "float",
        "輸出／輸入 Token 係數",
        "batch_limits",
        C_BATCH,
        "預期輸出是輸入的幾倍",
    ),
    Setting(
        "lm_translator.budget_min_scale",
        "float",
        "預算最小縮放",
        "batch_limits",
        C_BATCH,
        "撞牆後輸出預算最多縮到設定值的幾倍",
    ),
    Setting(
        "lm_translator.budget_recover_after",
        "int",
        "回升前連續成功批次",
        "batch_limits",
        C_BATCH,
        "連續成功幾批後開始回升預算",
    ),
    Setting(
        "lm_translator.budget_recover_factor",
        "float",
        "預算回升倍率",
        "batch_limits",
        C_BATCH,
        "每次回升的倍率",
    ),
    Setting(
        "lm_translator.batch_write_interval",
        "int",
        "每 N 批寫一次快取",
        "batch_limits",
        C_BATCH,
        "太大會讓單次寫入超過分片上限",
    ),
    # --- 語言合併器 ---------------------------------------------------------
    Setting(
        "lang_merger.pending_folder_name",
        "str",
        "待翻譯資料夾名稱",
        "merger",
        C_MERGER,
        "用於：語言合併器",
        label_template="待翻譯資料夾名稱（目前：{value}）",
    ),
    Setting(
        "lang_merger.pending_organized_folder_name",
        "str",
        "待翻譯整理資料夾名稱",
        "merger",
        C_MERGER,
        "用於：lang_merger",
        label_template="整理資料夾名稱（目前：{value}）",
    ),
    Setting(
        "lang_merger.filtered_pending_min_count",
        "int",
        "待翻譯整理json筆數最小出現次數",
        "merger",
        C_MERGER,
        "用於：整理分類邏輯",
        blank="error",
        label_template="「{organized}」key最小出現次數（目前：{value}）",
        label_refs=(("organized", "lang_merger.pending_organized_folder_name"),),
    ),
    Setting(
        "lang_merger.quarantine_folder_name",
        "str",
        "語言合併器格式問題隔離資料夾名稱",
        "merger",
        C_MERGER,
        "用於：格式錯誤隔離",
    ),
    Setting(
        "lang_merger.zh_en_letter_threshold",
        "int",
        "zh 英文含量閾值",
        "merger",
        C_MERGER,
        "超過此數值判定為英文，空白用預設值 2",
    ),
    Setting(
        "lang_merger.patchouli_skip_en_us_when_zh_cn_exists",
        "bool",
        "優先使用已有繁中，無則信任簡中（跳過英文）",
        "merger",
        C_MERGER,
    ),
    Setting(
        "lang_merger.patchouli_effective_translation_threshold",
        "float",
        "en_us 跳過門檻",
        "merger",
        C_MERGER,
        "有效翻譯比例閾值 0.0~1.0，空白用預設值 0.5",
    ),
    Setting(
        "lang_merger.enable_extracted_to_assets_merge",
        "bool",
        "合併 XX_extracted → assets/(merge 階段2)",
        "merger",
        C_MERGER,
    ),
    # --- Jar 提取 -----------------------------------------------------------
    Setting(
        "extractor.output_folder_names.lang_extract",
        "str",
        "Lang 提取輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        _FOLDER_HELP,
    ),
    Setting(
        "extractor.output_folder_names.book_extract",
        "str",
        "Book 提取輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        _FOLDER_HELP,
    ),
    Setting(
        "extractor.output_folder_names.dual_extract",
        "str",
        "Dual 提取輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        "Lang + Book 同時提取時使用",
    ),
    Setting(
        "extractor.output_folder_names.lang_preview",
        "str",
        "Lang 預覽輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        _FOLDER_HELP,
    ),
    Setting(
        "extractor.output_folder_names.book_preview",
        "str",
        "Book 預覽輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        _FOLDER_HELP,
    ),
    Setting(
        "extractor.output_folder_names.dual_preview",
        "str",
        "Dual 預覽輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        "Lang + Book 同時預覽時使用",
    ),
    Setting(
        "extractor.skip_zh_cn_extract",
        "bool",
        "提取頁預設跳過 zh_cn（可在每次操作前覆寫）",
        "extractor",
        C_EXTRACTOR,
    ),
    # --- 不在設定頁編輯 -----------------------------------------------------
    Setting("ui.theme_mode", "none", reason="由側欄的深淺色切換，不在設定頁"),
    Setting(
        "jar_extractor.lang_codes",
        "none",
        reason="提取流程固定的語系清單，由提取頁處理",
    ),
    Setting(
        "lang_merger.process_zh_cn_files",
        "none",
        reason="由合併頁的開關覆寫（merge_view）",
    ),
    Setting(
        "lang_merger.skip_zh_cn_when_only_process_lang",
        "none",
        reason="由合併流程內部使用，尚無設定頁需求",
    ),
    Setting(
        "extractor.target_language",
        "none",
        reason="歷史相容欄位；追查不到正式 caller，不宣稱可調整",
    ),
    Setting(
        "translator.cjk_ratio_threshold",
        "none",
        reason="歷史相容欄位；追查不到 caller，不臆造語意",
    ),
)

SETTINGS_BY_PATH: dict[str, Setting] = {s.path: s for s in SETTINGS}

# ---------------------------------------------------------------------------
# 版面
# ---------------------------------------------------------------------------
# 沒有列在 LAYOUT 的設定，會自動以單欄接在所屬 page/card 的最後面。


@dataclass(frozen=True)
class F:
    """單一控制項，直接放進卡片。"""

    path: str


@dataclass(frozen=True)
class R:
    """一列多欄（欄寬依 Setting.weight）；``pad`` > 0 時右側補一個同寬的空欄。"""

    paths: tuple[str, ...]
    pad: int = 0

    def __init__(self, *paths: str, pad: int = 0):
        object.__setattr__(self, "paths", tuple(paths))
        object.__setattr__(self, "pad", pad)


@dataclass(frozen=True)
class Side:
    """固定高度、並排的多個控制項，中間加垂直分隔線。"""

    paths: tuple[str, ...]
    height: int
    spacing: int = 10

    def __init__(self, *paths: str, height: int, spacing: int = 10):
        object.__setattr__(self, "paths", tuple(paths))
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "spacing", spacing)


@dataclass(frozen=True)
class Note:
    text: str


@dataclass(frozen=True)
class Head:
    text: str


@dataclass(frozen=True)
class Gap:
    height: int = 8


@dataclass(frozen=True)
class Div:
    pass


@dataclass(frozen=True)
class Labeled:
    """上方標題、中間控制項、下方說明的一欄。"""

    path: str
    title: str
    note: str = ""


@dataclass(frozen=True)
class Cols:
    """並排的多個 ``Labeled`` 欄。"""

    items: tuple[Labeled, ...]
    spacing: int = 8

    def __init__(self, *items: Labeled, spacing: int = 8):
        object.__setattr__(self, "items", tuple(items))
        object.__setattr__(self, "spacing", spacing)


@dataclass(frozen=True)
class Custom:
    """由設定頁專用元件處理的區塊（``keys`` / ``models``）。"""

    name: str


@dataclass(frozen=True)
class Card:
    title: str
    blocks: tuple[Any, ...] = field(default_factory=tuple)


def _extractor_rows() -> tuple[Any, ...]:
    return (
        R(
            "extractor.output_folder_names.lang_extract",
            "extractor.output_folder_names.book_extract",
            "extractor.output_folder_names.dual_extract",
        ),
        R(
            "extractor.output_folder_names.lang_preview",
            "extractor.output_folder_names.book_preview",
            "extractor.output_folder_names.dual_preview",
        ),
        Div(),
        F("extractor.skip_zh_cn_extract"),
        Note(
            "extractor.target_language 是歷史設定；目前沒有正式生效語意，"
            "保留舊 config 讀取相容但不再宣稱會影響提取。"
        ),
    )


# page id → 該頁卡片的版面（只需列出有特殊排版的卡片；其餘卡片由 SETTINGS 自動產生）
LAYOUT: dict[str, tuple[Card, ...]] = {
    "general": (
        Card(C_LOGGING),
        Card(
            C_TRANSLATOR,
            (
                F("translator.output_dir_name"),
                F("ftb_translator.output_dir_name"),
                F("translator.replace_rules_path"),
                F("translator.cache_directory"),
                F("translator.parallel_execution_workers"),
                F("translator.enable_cache_saving"),
                F("translator.custom_translator_folder"),
                Note(
                    "translator.cjk_ratio_threshold 已保留供舊設定相容，歷史上沒有實際 caller；"
                    "本頁不提供無效的可調整欄位。"
                ),
            ),
        ),
        Card(C_BUNDLER),
    ),
    "api_models": (
        Card(C_KEYS, (Custom("keys"),)),
        Card(C_MODELS, (Custom("models"),)),
    ),
    "translation_behavior": (
        Card(
            C_LM_BASIC,
            (
                R(
                    "lm_translator.temperature",
                    "lm_translator.rate_limit.timeout",
                    "lm_translator.rate_limit.sleep_seconds_between_batches",
                    "lm_translator.lm_translate_folder_name",
                ),
            ),
        ),
        Card(
            C_LM_FILTER,
            (
                Side(
                    "lm_translator.translator.skip_terms",
                    "lm_translator.translator.translatable_keywords",
                    "lm_translator.patchouli.dir_names",
                    height=200,
                    spacing=5,
                ),
                R("lm_translator.translator.short_text_skip_len", pad=2),
            ),
        ),
    ),
    "prompts": (
        Card(
            C_PROMPTS,
            (
                Side(
                    "lm_translator.patchouli_system_prompt",
                    "lm_translator.lang_system_prompt",
                    height=250,
                ),
            ),
        ),
    ),
    "species_lookup": (Card(C_SPECIES),),
    "batch_limits": (
        Card(
            C_BATCH,
            (
                R(
                    "lm_translator.initial_batch_size_patchouli",
                    "lm_translator.initial_batch_size_lang",
                    "lm_translator.initial_batch_size_ftb",
                ),
                R(
                    "lm_translator.initial_batch_size_kubejs",
                    "lm_translator.initial_batch_size_md",
                    "lm_translator.min_batch_size",
                    "lm_translator.batch_shrink_factor",
                ),
                R(
                    "lm_translator.rpm_cooldown_sec",
                    "lm_translator.max_output_tokens",
                    "lm_translator.key_failure_cooldown_sec",
                ),
                R(
                    "lm_translator.max_output_token_budget",
                    "lm_translator.max_input_token_budget",
                ),
                R(
                    "lm_translator.token_budget_enabled",
                    "lm_translator.output_token_factor",
                    "lm_translator.budget_min_scale",
                ),
                R(
                    "lm_translator.budget_recover_after",
                    "lm_translator.budget_recover_factor",
                    "lm_translator.batch_write_interval",
                ),
            ),
        ),
    ),
    "merger": (
        Card(
            C_MERGER,
            (
                R(
                    "lang_merger.pending_folder_name",
                    "lang_merger.pending_organized_folder_name",
                ),
                R(
                    "lang_merger.filtered_pending_min_count",
                    "lang_merger.quarantine_folder_name",
                ),
                Gap(),
                Head("語系過濾設定"),
                Cols(Labeled("lang_merger.zh_en_letter_threshold", "zh 英文含量閾值")),
                Gap(),
                Head("Patchouli 進階設定"),
                Cols(
                    Labeled(
                        "lang_merger.patchouli_skip_en_us_when_zh_cn_exists",
                        "翻譯來源優先級：繁中 > 簡中(達門檻) > 英文",
                        "內容中日韓文字佔比達此值時視為有效翻譯",
                    ),
                    Labeled(
                        "lang_merger.patchouli_effective_translation_threshold",
                        "en_us 跳過門檻",
                    ),
                ),
                Gap(),
                Head("檔案合併(階段 2)"),
                Cols(
                    Labeled(
                        "lang_merger.enable_extracted_to_assets_merge",
                        "合併 XX_extracted → assets/",
                        "merge 後跑階段 2,把 {XX_extracted}/{modid}/lang/* 補入 assets/{modid}/lang/*",
                    )
                ),
            ),
        ),
    ),
    "extractor": (Card(C_EXTRACTOR, _extractor_rows()),),
}

# 儲存前驗證／正規化（名稱對應 Setting.validator）。實作在 config_actions，避免這裡依賴服務層。
VALIDATOR_NAMES = frozenset({"log_format"})


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------


def ui_settings() -> list[Setting]:
    """所有會在設定頁出現的設定（含專用元件），依 SETTINGS 順序。"""
    return [s for s in SETTINGS if s.kind != "none"]


def editable_settings() -> list[Setting]:
    """由 schema 產生控制項的設定（不含專用元件與不在設定頁的）。"""
    return [s for s in SETTINGS if s.kind not in ("none", "custom")]


def get_path(data: dict, path: str, default: Any = None) -> Any:
    node: Any = data
    for key in path.split("."):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node


def set_path(data: dict, path: str, value: Any) -> None:
    node = data
    keys = path.split(".")
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def resolved_layout() -> dict[str, tuple[Card, ...]]:
    """把 LAYOUT 與 SETTINGS 合併：未被版面引用的設定，自動接在所屬卡片最後面。

    回傳 page id → 卡片清單（保持 NAV_PAGES 的頁面順序；卡片順序為 LAYOUT 先、
    新增的卡片依 SETTINGS 出現順序）。
    """
    placed: set[str] = set()
    for cards in LAYOUT.values():
        for card in cards:
            for block in card.blocks:
                placed.update(_block_paths(block))

    result: dict[str, list[Card]] = {
        page["id"]: list(LAYOUT.get(page["id"], ())) for page in NAV_PAGES
    }
    extra: dict[tuple[str, str], list[Any]] = {}
    for s in editable_settings():
        if s.path in placed or s.page is None or s.card is None:
            continue
        extra.setdefault((s.page, s.card), []).append(F(s.path))
    for (page, title), blocks in extra.items():
        cards = result.setdefault(page, [])
        for i, card in enumerate(cards):
            if card.title == title:
                cards[i] = Card(card.title, (*card.blocks, *blocks))
                break
        else:
            cards.append(Card(title, tuple(blocks)))
    # 只有標題沒有內容的卡片（例如 Card(C_LOGGING)）已在上面補齊；
    return {page: tuple(cards) for page, cards in result.items()}


def _block_paths(block: Any) -> list[str]:
    if isinstance(block, F):
        return [block.path]
    if isinstance(block, (R, Side)):
        return list(block.paths)
    if isinstance(block, Cols):
        return [item.path for item in block.items]
    return []


def layout_paths(layout: dict[str, tuple[Card, ...]]) -> list[str]:
    paths: list[str] = []
    for cards in layout.values():
        for card in cards:
            for block in card.blocks:
                paths.extend(_block_paths(block))
    return paths
