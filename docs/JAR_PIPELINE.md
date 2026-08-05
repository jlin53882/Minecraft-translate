# JAR 處理流程

## 目的
從 Minecraft mod JAR 檔案中取出語言檔（lang / Patchouli / FTB / KubeJS）。

## 流程
```
1. Discovery — 掃描 mods 目錄找出所有 .jar
2. Extract  — 從 JAR 中抽出語言檔
3. Preview  — 可選：預覽即將翻譯的內容
```

## 各階段說明

### 1. Discovery
- **入口函式**：`find_jar_files(folder_path)` (`jar_processor_discovery.py`)
- **行為**：遞迴走訪資料夾，回傳所有 `.jar` 檔案的絕對路徑列表
- **輸出**：`List[str]` — JAR 絕對路徑清單

### 2. Extract（核心實作：`jar_processor_extract.py`）
- **單一 JAR**：`extract_from_jar_impl(jar_path, output_root, target_regex)`
  - 用 `zipfile` 開啟 JAR，比對正則表達式取出檔案
  - 支援 `assets/` 結構與非 assets 兩種輸出方式
  - SHA-256 hash 相同者自動跳過（增量更新）
- **批量執行**：`run_extraction_process_impl(...)`
  - 使用 `ThreadPoolExecutor` 並行處理（預設 `cpu_count // 2` 執行緒）
  - Generator 模式，逐個 yield 進度（0.0~1.0）
- **兩種提取模式**（`jar_processor.py`）：
  - `extract_lang_files_generator()` — 提取 `lang/en_us.json` 等語言檔
  - `extract_book_files_generator()` — 提取 Patchouli Book 結構

### 3. Preview（`jar_processor_preview.py`）
- **入口**：`preview_extraction_generator_impl(mods_dir, mode, ...)`
- **行為**：只掃描不寫入，回傳每個 JAR 的檔案數、大小與路徑清單
- **輸出格式**：`ExtractionSummary` 結構（success / warnings / failures）
- **可選產出**：`generate_preview_report(result, mode, output_path)` → Markdown 報告

## 資料流
```
mods/ → [Discovery: find_jar_files] → JAR列表
       → [Extract: ThreadPoolExecutor] → lang檔 / book檔 → 寫入 output/
       → [Preview: 只掃描不回寫]     → 預覽報告
```

## Regex 說明
- **Lang**：`rf"(?:assets/([^/]+)/)?lang/({codes_str})\.(json|lang)$"`（動態讀取 config）
- **Book**：`BOOK_PATH_REGEX_DUAL_STRUCTURE` — 匹配 `assets/data/*/patchouli_books|book|manual|guidebook/`

## 與 ExtractorView 的關係
`ExtractorView`（`app/views/extractor_view.py`）是 UI 層：

| 元件 | 職責 |
|------|------|
| `extractor_view.py` | 按鈕 / 路徑欄位 / 入口分派（`_handle_*_click`） |
| `extractor_dialog.py` | `open_extractor_dialog()` / `open_preview_dialog()` — 進度+日誌+統計 |
| `extractor_panels.py` | 左側設定面板組合（路徑 + 動作區） |
| `extract_service.py` | Service 層：路徑準備 + `run_extraction_loop` 驅動 generator |

ExtractorView 不直接操作核心模組。詳細架構（含狀態管理 / 取消機制 / dual 統計 / 常見陷阱）見 [`EXTRACTOR_VIEW_ARCHITECTURE.md`](./EXTRACTOR_VIEW_ARCHITECTURE.md)。
