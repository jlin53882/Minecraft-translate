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
├── tests/                 # 單元測試（178 檔、1905 tests）
├── tools/                 # 開發輔助腳本
└── docs/                  # 專案文件
```

### `app/` — UI 層（Flet）

| 子目錄／檔案 | 職責 |
|---|---|
| `views/*.py` | 12 個主視圖：config / rules / cache / qc / lookup / icon_preview / bundler / translation / extractor / lm / merge / pipeline |
| `views/cache_manager/` | 快取視圖 MVC 子模組（panels/overview、query、shard + actions/state/controller） |
| `views/cache/` | 舊版快取視圖實驗碼（未接線，待清理） |
| `views/pipeline/` | 一鍵批次翻譯子模組（pipeline_view + 5 個 dialog：extract/merge/translate/bundle/one_click） |
| `views/config/`、`views/extractor/`、`views/rules/`、`views/translation/` | 各主視圖的 panels/actions/state 子模組 |
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
| `checkers/` | 品管檢查器（generator 形式，回傳 `CheckResult`），各 checker 職責如下 |

`checkers/` 包含以下檢查器：

| 模組 | 職責 |
|------|------|
| `untranslated_checker.py` | 偵測仍未未翻譯的條目 |
| `english_residue_checker.py` | 偵測翻譯結果中殘留的英文字詞 |
| `color_char_checker.py` | 偵測顏色代碼（§）或特殊字元異常 |
| `variant_comparator.py` | 比較同鍵在 en_us / zh_cn / zh_tw 的 variant 差異 |
| `variant_comparator_tsv.py` | TSV 格式的 variant 比較工具 |
| `utils/` | 工具：快取、config、text_processing、logging、例外處理 |

`utils/` 包含以下 cache 相關模組：

| 模組 | 職責 |
|------|------|
| `cache_manager.py` | 公開 API façade，協調載入／儲存／查詢 |
| `cache_store.py` | 持有 `CacheRuntimeState`，提供 dict 層級讀寫 helper |
| `cache_loader.py` | 從磁碟讀取 cache 檔案（支援平行 workers） |
| `cache_shards.py` | 滾動分片管理（每片 2500 筆，容量滿自動輪轉） |
| `cache_overview.py` | cache 概覽（各 type 統計資訊）建構 |
| `cache_search.py` | SQLite FTS5 全文搜尋實作（低層） |
| `cache_search_facade.py` | 搜尋 façade，封裝 FTS5 與模糊比對邏輯 |

### `tests/` — 測試

178 個測試檔、1905 個測試案例（pytest），覆蓋 app/、translation_tool/core/、translation_tool/utils/ 所有模組。

### `tools/` — 開發工具

分析腳本（覆蓋率分析、間隙分析）、修補腳本、驗證腳本。

### `docs/` — 專案文件

每個主視圖一份架構文件（`*_VIEW_ARCHITECTURE.md`），涵蓋 UI 元件、呼叫鏈與 service 層關係；相關文件一覽見 `PROJECT_INDEX.md` §10。

## 入口點

`main.py` → `bootstrap_runtime()`（初始化 config + logging）→ Flet Page 組裝 → `build_view_registry()`（注册所有 View）→ 背景執行 `start_background_startup_tasks()`（索引重建）。

## 關鍵模組職責對照表

| 模組 | 職責 | 對外函式 |
|---|---|---|
| `core/lm_translator_main.py` | AI 翻譯入口調度，支援批次大小自動調整、錯誤重試、API Key 輪替 | `translate_batch_smart()`, `translate_batch_smart_old()` |
| `core/lm_translator.py` | AI 翻譯主邏輯，支援 Gemini API 目錄批次翻譯、斷點續傳 | `translate_directory_generator()`, `save_checkpoint()`, `load_checkpoint()`, `clear_checkpoint()` |
| `core/lang_merger.py` | 智慧合併 en_us / zh_cn / zh_tw，保留已翻譯內容 | `merge_zhcn_to_zhtw_from_zip()`, `get_lang_codes()`, `build_lang_file_regex()` |
| `core/jar_processor.py` | 從 mod JAR 提取語言檔與 Patchouli 手冊（ Generator 介面） | `extract_lang_files_generator()`, `extract_book_files_generator()`, `preview_extraction_generator()` |
| `core/jar_processor_extract.py` | JAR 提取的底層實作（路徑正規化、程序執行） | `extract_from_jar_impl()`, `run_extraction_process_impl()`, `get_file_hash()` |
| `core/lm_api_client.py` | Gemini API 呼叫包裝，處理 Overload / 429 等錯誤 | `call_gemini_requests()` |
| `core/lm_response_parser.py` | AI 回應 JSON 解析，處理截斷與 chunked 回應 | `safe_json_loads()`, `_extract_json_blocks()`, `chunked()` |
| `core/lm_translator_shared_loop.py` | 批次翻譯迴圈（含 cache 查詢、WAR 記錄） | `translate_items_with_cache_loop()`, `_get_default_batch_size()` |
| `core/lm_translator_shared_cache.py` | 翻譯 cache 查詢與匹配邏輯 | `fast_split_items_by_cache()`, `get_default_cache_rules()`, `_is_valid_hit()` |
| `core/lm_translator_shared_preview.py` | 翻譯預覽輸出（dry-run / cache-hit） | `write_dry_run_preview()`, `write_cache_hit_preview()` |
| `core/lang_merge_pipeline.py` | 合併流程管線（ZIP → 解壓 → 合併 → 寫出） | （內部用於 `lang_merger`） |
| `plugins/ftbquests/ftbquests_lmtranslator.py` | FTB Quests 翻譯引擎 | `translate_ftb_pending_to_zh_tw()`, `DryRunStats` |
| `plugins/ftbquests/ftbquests_snbt_extractor.py` | FTB Quests SBNT 解析與欄位提取 | `extract_lang_file()`, `extract_quest_file()`, `process_quest_folder()` |
| `plugins/ftbquests/ftbquests_snbt_inject.py` | FTB Quests SBNT 翻譯注入 | `patch_lang_snbt_file()`, `patch_quest_snbt_file()`, `inject_ftbquests_zh_tw_from_jsons()` |
| `plugins/kubejs/kubejs_tooltip_lmtranslator.py` | KubeJS Tooltip 翻譯引擎 | `translate_kubejs_pending_to_zh_tw()`, `DryRunStats` |
| `plugins/kubejs/kubejs_tooltip_extract.py` | KubeJS tooltip 文字提取 | `extract()`, `extract_itemevents_tooltips()` |
| `plugins/kubejs/kubejs_tooltip_inject.py` | KubeJS tooltip 翻譯注入 | `inject()`, `replace_text_in_text_obj()` |
| `plugins/md/md_lmtranslator.py` | Markdown 文件翻譯引擎 | `translate_md_pending()`, `PendingItem` |
| `plugins/md/md_extract_qa.py` | Markdown 翻譯品質萃取 | `extract_blocks()`, `build_pending_json()`, `BlockItem` |
| `plugins/md/md_inject_qa.py` | Markdown 翻譯注入 | `apply_item_to_md_lines()`, `Item` |
| `plugins/shared/rich_text_shield.py` | 特殊文字遮蔽（顏色代碼、物品名佔位符） | `shield_text()`, `unshield_text()` |
| `plugins/shared/lang_path_rules.py` | 語言檔路徑轉換規則 | `should_rename_to_zh_tw()`, `compute_output_path()` |
| `plugins/shared/lang_text_rules.py` | 語言文字內容規則（已翻譯判斷） | `is_already_zh()` |
| `plugins/shared/json_io.py` | 通用 JSON 讀寫工具 | `read_json_dict()`, `write_json_dict()`, `collect_json_files()` |
| `checkers/untranslated_checker.py` | 偵測未翻譯條目 | `check_untranslated_generator()` |
| `checkers/english_residue_checker.py` | 偵測英文殘留 | `check_english_residue_generator()`, `find_json_files()` |
| `checkers/color_char_checker.py` | 偵測色彩字元殘留 | `check_color_chars()`, `check_directory()`, `ColorCharError` |
| `utils/cache_manager.py` | SQLite 分片快取、全文搜尋、WAL 優化 | module-level façade（無 class） |
| `utils/config_manager.py` | 設定載入與驗證、Logging 初始化 | `load_config()`, `setup_logging()` |
| `app/views/translation_view.py` | 任務翻譯工具主視圖（FTB / KubeJS / Markdown） | `TranslationView` |
| `app/views/lm_view.py` | 機器翻譯設定與操作視圖 | `LMView` |
| `app/ui/theme.py` | 主題系統（深色／淺色切換） | module-level constants（無 class） |
