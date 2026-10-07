"""設定的單一 schema（#134）：欄位、型別、預設值、說明、套用時機、是否敏感、驗證，全部在這裡。

這是**預設設定的唯一來源**：``config_manager.DEFAULT_CONFIG`` 由這份 schema 建出
（``build_default_config()``）；設定頁（``app/views/config``）、套用時機提示
（``app/config_apply.py``）、機密遮蔽（登錄 ``sensitive`` 的設定值）都從這裡衍生。

新增一個一般設定只需要在 ``SETTINGS`` 加一個 ``Setting``：

- ``default``：預設值（會自動進入 ``DEFAULT_CONFIG``）；
- ``kind`` / ``label`` / ``help`` / ``page`` / ``card``：設定頁自動產生控制項並放進所屬卡片；
- ``timing`` / ``timing_note``：套用時機（不寫就是「下次任務」）；
- ``sensitive``：True 時，這個設定的值會被登錄為已知機密並在所有輸出出口遮蔽；
- ``validator`` / ``minimum`` / ``blank``：儲存時的驗證與正規化。

此模組不依賴 Flet 或 ``app``：引擎層（``translation_tool``）與設定頁共用同一份資料。
設定頁的版面（``LAYOUT``）與導覽頁則留在 ``app/views/config/settings_schema.py``。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

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

TIMINGS = frozenset(
    {"immediate", "next_request", "next_batch", "next_task", "when_idle", "restart"}
)


@dataclass(frozen=True)
class Setting:
    path: str
    kind: str
    label: str = ""
    page: str | None = None
    card: str | None = None
    help: str = ""
    default: Any = None  # 預設值；DEFAULT_CONFIG 由此建出
    weight: int = 1  # 同一列內的欄寬比例
    minimum: float | None = None  # 數值下限（儲存時夾住）
    blank: str = "default"  # 數值欄位留空：default=使用預設值 / zero=0 / error=報錯
    choices: tuple[str, ...] = ()
    validator: str | None = None  # 儲存前驗證並可正規化（名稱見 app 端的 VALIDATORS）
    label_template: str | None = None  # 載入時用目前值組出標籤，{value} 與 refs
    label_refs: tuple[tuple[str, str], ...] = ()  # (模板內名稱, 設定路徑)
    reason: str = ""  # kind="none" 時說明為何不在設定頁
    timing: str = "next_task"  # 套用時機（見 app/config_apply.py）
    timing_note: str = ""  # 套用時機的說明文字；空白時使用預設說明
    sensitive: bool = False  # True：值是機密，會被登錄並在所有輸出出口遮蔽


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
C_TDB = "Mod 資料庫 (Translation DB)"

SETTINGS: tuple[Setting, ...] = (
    Setting(
        "logging.log_level",
        "choice",
        "日誌等級",
        "general",
        C_LOGGING,
        "用於：logging module",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        timing="immediate",
        timing_note="存檔後立即套用到執行中的日誌，不必重啟。",
    ),
    Setting(
        "logging.log_dir",
        "str",
        "日誌資料夾名稱",
        "general",
        C_LOGGING,
        "用於：logging module",
        default="logs",
        timing="restart",
        timing_note="應用日誌需重啟才換資料夾；錯誤記錄（errors_*.log）下次寫入時即套用。",
    ),
    Setting(
        "logging.log_format",
        "str",
        "日誌格式",
        "general",
        C_LOGGING,
        "使用 Python logging 格式欄位",
        default="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s",
        validator="log_format",
        timing="immediate",
        timing_note="存檔後立即套用到執行中的日誌，不必重啟。",
    ),
    Setting(
        "translator.output_dir_name",
        "str",
        "主要輸出資料夾名稱",
        "general",
        C_TRANSLATOR,
        "用於：翻譯結果輸出",
        default="zh_tw_generated",
    ),
    Setting(
        "ftb_translator.output_dir_name",
        "str",
        "FTB 任務輸出資料夾名稱",
        "general",
        C_TRANSLATOR,
        "用於：FTB任務翻譯輸出",
        default="FTB任務翻譯輸出",
    ),
    Setting(
        "translator.replace_rules_path",
        "str",
        "替換規則檔案名稱",
        "general",
        C_TRANSLATOR,
        "用於：replace_rules_loader",
        default="replace_rules.json",
    ),
    Setting(
        "translator.cache_directory",
        "str",
        "快取資料夾名稱",
        "general",
        C_TRANSLATOR,
        "用於：翻譯快取系統",
        default="快取資料",
        timing="when_idle",
        timing_note="存檔後自動重載快取與搜尋索引；有任務進行中時，等任務結束後才切換，避免新舊資料混用。",
    ),
    Setting(
        "translator.parallel_execution_workers",
        "int",
        "檔案處理多執行緒數量",
        "general",
        C_TRANSLATOR,
        "用於：平行執行器",
        default=4,
        blank="error",
    ),
    Setting(
        "translator.enable_cache_saving",
        "bool",
        "啟用通用翻譯快取",
        "general",
        C_TRANSLATOR,
        "",
        default=True,
    ),
    Setting(
        "translation_db.enabled",
        "bool",
        "翻譯時使用 Mod 資料庫",
        "general",
        C_TDB,
        "機器翻譯先查資料庫（資料庫 → 快取 → AI）；資料庫不存在或未指定版本時自動略過",
        default=True,
    ),
    Setting(
        "translation_db.path",
        "str",
        "資料庫檔案（SQLite）",
        "general",
        C_TDB,
        "預設空白（使用資料目錄內的 mod_translation.db）；第一次建立資料庫時會自動寫入實際路徑。相對路徑以資料目錄為基準",
        default="",
    ),
    Setting(
        "translation_db.version",
        "str",
        "預設目標版本",
        "general",
        C_TDB,
        "翻譯查詢與寫回使用的遊戲版本，例如 1.21.1；機器翻譯頁可個別覆寫",
        default="",
    ),
    Setting(
        "translation_db.cross_version",
        "bool",
        "允許跨版本沿用",
        "general",
        C_TDB,
        "目標版本沒有譯文時，沿用其他版本中原文完全相同的譯文",
        default=True,
    ),
    Setting(
        "translation_db.write_back",
        "bool",
        "翻譯結果寫入資料庫",
        "general",
        C_TDB,
        "只新增、不覆蓋；其他版本中原文相同且沒有譯文的空白也會補上",
        default=True,
    ),
    Setting(
        "translation_db.sync_manual",
        "bool",
        "手動儲存時同步其他版本",
        "general",
        C_TDB,
        "在 Mod 資料庫頁修改譯文時，原文相同的其他版本一併取代（異動記錄可還原）",
        default=True,
    ),
    Setting(
        "translation_db.zip_source",
        "str",
        "翻譯 ZIP 匯入的預設來源標記",
        "general",
        C_TDB,
        "Mod 資料庫「掃描匯入 → 翻譯 ZIP」的「譯文來源標記」預設值。可用名稱：自訂補充、町宮字幕組、i18n 轉換、模組自帶繁中、人工；填錯時使用「自訂補充」",
        default="自訂補充",
    ),
    Setting(
        "translation_db.priority",
        "lines",
        "來源優先順序（每行一個，上方優先）",
        "general",
        C_TDB,
        "已校驗者永遠最優先。內建名稱：人工、町宮字幕組、自訂補充、模組自帶繁中、i18n 轉換、簡中轉繁、AI 機翻；輸入其他名稱會自動新增為自訂來源（儲存後登錄到資料庫，並出現在各來源選單），請確認拼寫",
        default=[
            "人工",
            "町宮字幕組",
            "自訂補充",
            "模組自帶繁中",
            "i18n 轉換",
            "簡中轉繁",
            "AI 機翻",
        ],
        weight=1,
    ),
    Setting(
        "translator.custom_translator_folder",
        "str",
        "自訂翻譯資料夾",
        "general",
        C_TRANSLATOR,
        "下次 FTB 任務讀取；相對路徑以專案根目錄為基準",
        default="custom_translators",
    ),
    Setting(
        "output_bundler.output_zip_name",
        "str",
        "最終打包 ZIP 檔名",
        "general",
        C_BUNDLER,
        "用於：BundlerView自動帶入",
        default="可使用翻譯.zip",
        timing_note="打包時才讀取；打包頁的輸入框提示會在存檔後立即更新。",
    ),
    Setting(
        "lm_translator.keys",
        "custom",
        "API 金鑰",
        "api_models",
        C_KEYS,
        "",
        default=["YOUR_GEMINI_API_KEY_1", "YOUR_GEMINI_API_KEY_2"],
        timing="next_request",
        timing_note="下次 API 請求讀取；不改變已送出的請求。",
        sensitive=True,
    ),
    Setting(
        "lm_translator.models",
        "custom",
        "模型清單",
        "api_models",
        C_MODELS,
        "",
        default={
            "gemini-3.5-flash-lite": {"enabled": True},
            "gemini-3.1-flash-lite": {"enabled": True},
        },
        timing="next_batch",
        timing_note="下一批次讀取；進行中的批次維持原設定。",
    ),
    Setting(
        "lm_translator.temperature",
        "float",
        "模型溫度 (Temperature)",
        "translation_behavior",
        C_LM_BASIC,
        "用於：LM翻譯請求",
        default=0.3,
        blank="error",
        timing="next_batch",
        timing_note="下一批次讀取。",
    ),
    Setting(
        "lm_translator.rate_limit.timeout",
        "int",
        "API 請求 Timeout",
        "translation_behavior",
        C_LM_BASIC,
        "用於：API超時控制",
        default=600,
        blank="error",
        timing="next_request",
        timing_note="下次 API 請求讀取。",
    ),
    Setting(
        "lm_translator.rate_limit.sleep_seconds_between_batches",
        "float",
        "批次間延遲 (秒)",
        "translation_behavior",
        C_LM_BASIC,
        "用於：翻譯批次間延遲",
        default=0.0,
        blank="error",
        timing="next_request",
        timing_note="下次 API 請求讀取。",
    ),
    Setting(
        "lm_translator.lm_translate_folder_name",
        "str",
        "LM 翻譯輸出資料夾",
        "translation_behavior",
        C_LM_BASIC,
        "用於：翻譯結果輸出",
        default="LM翻譯後",
        weight=2,
    ),
    Setting(
        "lm_translator.translator.skip_terms",
        "lines",
        "略過翻譯 (Skip Terms)",
        "translation_behavior",
        C_LM_FILTER,
        "用於：翻譯時略過含關鍵字的項目",
        default=[
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
    ),
    Setting(
        "lm_translator.translator.translatable_keywords",
        "lines",
        "可翻譯欄位 (Keywords)",
        "translation_behavior",
        C_LM_FILTER,
        "用於：判斷哪些JSON欄位需翻譯",
        default=[
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
    ),
    Setting(
        "lm_translator.patchouli.dir_names",
        "lines",
        "Patchouli 資料夾",
        "translation_behavior",
        C_LM_FILTER,
        "用於：find_patchouli_json 掃描目錄",
        default=["patchouli_books", "book", "manual", "guidebook"],
    ),
    Setting(
        "lm_translator.translator.short_text_skip_len",
        "int",
        "短字串略過長度",
        "translation_behavior",
        C_LM_FILTER,
        "lang 值 ≤ 此長度且無空白時不翻譯（0 = 不略過，例如 Axe、Ore 也會翻）",
        default=3,
        weight=2,
        minimum=0,
        blank="zero",
    ),
    Setting(
        "lm_translator.patchouli_system_prompt",
        "text",
        "Patchouli 提示詞 (System Prompt)",
        "prompts",
        C_PROMPTS,
        "用於：Patchouli翻譯請求",
        default=(
            "你是專業的 Minecraft Patchouli 手冊翻譯員。\n"
            "\n"
            "你正在翻譯一個「ID → Value 對照表」。\n"
            "\n"
            "⚠️【極重要規則 — ID 不可變】⚠️\n"
            "- items[].id 是不可變的識別符號\n"
            "- id 不具有任何語意，也不對應任何 JSON 結構\n"
            "- id 只能被視為純文字索引\n"
            "- 絕對禁止：\n"
            "  - 修改、重寫、補零、轉型、排序、重編任何 id\n"
            "  - 新增或刪除任何 id\n"
            "  - 嘗試推測 id 與內容的關聯\n"
            "\n"
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
        timing="next_batch",
        timing_note="下一批次讀取。",
    ),
    Setting(
        "lm_translator.lang_system_prompt",
        "text",
        "Lang 提示詞 (System Prompt)",
        "prompts",
        C_PROMPTS,
        "用於：Lang檔案翻譯請求",
        default=(
            "你正在翻譯 Minecraft 語言檔案（JSON 格式）。\n"
            "\n"
            "你收到的是一個「ID → value 對照表」。\n"
            "\n"
            "⚠️【極重要規則 — ID 不可變】⚠️\n"
            "- items[].id 是唯一識別符號\n"
            "- id 不具有任何語意\n"
            "- 絕對禁止：\n"
            "  - 修改、轉型、補零、重排、推測或重寫任何 id\n"
            "  - 新增或刪除任何 item\n"
            "\n"
            "📌 任務規則：\n"
            "1. 只允許修改 items[].value 的字串內容\n"
            "2. items[].id 必須與輸入完全一字不差\n"
            "3. items 的數量與順序必須與輸入完全一致\n"
            "4. 如果你不確定如何翻譯，請原樣回傳 value\n"
            '5. 回傳必須是合法 JSON，格式必須為 {"items":[{"id":...,"value":...}, ...]}\n'
            "6. 僅翻譯為繁體中文（台灣用語）\n"
            "7. 保留 §, %, {}, $(...) 等所有符號與格式\n"
            "8. 單位（mb、tick 等）請保留原文\n"
            "9. Minecraft 請保持原文\n"
            "10. 每一筆 value 只依該筆原文翻譯\n"
            "11. 只要 value 包含人類語言就必須翻譯\n"
        ),
        timing="next_batch",
        timing_note="下一批次讀取。",
    ),
    Setting(
        "species_cache.cache_directory",
        "str",
        "學名快取資料夾",
        "species_lookup",
        C_SPECIES,
        "用於：學名查詢系統",
        default="學名資料庫",
        timing="restart",
        timing_note="目前模組初始化後不熱切換，需重啟才套用。",
    ),
    Setting(
        "species_cache.cache_filename",
        "str",
        "學名存放檔案名稱",
        "species_lookup",
        C_SPECIES,
        "用於：學名TSV快取",
        default="species_cache.tsv",
        timing="restart",
        timing_note="目前模組初始化後不熱切換，需重啟才套用。",
    ),
    Setting(
        "species_cache.wikipedia_language",
        "str",
        "Wiki 查詢語言",
        "species_lookup",
        C_SPECIES,
        "用於：維基百科API",
        default="zh",
        timing="restart",
        timing_note="目前模組初始化後不熱切換，需重啟才套用。",
    ),
    Setting(
        "species_cache.wikipedia_rate_limit_delay",
        "float",
        "查詢延遲(秒)",
        "species_lookup",
        C_SPECIES,
        "用於：API速率限制",
        default=0.5,
        blank="error",
        timing="restart",
        timing_note="目前模組初始化後不熱切換，需重啟才套用。",
    ),
    Setting(
        "lm_translator.initial_batch_size_patchouli",
        "int",
        "Patchouli 請求大小",
        "batch_limits",
        C_BATCH,
        "用於：批次翻譯請求",
        default=100,
    ),
    Setting(
        "lm_translator.initial_batch_size_lang",
        "int",
        "Lang 請求大小",
        "batch_limits",
        C_BATCH,
        "用於：批次翻譯請求",
        default=300,
    ),
    Setting(
        "lm_translator.initial_batch_size_ftb",
        "int",
        "FTB Quests 請求大小",
        "batch_limits",
        C_BATCH,
        "用於：批次翻譯請求",
        default=200,
    ),
    Setting(
        "lm_translator.initial_batch_size_kubejs",
        "int",
        "KubeJS 請求大小",
        "batch_limits",
        C_BATCH,
        "用於：批次翻譯請求",
        default=200,
    ),
    Setting(
        "lm_translator.initial_batch_size_md",
        "int",
        "MD 請求大小",
        "batch_limits",
        C_BATCH,
        "用於：批次翻譯請求",
        default=100,
    ),
    Setting(
        "lm_translator.min_batch_size",
        "int",
        "最小錯誤請求大小",
        "batch_limits",
        C_BATCH,
        "用於：錯誤時批次縮小",
        default=50,
    ),
    Setting(
        "lm_translator.batch_shrink_factor",
        "float",
        "錯誤縮小比例",
        "batch_limits",
        C_BATCH,
        "用於：批次失敗時縮小率",
        default=0.5,
    ),
    Setting(
        "lm_translator.rpm_cooldown_sec",
        "float",
        "每批翻譯後等待秒數",
        "batch_limits",
        C_BATCH,
        "0 = 不等待；免費層常遇 429 時可調高",
        default=0,
        weight=2,
        minimum=0.0,
        blank="zero",
        timing="next_request",
        timing_note="下次 API 請求讀取。",
    ),
    Setting(
        "lm_translator.max_output_tokens",
        "int",
        "全域最大輸出 Tokens",
        "batch_limits",
        C_BATCH,
        "",
        default=32768,
        weight=2,
        blank="zero",
        timing="next_batch",
        timing_note="下一批次讀取；per-model override 優先於全域值；0 表示不送 maxOutputTokens。",
    ),
    Setting(
        "lm_translator.key_failure_cooldown_sec",
        "float",
        "API Key 無權限(403)冷卻秒數",
        "batch_limits",
        C_BATCH,
        "",
        default=3600,
        weight=2,
        minimum=0.0,
        blank="zero",
        timing="next_request",
        timing_note="下一次 API key 判斷讀取；不改變已送出的請求。",
    ),
    Setting(
        "lm_translator.max_output_token_budget",
        "int",
        "單批輸出預算",
        "batch_limits",
        C_BATCH,
        "",
        default=24000,
        blank="zero",
        timing="next_batch",
        timing_note="下一批次讀取；用於切批估算。",
    ),
    Setting(
        "lm_translator.max_input_token_budget",
        "int",
        "單批輸入預算",
        "batch_limits",
        C_BATCH,
        "",
        default=60000,
        blank="zero",
        timing="next_batch",
        timing_note="下一批次讀取；用於切批估算。",
    ),
    Setting(
        "lm_translator.token_budget_enabled",
        "bool",
        "啟用 Token 預算切批",
        "batch_limits",
        C_BATCH,
        "關閉後回到固定批次大小",
        default=True,
        timing="next_batch",
        timing_note="下一批次讀取。",
    ),
    Setting(
        "lm_translator.output_token_factor",
        "float",
        "輸出／輸入 Token 係數",
        "batch_limits",
        C_BATCH,
        "預期輸出是輸入的幾倍",
        default=1.5,
        timing="next_batch",
        timing_note="下一批次讀取。",
    ),
    Setting(
        "lm_translator.budget_min_scale",
        "float",
        "預算最小縮放",
        "batch_limits",
        C_BATCH,
        "撞牆後輸出預算最多縮到設定值的幾倍",
        default=0.0625,
        timing="next_batch",
        timing_note="下一批次讀取。",
    ),
    Setting(
        "lm_translator.budget_recover_after",
        "int",
        "回升前連續成功批次",
        "batch_limits",
        C_BATCH,
        "連續成功幾批後開始回升預算",
        default=3,
        timing="next_batch",
        timing_note="下一批次讀取。",
    ),
    Setting(
        "lm_translator.budget_recover_factor",
        "float",
        "預算回升倍率",
        "batch_limits",
        C_BATCH,
        "每次回升的倍率",
        default=1.5,
        timing="next_batch",
        timing_note="下一批次讀取。",
    ),
    Setting(
        "lm_translator.batch_write_interval",
        "int",
        "每 N 批寫一次快取",
        "batch_limits",
        C_BATCH,
        "太大會讓單次寫入超過分片上限",
        default=2,
    ),
    Setting(
        "lang_merger.pending_folder_name",
        "str",
        "待翻譯資料夾名稱",
        "merger",
        C_MERGER,
        "用於：語言合併器",
        default="待翻譯",
        label_template="待翻譯資料夾名稱（目前：{value}）",
    ),
    Setting(
        "lang_merger.pending_organized_folder_name",
        "str",
        "待翻譯整理資料夾名稱",
        "merger",
        C_MERGER,
        "用於：lang_merger",
        default="待翻譯整理需翻譯",
        label_template="整理資料夾名稱（目前：{value}）",
    ),
    Setting(
        "lang_merger.filtered_pending_min_count",
        "int",
        "待翻譯整理json筆數最小出現次數",
        "merger",
        C_MERGER,
        "用於：整理分類邏輯",
        default=3,
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
        default="問題檔案skipped_json",
    ),
    Setting(
        "lang_merger.zh_en_letter_threshold",
        "int",
        "zh 英文含量閾值",
        "merger",
        C_MERGER,
        "超過此數值判定為英文，空白用預設值 2",
        default=2,
    ),
    Setting(
        "lang_merger.patchouli_skip_en_us_when_zh_cn_exists",
        "bool",
        "優先使用已有繁中，無則信任簡中（跳過英文）",
        "merger",
        C_MERGER,
        "",
        default=False,
    ),
    Setting(
        "lang_merger.patchouli_effective_translation_threshold",
        "float",
        "en_us 跳過門檻",
        "merger",
        C_MERGER,
        "有效翻譯比例閾值 0.0~1.0，空白用預設值 0.5",
        default=0.5,
    ),
    Setting(
        "lang_merger.enable_extracted_to_assets_merge",
        "bool",
        "合併 XX_extracted → assets/(merge 階段2)",
        "merger",
        C_MERGER,
        "",
        default=True,
    ),
    Setting(
        "extractor.output_folder_names.lang_extract",
        "str",
        "Lang 提取輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        "未填入輸出路徑時自動帶入此名稱",
        default="_提取lang_輸出",
    ),
    Setting(
        "extractor.output_folder_names.book_extract",
        "str",
        "Book 提取輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        "未填入輸出路徑時自動帶入此名稱",
        default="_提取book_輸出",
    ),
    Setting(
        "extractor.output_folder_names.dual_extract",
        "str",
        "Dual 提取輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        "Lang + Book 同時提取時使用",
        default="_提取both_輸出",
    ),
    Setting(
        "extractor.output_folder_names.lang_preview",
        "str",
        "Lang 預覽輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        "未填入輸出路徑時自動帶入此名稱",
        default="_預覽lang_輸出",
    ),
    Setting(
        "extractor.output_folder_names.book_preview",
        "str",
        "Book 預覽輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        "未填入輸出路徑時自動帶入此名稱",
        default="_預覽book_輸出",
    ),
    Setting(
        "extractor.output_folder_names.dual_preview",
        "str",
        "Dual 預覽輸出資料夾",
        "extractor",
        C_EXTRACTOR,
        "Lang + Book 同時預覽時使用",
        default="_預覽both_輸出",
    ),
    Setting(
        "extractor.skip_zh_cn_extract",
        "bool",
        "提取頁預設跳過 zh_cn（可在每次操作前覆寫）",
        "extractor",
        C_EXTRACTOR,
        "",
        default=False,
    ),
    Setting(
        "ui.theme_mode",
        "none",
        "",
        None,
        None,
        "",
        default="dark",
        reason="由側欄的深淺色切換，不在設定頁",
        timing="immediate",
        timing_note="切換後立即套用。",
    ),
    Setting(
        "jar_extractor.lang_codes",
        "none",
        "",
        None,
        None,
        "",
        default=["en_us", "zh_cn", "zh_tw"],
        reason="提取流程固定的語系清單，由提取頁處理",
    ),
    Setting(
        "lang_merger.process_zh_cn_files",
        "none",
        "",
        None,
        None,
        "",
        default=True,
        reason="由合併頁的開關覆寫（merge_view）",
    ),
    Setting(
        "lang_merger.skip_zh_cn_when_only_process_lang",
        "none",
        "",
        None,
        None,
        "",
        default=False,
        reason="由合併流程內部使用，尚無設定頁需求",
    ),
    Setting(
        "extractor.target_language",
        "none",
        "",
        None,
        None,
        "",
        default=["zh_tw"],
        reason="歷史相容欄位；追查不到正式 caller，不宣稱可調整",
    ),
    Setting(
        "translator.cjk_ratio_threshold",
        "none",
        "",
        None,
        None,
        "",
        default=0.7,
        reason="歷史相容欄位；追查不到 caller，不臆造語意",
    ),
)

SETTINGS_BY_PATH: dict[str, Setting] = {s.path: s for s in SETTINGS}


def build_default_config() -> dict[str, Any]:
    """由 schema 建出完整的預設設定（深拷貝，呼叫端可自由修改）。"""
    config: dict[str, Any] = {}
    for setting in SETTINGS:
        set_path(config, setting.path, copy.deepcopy(setting.default))
    return config


def ui_settings() -> list[Setting]:
    """所有會在設定頁出現的設定（含專用元件），依 SETTINGS 順序。"""
    return [s for s in SETTINGS if s.kind != "none"]


def editable_settings() -> list[Setting]:
    """由 schema 產生控制項的設定（不含專用元件與不在設定頁的）。"""
    return [s for s in SETTINGS if s.kind not in ("none", "custom")]


def sensitive_paths() -> frozenset[str]:
    """值是機密的設定路徑。"""
    return frozenset(s.path for s in SETTINGS if s.sensitive)


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
