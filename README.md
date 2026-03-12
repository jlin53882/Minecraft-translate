# Minecraft 模組包繁體化工具（Flet）

基於 [Flet](https://flet.dev/) 的桌面應用程式，用於將 Minecraft 模組包從簡體中文／英文批量翻譯為繁體中文（台灣用語）。

## 目前狀態（2026-03-12）

- ✅ `app.services` 已收斂為 **QC/checkers 暫緩線 façade**（PR29）
- ✅ 主線 caller 已完成遷移到 `app.services_impl.*`（PR28a + PR28b）
- ✅ 最近一次完整測試記錄：`40 passed`

> 詳細變更請看 `docs/pr/2026-03-12_0204_PR_pr28a-low-risk-caller-migration-design.md`、
> `docs/pr/2026-03-12_0205_PR_pr28b-high-risk-caller-migration-design.md`、
> `docs/pr/2026-03-12_1003_PR_pr29-services-facade-non-qc-cleanup.md`。

## UI 對照（啟用狀態，依 `main.py`）

### 已啟用（目前會出現在左側選單）
- `config_view.py`：設定
- `rules_view.py`：規則
- `cache_view.py`：快取管理
- `translation_view.py`：任務翻譯工具（FTB / KubeJS / Markdown）
- `extractor_view.py`：JAR 提取
- `lm_view.py`：機器翻譯
- `merge_view.py`：檔案合併

### 未啟用（檔案存在，但目前未掛到 `nav_destinations`）
- `bundler_view.py`：打包成品 ZIP
- `lookup_view.py`：學名查詢
- `icon_preview_view.py`：圖示映對翻譯
- `qc_view.py`：品質檢查

> 註：未啟用代表「目前 UI 主選單不顯示」，不代表功能檔案已刪除。

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

### 1) Service 分層（最新）

- `app/services_impl/*`：主線 canonical services（config/cache/pipelines）
- `app/services.py`：目前僅保留 QC/checkers 所需 façade

也就是說，除了 QC/checkers 暫緩線，其他流程都應直接 import `app.services_impl.*`。

### 2) 核心處理邏輯

- `translation_tool/core/lang_merger.py`
  - 依 `en_us.json` / `zh_cn.json` / `zh_tw.json` 組合決定合併策略
  - JSON 語言檔採增量更新；內容檔（`.md`、`.png`、`.snbt`）採覆寫策略
- `translation_tool/core/lm_translator_main.py`
  - 批次送 Gemini，依錯誤類型做縮批、重試、換 Key、節流

### 3) UI 頁面檔案索引（`app/views`）

- ✅ `config_view.py`：Config 設定（已啟用）
- ✅ `rules_view.py`：替換規則（已啟用）
- ✅ `cache_view.py`：快取處理 / 檢視（已啟用）
- ✅ `translation_view.py`：FTB / KubeJS / Markdown 翻譯（已啟用）
- ✅ `extractor_view.py`：JAR 抽取（已啟用）
- ✅ `lm_view.py`：機器翻譯（已啟用）
- ✅ `merge_view.py`：`zh_cn` / `zh_tw` / `en_us` 合併（已啟用）
- ⏸ `bundler_view.py`：打包成品 ZIP（未啟用）
- ⏸ `lookup_view.py`：學名查詢（未啟用）
- ⏸ `icon_preview_view.py`：圖示映對翻譯（未啟用）
- ⏸ `qc_view.py`：品質檢查（未啟用）

## 專案結構

```text
├── app/
│   ├── services.py                      # QC façade：run_untranslated_check_service / run_variant_compare_service / run_english_residue_check_service / run_variant_compare_tsv_service
│   ├── services_impl/                   # 主線 canonical services（非 QC 流程都走這層）
│   │   ├── config_service.py            # load_config_json / save_config_json / load_replace_rules / save_replace_rules
│   │   ├── logging_service.py           # update_logger_config（重建 logger 與 handler）
│   │   ├── cache/
│   │   │   └── cache_services.py        # cache_get_overview / reload / save_all / search / get_entry / update_dst / rotate / rebuild_index
│   │   └── pipelines/
│   │       ├── bundle_service.py        # run_bundling_service（打包輸出 ZIP）
│   │       ├── extract_service.py       # run_lang_extraction_service / run_book_extraction_service
│   │       ├── ftb_service.py           # run_ftb_translation_service
│   │       ├── kubejs_service.py        # run_kubejs_tooltip_service
│   │       ├── lm_service.py            # run_lm_translation_service
│   │       ├── lookup_service.py        # run_manual_lookup_service / run_batch_lookup_service
│   │       ├── md_service.py            # run_md_translation_service
│   │       └── merge_service.py         # run_merge_zip_batch_service
│   ├── ui/                              # 共用 UI 元件
│   └── views/
│       ├── cache_manager/               # 快取管理子模組（controller/presenter/panel）
│       ├── bundler_view.py              # 打包成品 ZIP
│       ├── cache_view.py                # 快取處理 / 檢視
│       ├── config_view.py               # Config 設定
│       ├── extractor_view.py            # JAR 抽取
│       ├── icon_preview_view.py         # 圖示映對翻譯
│       ├── lm_view.py                   # 機器翻譯
│       ├── lookup_view.py               # 學名查詢
│       ├── merge_view.py                # zh_cn/zh_tw/en_us 合併
│       ├── qc_view.py                   # 品質檢查
│       ├── rules_view.py                # 替換規則
│       └── translation_view.py          # FTB/KubeJS/Markdown 翻譯
│
├── translation_tool/                    # 核心演算法層
│   ├── core/
│   │   ├── jar_processor.py             # extract_lang_files_generator / extract_book_files_generator / preview_extraction_generator
│   │   ├── lang_merger.py               # merge_zhcn_to_zhtw_from_zip / export_filtered_pending
│   │   ├── lm_translator_main.py        # call_gemini_requests / translate_batch_smart（批次翻譯主流程）
│   │   ├── lm_translator.py             # translate_directory_generator（翻譯迴圈）
│   │   ├── output_bundler.py            # bundle_outputs_generator
│   │   ├── ftb_translator.py            # run_ftb_pipeline（FTB 三步流程）
│   │   ├── kubejs_translator.py         # run_kubejs_pipeline（KubeJS 三步流程）
│   │   ├── md_translation_assembly.py   # run_md_pipeline（Markdown 三步流程）
│   │   ├── icon_*.py                    # 圖示分類/解析/預覽快取
│   │   ├── lang_processing_format.py    # OpenCC 轉換與文字格式處理
│   │   └── lm_config_rules.py           # API key 輪替、可翻譯欄位與規則判定
│   ├── plugins/
│   │   ├── ftbquests/                   # SNBT 抽取/注入 + FTB JSON 翻譯
│   │   │   ├── ftbquests_snbt_extractor.py
│   │   │   ├── ftbquests_snbt_inject.py
│   │   │   └── ftbquests_lmtranslator.py
│   │   ├── kubejs/                      # Tooltip 抽取/注入 + JSON 翻譯
│   │   │   ├── kubejs_tooltip_extract.py
│   │   │   ├── kubejs_tooltip_inject.py
│   │   │   └── kubejs_tooltip_lmtranslator.py
│   │   └── md/                          # Markdown 區塊抽取/回填 + JSON 翻譯
│   │       ├── md_extract_qa.py
│   │       ├── md_inject_qa.py
│   │       └── md_lmtranslator.py
│   ├── checkers/                        # 品質檢查
│   │   ├── untranslated_checker.py      # check_untranslated_generator
│   │   ├── variant_comparator.py        # compare_variants_generator
│   │   ├── variant_comparator_tsv.py    # compare_variants_tsv_generator
│   │   └── english_residue_checker.py   # check_english_residue_generator
│   └── utils/                           # 共用基礎工具
│       ├── config_manager.py            # load_config / save_config / setup_logging
│       ├── text_processor.py            # OpenCC + replace_rules 套用
│       ├── cache_manager.py             # initialize/reload/save/search cache
│       ├── cache_search.py              # 快取索引與查詢
│       ├── species_cache.py             # Wikipedia 學名快取
│       ├── exceptions.py                # 統一錯誤處理 decorator
│       └── log_unit.py                  # progress / log_* 封裝
│
├── tests/
├── docs/pr/
├── config.example.json
├── main.py
├── pyproject.toml
└── uv.lock
```

## 授權

MIT License
