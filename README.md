# Minecraft 模組包繁體化工具（Flet）

基於 [Flet](https://flet.dev/) 的桌面應用程式，用於將 Minecraft 模組包從簡體中文／英文批量翻譯為繁體中文（台灣用語）。

## 目前狀態（2026-03-16）

- ✅ `app.services` 已收斂為 **QC/checkers 暫緩線 façade**（PR29）
- ✅ 主線 caller 已完成遷移到 `app.services_impl.*`（PR28a + PR28b）
- ✅ 最近一次完整測試記錄：**834 passed**
- ✅ **PR62-71 全部完成**：
  - PR62: 測試覆蓋率健檢
  - PR63: 測試基礎設施（fixtures）
  - PR64: Docstring 補完
  - PR65: README 更新
  - PR66-A: Cache 監控
  - PR67: Lazy Load 優化
  - PR68: UI Component 抽取
  - PR69: 主題系統建立（app/ui/theme.py）
  - PR70: 廢棄程式碼清理
  - PR71: Exception 使用一致性評估
- ✅ **搜尋索引效能優化**（2026-03-16）：
  - SQLite PRAGMA 效能優化（WAL、同步關閉、記憶體快取）
  - 並行處理 Metadata 建立（ThreadPoolExecutor, 4執行緒）
  - 批次大小 5000 → 20000
  - 直接寫入避開 Windows 檔案鎖
  - LSP 類型修復
  - **結果：重建時間 49秒 → 4秒（提升91%）**

> 詳細變更請看 `docs/pr/2026-03-12_0204_PR_pr28a-low-risk-caller-migration-design.md`、
> `docs/pr/2026-03-12_0205_PR_pr28b-high-risk-caller-migration-design.md`、
> `docs/pr/2026-03-12_1003_PR_pr29-services-facade-non-qc-cleanup.md`。

## UI 對照（啟用狀態，依 `main.py`）

### 已啟用（目前會出現在左側選單）- 11 個主 View
- `config_view.py`：設定
- `rules_view.py`：替換規則
- `cache_view.py`：快取管理（含 QueryPanel / ShardPanel）
- `qc_view.py`：品質檢查（含 UntranslatedChecker / QCBase）
- `lookup_view.py`：查詢
- `icon_preview_view.py`：圖示預覽
- `bundler_view.py`：打包
- `translation_view.py`：任務翻譯工具（FTB / KubeJS / Markdown）
- `extractor_view.py`：JAR 提取
- `lm_view.py`：機器翻譯
- `merge_view.py`：檔案合併

## 功能

- **JAR 提取**：從模組 JAR 檔提取語言檔與 Patchouli 手冊
- **語言合併**：智慧合併 `en_us.json`、`zh_cn.json`、`zh_tw.json`，保留已翻譯內容
- **簡繁轉換**：基於 OpenCC S2TW，含自訂替換規則
- **AI 機器翻譯**：串接 Gemini API，支援多 Key 輪替、批次翻譯、自動重試
- **快取管理**：翻譯快取分片儲存、全文搜尋、版本歷史
- **品管檢查**：偵測未翻譯條目、簡繁不一致、英文殘留
- **輸出打包**：產出可直接使用的資源包 ZIP

## 支援的翻譯來源格式

| 格式 | 說明 |
|---|---|
| `lang/*.json` | Minecraft 標準語言檔 |
| `patchouli_books/` | Patchouli 手冊 JSON |
| `ftbquests/*.snbt` | FTB Quests 任務檔 |
| `kubejs/*.js` | KubeJS Tooltip 腳本 |
| `*.md` | Markdown 文件 |

## 安裝

需求：Python `>=3.12`、[uv](https://github.com/astral-sh/uv)

```bash
# 1) Clone
git clone https://github.com/jlin53882/Minecraft-translate.git
cd Minecraft-translate

# 2) 安裝依賴
uv sync

# 3) 設定 config
cp config.example.json config.json
# 編輯 config.json，填入 Gemini API Key
```

## 使用

```bash
uv run python main.py
```

啟動後會開啟桌面 GUI，左側導覽列可切換功能頁面。

## 開發與測試

```bash
# 基本 smoke test
uv run python -c "import main; print('ok')"

# 全量測試
uv run pytest -q
```

Windows 若遇到 `WinError 5`（使用者目錄快取/暫存權限），建議改用 repo 內路徑：

```powershell
$env:UV_CACHE_DIR = ".uv-cache"
$env:TMP = ".tmp"
$env:TEMP = ".tmp"
uv run pytest -q --basetemp=.pytest-tmp\full -o cache_dir=.pytest-cache\full
```

## 設定說明

`config.json` 主要區塊：

| 區塊 | 說明 |
|---|---|
| `logging` | 日誌等級與輸出目錄 |
| `translator` | 輸出資料夾、快取目錄、平行處理 worker 數 |
| `species_cache` | 生物學名快取（Wikipedia 查詢） |
| `lm_translator` | Gemini API Keys、模型設定、批次大小、System Prompt |
| `output_bundler` | 最終 ZIP 打包路徑 |
| `lang_merger` | 待翻譯與隔離資料夾命名 |

> ⚠️ `config.json` 含 API Key，已加入 `.gitignore`，請勿上傳。

## 架構重點

### 1) Service 分層

- `app/services_impl/*`：主線 canonical services（config/cache/pipelines）
- `app/services.py`：QC/checkers 所需 façade

### 2) Views 子模組化

部分 View 已拆分為獨立子模組：
- `views/cache_manager/`：快取管理（panels/overview/query/shard）
- `views/config/`：設定（config_form/actions）
- `views/extractor/`：JAR 提取（extractor_panels/actions/state）
- `views/rules/`：替換規則（rules_table/actions/state）
- `views/translation/`：翻譯任務（translation_panels/actions/state）

### 3) 核心處理邏輯

- `translation_tool/core/lang_merger.py` + `lang_merge_*.py`
  - 依 `en_us.json` / `zh_cn.json` / `zh_tw.json` 組合決定合併策略
  - JSON 語言檔採增量更新；內容檔採覆寫策略
- `translation_tool/core/lm_translator_main.py` + `lm_translator_*.py`
  - 批次送 Gemini，依錯誤類型做縮批、重試、換 Key、節流

### 4) 快取搜尋系統

- `translation_tool/utils/cache_search.py`
  - SQLite FTS5 全文搜尋
  - 支援模糊比對、相似詞推薦
  - 2026-03-16 優化：49秒 → 4秒（提升 91%）

## 專案結構

```text
├── app/
│   ├── services.py                      # QC façade
│   ├── services_impl/                    # 主線 canonical services
│   │   ├── config_service.py
│   │   ├── logging_service.py
│   │   ├── cache/
│   │   │   └── cache_services.py
│   │   └── pipelines/
│   │       ├── bundle_service.py
│   │       ├── extract_service.py
│   │       ├── ftb_service.py
│   │       ├── kubejs_service.py
│   │       ├── lm_service.py
│   │       ├── lookup_service.py
│   │       ├── md_service.py
│   │       ├── merge_service.py
│   │       ├── _pipeline_logging.py
│   │       └── _task_runner.py
│   ├── ui/
│   │   ├── components.py
│   │   ├── theme.py
│   │   ├── view_wrapper.py
│   │   ├── keyboard_shortcuts.py
│   │   └── quick_jump.py
│   └── views/                           # 11 個 View + 子模組
│       ├── bundler_view.py
│       ├── cache_view.py
│       ├── cache_query_panel.py
│       ├── cache_shard_panel.py
│       ├── config_view.py
│       ├── extractor_view.py
│       ├── icon_preview_view.py
│       ├── lm_view.py
│       ├── lookup_view.py
│       ├── merge_view.py
│       ├── qc_view.py
│       ├── qc_base.py
│       ├── rules_view.py
│       ├── translation_view.py
│       ├── untranslated_checker.py
│       ├── cache_manager/                # 子模組：cache
│       ├── config/                      # 子模組：config
│       ├── extractor/                   # 子模組：extractor
│       ├── rules/                       # 子模組：rules
│       └── translation/                 # 子模組：translation
│
├── translation_tool/                    # 核心演算法層
│   ├── core/
│   │   ├── jar_processor.py             # JAR 提取
│   │   ├── jar_processor_discovery.py
│   │   ├── jar_processor_extract.py
│   │   ├── jar_processor_preview.py
│   │   ├── lang_merger.py               # 語言合併
│   │   ├── lang_merge_*.py              # 合併子流程
│   │   ├── lm_translator.py             # 翻譯主流程
│   │   ├── lm_translator_main.py
│   │   ├── lm_translator_*.py           # 翻譯子模組
│   │   ├── lm_api_client.py
│   │   ├── lm_config_rules.py
│   │   ├── lm_response_parser.py
│   │   ├── ftb_translator.py            # FTB 翻譯
│   │   ├── kubejs_translator.py          # KubeJS 翻譯
│   │   ├── md_translation_*.py           # Markdown 翻譯
│   │   ├── output_bundler.py            # 打包輸出
│   │   ├── icon_*.py                    # 圖示處理
│   │   └── lang_processing_format.py
│   ├── plugins/
│   │   ├── ftbquests/
│   │   ├── kubejs/
│   │   ├── md/
│   │   └── shared/
│   ├── checkers/
│   │   ├── untranslated_checker.py
│   │   ├── variant_comparator.py
│   │   ├── variant_comparator_tsv.py
│   │   └── english_residue_checker.py
│   └── utils/
│       ├── cache_manager.py
│       ├── cache_search.py               # SQLite FTS5 全文搜尋
│       ├── cache_search_facade.py
│       ├── cache_loader.py
│       ├── cache_store.py
│       ├── cache_shards.py
│       ├── cache_overview.py
│       ├── config_manager.py
│       ├── config_access.py
│       ├── text_processor.py
│       ├── species_cache.py
│       ├── exceptions.py
│       ├── log_unit.py
│       ├── safe_json_loader.py
│       └── ui_logging_handler.py
│
├── tests/                               # 834 個單元測試
├── docs/pr/
├── config.example.json
├── main.py
├── pyproject.toml
└── uv.lock
```

## 授權

MIT License
