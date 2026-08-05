# LM_VIEW_ARCHITECTURE.md

## 定位

LMView 是 **LM（Large Model）翻譯執行頁**，對已提取的 assets 資料夾進行批量 AI 翻譯。屬於翻譯管線的翻譯環節。

## 主要 UI 元件

| 元件 | 類型 | 說明 |
|------|------|------|
| `input_path` | TextField | 要翻譯的 assets 資料夾（必填） |
| `output_path` | TextField | 輸出資料夾（留空則用 `lm_translator.lm_translate_folder_name`，預設 `LM翻譯`） |
| `dry_run_switch` | Switch | Dry-run 模式：只分析、不送 API |
| `export_lang_checkbox` | Switch | 輸出 `.lang` 檔而非 `.json` |
| `write_new_cache_switch` | Switch | 每次 API 回傳單獨寫入一筆快取（**預設 False**） |
| `status_chip` | Chip | 狀態顯示（`_set_status(text, color)`） |
| `progress_bar` | ProgressBar | 進度條 |
| `log_view` | LogView | `mode="tail"`，tail_lines 由 `load_ui_logging_config()` 讀取（預設 250） |

## 呼叫鏈

```
start_clicked()
  ├─ 驗證 input_path 非空
  ├─ session = TaskSession(); session.start()
  ├─ output_dir = output_path 或 LM_translate_folder_name
  ├─ threading.Thread(run_lm_translation_service, args=(input, output, session, dry_run, export_lang, write_new_cache)).start()
  └─ start_ui_timer()

run_lm_translation_service()               [lm_service.py]
  ├─ ensure_pipeline_logging()             ← 重新讀 config 並設定 logger
  ├─ UI_LOG_HANDLER.set_session(session)
  ├─ dry_run → session.add_log("[DRY-RUN] ...")
  ├─ for update in lm_translate_gen(input, output, dry_run, export_lang, write_new_cache)
  │     ├─ GLOBAL_LOG_LIMITER.filter()     ← 高頻日誌過濾
  │     ├─ log → session.add_log / progress → session.set_progress / error → session.set_error
  ├─ session.finish()
  └─ finally: UI_LOG_HANDLER.set_session(None)
```

`lm_translate_gen` = `translation_tool.core.lm_translator.translate_directory_generator`（快取讀寫與 API 批次邏輯在 core 層）。

## UI Timer（start_ui_timer）

背景執行緒每 0.1 秒輪詢 `session.snapshot()`：
- `progress` → `progress_bar.value`
- `logs` → `log_view.sync_entries(logs)`（LogView 內部管理 tail 截斷）
- `status == "DONE"` → 「任務完成」（GREEN）；`"ERROR"` → 「任務發生錯誤」（RED）
- 終止條件：DONE/ERROR 或 page.update() 拋例外（設 `_ui_timer_running = False`）

## 與 cache 的關係

LMView 本身不直接操作 cache_manager，只透過 `write_new_cache_switch` 控制是否寫入快取；實際快取讀寫在 `lm_translator.py` 內部。

## 檔案結構

- `app/views/lm_view.py` — UI（約 335 行）
- `app/services_impl/pipelines/lm_service.py` — service 封裝（PR21 抽離）
- `translation_tool/core/lm_translator.py` — `translate_directory_generator`（核心翻譯流程）

## 維護注意

1. 目錄選擇回呼（`on_input_dir_picked` / `on_output_dir_picked`）接受帶 `.path` 的事件物件；`_async_pick_*` 會包 FakeEvent 觸發（FilePicker 相容層）。
2. `LM_translate_folder_name` 在 import 時從 config 讀取（模組層級常數）— 改設定需重啟。
3. 與 Extractor/Translation 頁共用同一套 TaskSession + UI timer 模式。
