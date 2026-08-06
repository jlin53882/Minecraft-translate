# Minecraft Translator Flet — 專案索引

> 版本：0.8.0｜最後更新：2026-08-06

---

## 1. 專案概述

**用途**：Minecraft 模組翻譯工具，支援 KubeJS、FTB Quests、Patchouli、Markdown 等多種格式的翻譯 pipeline。提供 Flet 桌面 GUI 介面。

**技術棧**：Python 3.12+｜Flet 0.28.3｜Google Gemini API｜OpenCC

---

## 2. 目錄結構

```
Minecraft-translate/
├── app/                      # Flet 桌面應用程式（本體）
│   ├── ui/                   # UI 元件
│   │   ├── components.py    # 通用元件
│   │   ├── keyboard_shortcuts.py
│   │   ├── quick_jump.py
│   │   ├── theme.py
│   │   └── view_wrapper.py
│   ├── views/               # 各功能頁面（View）
│   │   ├── bundler_view.py        # 輸出打包
│   │   ├── cache_query_panel.py   # 快取查詢面板
│   │   ├── cache_shard_panel.py   # 快取分片面板
│   │   ├── cache_view.py          # 快取管理
│   │   ├── config_view.py         # 設定頁
│   │   ├── extractor_view.py      # 擷取頁
│   │   ├── icon_preview_view.py   # 圖示預覽
│   │   ├── lm_view.py             # LM 翻譯頁
│   │   ├── lookup_view.py         # 查詢頁
│   │   ├── merge_view.py          # 合併頁
│   │   ├── pipeline/              # 一鍵批次翻譯（pipeline_view + 5 dialogs）
│   │   ├── qc_base.py             # QC 基礎
│   │   ├── qc_view.py             # QC 檢查頁
│   │   ├── rules_view.py          # 規則頁
│   │   ├── translation_view.py     # 翻譯頁
│   │   └── untranslated_checker.py # 未翻譯檢查
│   ├── logging/              # 日誌模組
│   ├── services_impl/        # 服務實作
│   ├── views/                # View 子模組（見上方）
│   ├── services.py           # 服務協調
│   ├── startup_tasks.py      # 啟動任務
│   ├── task_session.py       # 任務 session
│   └── view_registry.py      # View 註冊表
│
├── translation_tool/         # 核心翻譯引擎（無 GUI 相依）
│   ├── core/                 # 核心翻譯 pipeline
│   │   ├── lm_translator.py          # LM 翻譯主程式
│   │   ├── lm_translator_main.py     # LM 翻譯入口
│   │   ├── lm_translator_scan.py     # 掃描器
│   │   ├── lm_translator_shared*.py  # 共用邏輯（cache/loop/preview/recording）
│   │   ├── lm_api_client.py          # API 客戶端
│   │   ├── lm_response_parser.py     # 回應解析
│   │   ├── lm_config_rules.py         # 設定規則
│   │   ├── lang_merger.py            # 語系合併
│   │   ├── lang_merge_*.py           # 合併相關（pipeline/content/content_copy/pending/zip_io）
│   │   ├── lang_processing_format.py  # 處理格式
│   │   ├── lang_codec.py             # 編碼處理
│   │   ├── lang_item_row.py          # 語系列處理
│   │   ├── md_translation_*.py       # MD 翻譯（assembly/progress/stats/steps）
│   │   ├── ftb_translator*.py        # FTB 翻譯（clean/export/template）
│   │   ├── jar_processor*.py         # JAR 處理（discovery/extract/preview）
│   │   ├── icon_*.py                 # 圖示處理（classifer/preview_cache/reason/resolver）
│   │   ├── translatable_extractor.py  # 可翻譯文字抽取
│   │   ├── output_bundler.py         # 輸出打包
│   │   └── translation_path_writer.py # 路徑寫入
│   │
│   ├── plugins/              # 各檔案格式的翻譯插件
│   │   ├── kubejs/                  # KubeJS tooltip 翻譯
│   │   │   ├── kubejs_translator*.py   # KubeJS 主翻譯器（clean/io/paths）
│   │   │   ├── kubejs_tooltip_*.py    # Tooltip 專用（extract/inject/lmtranslator）
│   │   │   └── __init__.py
│   │   ├── md/                      # Markdown 翻譯
│   │   │   ├── md_extract_qa.py       # 抽取 QA
│   │   │   ├── md_inject_qa.py        # 注入 QA
│   │   │   ├── md_lmtranslator.py     # LM 翻譯器
│   │   │   └── __init__.py
│   │   ├── ftbquests/               # FTB Quests SNBT 翻譯
│   │   │   ├── ftbquests_snbt_*.py     # 抽取/注入
│   │   │   ├── ftbquests_lmtranslator.py
│   │   │   └── __init__.py
│   │   └── shared/                  # 插件共用工具
│   │       ├── json_io.py
│   │       └── lang_rules.py
│   │
│   ├── checkers/              # 翻譯品質檢查
│   │   ├── english_residue_checker.py  # 英文殘留檢查
│   │   ├── untranslated_checker.py     # 未翻譯檢查
│   │   └── variant_comparator*.py       # 變體比較
│   │
│   └── utils/                  # 工具函式
│       ├── cache_loader.py      # 快取載入
│       ├── cache_manager.py     # 快取管理（核心）
│       ├── cache_overview.py    # 快取總覽
│       ├── cache_search*.py     # 快取搜尋（facade）
│       ├── cache_shards.py      # 快取分片
│       ├── cache_store.py       # 快取儲存
│       ├── config_access.py     # 設定存取
│       ├── config_manager.py    # 設定管理
│       ├── exceptions.py        # 自訂例外
│       ├── log_unit.py          # 日誌工具
│       ├── safe_json_loader.py  # 安全 JSON 載入
│       ├── species_cache.py     # 物種快取
│       ├── text_processor.py    # 文字處理
│       └── ui_logging_handler.py
│
├── tests/                     # 測試（178 個測試檔、1905 個測試）
│   ├── conftest.py            # pytest 全域 fixture
│   ├── fixtures/              # 測試資料
│   ├── test_ftb*.py           # FTB 翻譯器測試
│   ├── test_kubejs*.py        # KubeJS 翻譯器測試
│   ├── test_lm_*.py           # LM 翻譯器測試
│   ├── test_lang_*.py         # 語系合併測試
│   ├── test_md_*.py           # MD 翻譯器測試
│   ├── test_cache*.py         # 快取系統測試
│   ├── test_jar*.py           # JAR 處理測試
│   ├── test_icon*.py          # 圖示處理測試
│   └── test_view_*.py         # View 介面測試
│
├── docs/                      # 文件（架構／流程／規範）
│
├── workspace/                 # OpenClaw agent 工作區
├── tools/                     # 工具腳本
├── logs/                      # 執行日誌
├── backups/                   # 備份
├── .github/workflows/         # GitHub Actions
└── pyproject.toml
```

---

## 3. 翻譯 Pipeline 對照表

| 格式 | Step 1（抽取） | Step 2（翻譯） | Step 3（注入） | 設定檔 |
|------|---------------|---------------|---------------|--------|
| **KubeJS** | `kubejs_translator.py` | `kubejs_tooltip_lmtranslator.py` | `kubejs_tooltip_inject.py` | `kubejs_translator_clean.py` |
| **FTB Quests** | `ftbquests_snbt_extractor.py` | `ftbquests_lmtranslator.py` | `ftbquests_snbt_inject.py` | `ftb_translator_clean.py` |
| **Patchouli** | `jar_processor_extract.py` | `lm_translator.py` | `lang_merge_content_copy.py` | `lang_merge_pipeline.py` |
| **Markdown** | `md_translation_steps.py:step1` | `md_translation_steps.py:step2` | `md_translation_steps.py:step3` | `md_translation_assembly.py` |
| **Lang 檔** | `lang_merge_pending.py` | `lm_translator.py` | `lang_merge_pipeline.py` | `lang_codec.py` |

---

## 4. 核心類別與職責

### 4.1 Flet Views（`app/views/`）

| View | 職責 |
|------|------|
| `cache_view.py` | 翻譯快取管理與查詢 |
| `lm_view.py` | LM 翻譯執行與進度追蹤 |
| `merge_view.py` | 多語系合併管理 |
| `pipeline_view.py` | 一鍵批次翻譯（Pipeline 自動化流程） |
| `extractor_view.py` | JAR/模組內容擷取 |
| `bundler_view.py` | 翻譯產出打包輸出 |
| `icon_preview_view.py` | 遊戲道具圖示預覽 |
| `config_view.py` | 全域設定頁面 |
| `rules_view.py` | 翻譯規則管理 |

### 4.2 Core 模組（`translation_tool/core/`）

| 模組 | 職責 |
|------|------|
| `lm_translator.py` + `_main.py` | LM API 呼叫、翻譯核心邏輯 |
| `lm_translator_shared_loop.py` | 翻譯批次迴圈、cache 命中分流 |
| `lm_translator_shared_cache.py` | Cache 讀寫與 split 邏輯 |
| `lm_translator_shared_recording.py` | 翻譯結果寫入 cache |
| `lang_merge_pipeline.py` | Lang 檔合併主流程 |
| `lang_merge_pending.py` | 待翻譯清單處理 |
| `jar_processor*.py` | JAR 檔案解析與預覽 |
| `icon_*.py` | 道具圖示分類與快取 |
| `md_translation_steps.py` | MD 三步驟（extract/translate/inject） |

### 4.3 Utils（`translation_tool/utils/`）

| 模組 | 職責 |
|------|------|
| `cache_manager.py` | 快取管理器（query/shards/overview）|
| `cache_search*.py` | 快取語意/關鍵字搜尋 |
| `config_manager.py` | 設定讀寫與驗證 |
| `text_processor.py` | 文字處理與正規化 |

---

## 5. 資料流向（以 KubeJS 為例）

```
JAR 檔案
  └── jar_processor_discovery.py     # 發現 KubeJS JS 檔
        └── jar_processor_extract.py # 抽出原始 JS
              └── kubejs_translator_clean.py
                    ├── step1_extract_and_clean()
                    │     ├── extraction（抽出可翻譯區塊）
                    │     ├── OpenCC 簡→繁轉換
                    │     ├── 三語合併（tw > cn→tw > en）
                    │     └── 輸出：raw/ + pending/ + final/
                    │
                    └── step2_translate_lm()
                          ├── kubejs_tooltip_lmtranslator.py
                          │     ├── _is_valid_hit()     # cache 命中判斷
                          │     ├── _is_tw_text()       # 繁體跳過
                          │     ├── _split_off_tw_items() # 繁體 items 分流
                          │     └── translate_kubejs_pending_to_zh_tw()
                          │           ├── fast_split_items_by_cache()
                          │           ├── translate_items_with_cache_loop()
                          │           └── 輸出：LM翻譯後/
                          │
                          └── step3_inject()
                                └── kubejs_tooltip_inject.py  # 寫回 JS
                                      └── 輸出：最終翻譯後 JS
```

---

## 6. 快取系統

**Cache Manager** (`cache_manager.py`) 是核心，負責：
- 讀取/寫入快取資料（SQLite）
- 搜尋與查詢介面
- 分片管理

**Cache 分片**（`cache_shards.py`）：
- 依語言分片（zh_tw / zh_cn / en 等）
- 各分片獨立查詢，減少鎖竞争

**Cache 命中流程**：
```
pending item → cache key（path|source_text）
  → cache lookup → _is_valid_hit() 驗證
    ├── valid hit → 直接使用譯文
    └── miss → 送 LM API 翻譯 → 寫入 cache
```

---

## 7. Flet UI 架構

```
app/
├── services.py          # 服務協調層（協調 view 與 core）
├── view_registry.py     # View 註冊工廠
├── task_session.py      # 任務 session 管理
├── startup_tasks.py     # 啟動時執行的工作
├── ui/
│   ├── components.py    # Button / Input / Card 等通用元件
│   ├── view_wrapper.py  # View 包裝器（含滾動/標題列）
│   ├── theme.py         # 主題定義
│   └── keyboard_shortcuts.py
└── views/
    ├── lm_view.py          # 翻譯頁（核心）
    ├── cache_view.py       # 快取頁
    ├── merge_view.py       # 合併頁
    ├── extractor_view.py   # 擷取頁
    └── ...
```

**ViewWrapper** 提供的標準行為：
- 頁面標題列
- 滾動區域
- 底部操作列
- 快捷鍵支援

---

## 8. 測試覆蓋

- **178 個測試檔**（`tests/test_*.py`，共 1905 個測試）
- **主要測試分類**：
  - `test_kubejs_*.py` — KubeJS 流程
  - `test_ftbquests_*.py` — FTB Quests 流程
  - `test_md_*.py` — Markdown 流程
  - `test_lm_*.py` — LM 翻譯器
  - `test_lang_*.py` — 語系合併
  - `test_cache*.py` — 快取系統
  - `test_view_*.py` — UI View 表徵測試

---

## 9. 重要設定檔

| 檔案 | 用途 |
|------|------|
| `pyproject.toml` | 專案依賴與版本（v0.8.0）|
| `translation_tool/core/lm_config_rules.py` | LM API 行為設定（batch size / temperature 等）|
| `translation_tool/utils/config_manager.py` | 應用程式設定管理 |
| `docs/PR_WORKFLOW.md` | PR 工作流程規範 |
| `docs/PROJECT_STRUCTURE.md` | 專案結構與模組職責 |

## 10. View 架構文件（docs/）

每個主視圖一份架構文件，說明 UI 元件、呼叫鏈、與 service 層關係：

| 文件 | 對應 View |
|------|-----------|
| `docs/EXTRACTOR_VIEW_ARCHITECTURE.md` | ExtractorView（`app/views/extractor/`） |
| `docs/BUNDLER_VIEW_ARCHITECTURE.md` | BundlerView（`app/views/bundler_view.py`） |
| `docs/CACHE_VIEW_ARCHITECTURE.md` | CacheView（`app/views/cache_view.py`） |
| `docs/CONFIG_VIEW_ARCHITECTURE.md` | ConfigView（`app/views/config/`） |
| `docs/ICON_VIEW_ARCHITECTURE.md` | IconPreviewView（`app/views/icon_preview_view.py`） |
| `docs/LM_VIEW_ARCHITECTURE.md` | LMView（`app/views/lm_view.py`） |
| `docs/LOOKUP_VIEW_ARCHITECTURE.md` | LookupView（`app/views/lookup_view.py`） |
| `docs/MERGE_VIEW_ARCHITECTURE.md` | MergeView（`app/views/merge_view.py`） |
| `docs/PIPELINE_VIEW_ARCHITECTURE.md` | PipelineView（`app/views/pipeline/`） |
| `docs/QC_VIEW_ARCHITECTURE.md` | QCView（`app/views/qc_view.py` + `qc_base.py`） |
| `docs/RULES_VIEW_ARCHITECTURE.md` | RulesView（`app/views/rules/`） |
| `docs/TRANSLATION_VIEW_ARCHITECTURE.md` | TranslationView（`app/views/translation/`） |
| `docs/UNTRANSLATED_CHECKER_ARCHITECTURE.md` | UntranslatedChecker（`app/views/untranslated_checker.py`） |

相關系統文件：`JAR_PIPELINE.md`（抽取流程）、`CACHE_SYSTEM.md`（快取資料結構）、`cache_search_optimization.md`（搜尋效能）、`TRANSLATION_WORKFLOW.md`（翻譯流程）。

---

## 11. 依賴套件

| 套件 | 版本 | 用途 |
|------|------|------|
| flet-cli / flet | 0.28.3 | 桌面 GUI |
| ftb-snbt-lib | ≥0.4.1 | SNBT 格式解析 |
| google-genai | ≥1.56.0 | Gemini API |
| opencc-python-reimplemented | ≥0.1.7 | 簡繁轉換 |
| pandas | ≥2.3.3 | 資料處理 |
| pytest | ≥9.0.2 | 測試框架 |

---

*本文件由 agent 自動維護，最後更新：2026-08-05*
