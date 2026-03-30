# Minecraft-translate 專案結構總覽

## 專案定位
將 Minecraft 模組包從簡體中文／英文批量翻譯為繁體中文（台灣用語）的 Flet 桌面應用程式。

## 目錄結構

```
Minecraft-translate/
├── main.py                 # 應用程式入口（Flet Page 組裝）
├── pyproject.toml         # 專案設定與依賴
├── app/                   # UI 層（Flet）
├── translation_tool/      # 核心翻譯引擎（不含 UI）
├── tests/                 # 單元測試（834 passed）
├── tools/                 # 開發輔助腳本
└── docs/                  # 專案文件
```

### `app/` — UI 層（Flet）

| 子目錄／檔案 | 職責 |
|---|---|
| `views/*.py` | 11 個主視圖：config / rules / cache / qc / lookup / icon_preview / bundler / translation / extractor / lm / merge |
| `views/cache/` | 快取查詢子視圖（QueryPanel / ShardPanel） |
| `views/translation/` | 翻譯子視圖 |
| `services_impl/` | 服務實作（pipeline 業務邏輯），不含 QC/checkers |
| `services.py` | façade：僅保留 QC/checkers 暂緩線的 re-export |
| `ui/` | 通用 UI 元件：theme.py（主題）、view_wrapper.py、components.py、keyboard_shortcuts.py、quick_jump.py |
| `startup_tasks.py` | 背景啟動任務（索引重建等） |
| `view_registry.py` | View 註冊與導航選單建構 |
| `task_session.py` | 任務階段追蹤 |

### `translation_tool/` — 核心翻譯引擎

| 子目錄 | 職責 |
|---|---|
| `core/` | 翻譯核心演算法：lm_translator（AI 翻譯）、lang_merger（語言合併）、jar_processor（JAR 處理）、output_bundler（打包輸出） |
| `plugins/` | 格式插件：ftbquests（SBNT）、kubejs、md（Markdown）、shared（通用 JSON IO / lang rules） |
| `checkers/` | 品管檢查器：untranslated_checker、english_residue_checker、color_char_checker、variant_comparator |
| `utils/` | 工具：cache_*（快取管理）、config_manager、text_processor、log_unit、exceptions |

### `tests/` — 測試

146 個測試檔、1132 個測試案例（pytest），覆蓋 app/、translation_tool/core/、translation_tool/utils/ 所有模組。

### `tools/` — 開發工具

分析腳本（覆蓋率分析、間隙分析）、修補腳本、驗證腳本。

## 入口點

`main.py` → `bootstrap_runtime()`（初始化 config + logging）→ Flet Page 組裝 → `build_view_registry()`（注册所有 View）→ 背景執行 `start_background_startup_tasks()`（索引重建）。

## 關鍵模組職責對照表

| 模組 | 職責 | 關鍵類別/函式 |
|---|---|---|
| `core/lm_translator.py` | AI 翻譯主邏輯，支援 Gemini API、多 Key 輪替、批次翻譯 | `translate_directory_generator` |
| `core/lang_merger.py` | 智慧合併 en_us / zh_cn / zh_tw，保留已翻譯內容 | `merge_zhcn_to_zhtw_from_zip` |
| `core/jar_processor.py` | 從模組 JAR 提取語言檔與 Patchouli 手冊 | `extract_lang_files_generator` / `extract_book_files_generator` |
| `plugins/ftbquests/` | FTB Quests SBNT 格式翻譯 | `translate_ftb_pending_to_zh_tw` / `DryRunStats` |
| `plugins/kubejs/` | KubeJS 提示文字翻譯，含路徑解析與注入 | `translate_kubejs_pending_to_zh_tw` / `DryRunStats` |
| `plugins/md/` | Markdown 文件翻譯（含翻譯進度統計） | `translate_md_pending` / `PendingItem` |
| `checkers/untranslated_checker.py` | 偵測未翻譯條目 | `check_untranslated_generator` |
| `checkers/english_residue_checker.py` | 偵測英文殘留 | `check_english_residue_generator` |
| `utils/cache_manager.py` | SQLite 分片快取、全文搜尋、WAL 優化 | module-level façade（無 class） |
| `utils/config_manager.py` | 設定載入與驗證、Logging 初始化 | `load_config` / `setup_logging` |
| `app/views/translation_view.py` | 任務翻譯工具主視圖（FTB / KubeJS / Markdown） | `TranslationView` |
| `app/views/lm_view.py` | 機器翻譯設定與操作視圖 | `LMView` |
| `app/ui/theme.py` | 主題系統（深色／淺色切換） | module-level constants（無 class） |
