# 機器翻譯（`lm`）處理流程

> 入口：`app/views/lm_view.py` 的 `LMView`。詳細元件說明見 [`View 架構文件`](../developer-guide/views/LM_VIEW_ARCHITECTURE.md)。

## 職責

- 讓使用者選擇輸入／輸出資料夾，並設定本次任務選項（Dry-run、輸出 `.lang`、每次寫入新快取分片（預設關閉，寫入編號分片）、Mod 資料庫）。
- 按下開始後，透過 `launch_page_operation` 把 `run_lm_translation_service`（`app/services_impl/pipelines/lm_service.py`）放到背景執行緒執行。
- 服務層呼叫 `translation_tool/core/lm_translator.py` 的 `translate_directory_generator`，負責掃描、Mod 資料庫／翻譯快取分流、批次 AI 翻譯與輸出。
- 以 `TaskSession` 的 `snapshot()` 輪詢進度與日誌，右側統計卡顯示進度、已用時間、AI 認證狀態（金鑰健康或 ChatGPT 連線）與快取寫入頻率。
- 支援續跑：啟動時偵測到中斷任務後，由 `resume_interrupted(task)` 還原上次的輸入與選項。

## 主要流程

### 開始翻譯（UI 執行緒）

```mermaid
flowchart TD
  A["按下開始翻譯（start_clicked）"] --> B{"已有任務執行中？"}
  B -- 是 --> B1["提示：翻譯正在執行中"]
  B -- 否 --> C{"輸入資料夾為空？"}
  C -- 是 --> C1["狀態列：請先選擇輸入資料夾"]
  C -- 否 --> D["建立 TaskSession（tag_session）"]
  D --> E["讀取預設輸出名稱與日誌行數"]
  E --> F["鎖定開始按鈕、啟用取消按鈕"]
  F --> G["取得 DB 快照（_db_snapshot_for_run）"]
  G --> H{"launch_page_operation 成功？"}
  H -- 否 --> H1["提示：應用程式正在關閉"]
  H -- 是 --> I["start_ui_timer 啟動輪詢（_poll_session）"]
```

### 服務與核心翻譯（背景執行緒）

```mermaid
flowchart TD
  A["run_lm_translation_service（lm_service.py）"] --> B["session.start()"]
  B --> C["translate_directory_generator（lm_translator.py）"]
  C --> D["validate_api_keys 與 reload_translation_cache"]
  D --> E["解析 DB 選項（_resolve_directory_db_settings）"]
  E --> F["掃描可翻譯檔案（_scan_directory_files）"]
  F --> G["抽取文字項目（_extract_directory_items）"]
  G --> H["計算 checkpoint 指紋"]
  H --> I["Mod 資料庫命中（_split_db_cache / split_db_hits）"]
  I --> J["翻譯快取命中（_apply_cached_directory_items）"]
  J --> K{"Dry-run？"}
  K -- 是 --> L["寫出預覽（_write_directory_previews）"]
  K -- 否 --> M["run_translator_skeleton 逐批翻譯"]
  M --> N["批次寫出譯文、快取與 checkpoint"]
  N --> O["session.finish()"]
```

### 單批翻譯狀態機（`lm_translator_main.py`）

```mermaid
flowchart TD
  A["translate_batch_smart"] --> B["_run_batch_state_machine"]
  B --> C["_prepare_batch 組出本輪批次"]
  C --> D["_attempt_batch 呼叫 API"]
  D --> E{"BatchAction"}
  E -- "重試 / 換金鑰 / 換模型" --> C
  E -- "SHRINK_BATCH" --> F["依 batch_shrink_factor 縮小批次"]
  F --> C
  F -- "已達最小批次仍失敗" --> G["回填原文並標記 _untranslated"]
  G --> C
  E -- "EXHAUSTED" --> H["回傳 ALL_KEYS_EXHAUSTED"]
  E -- "FAIL" --> I["回傳 FAILED"]
  E -- "PARTIAL" --> J["回傳 PARTIAL"]
```

## 背景任務與 UI 更新

- 開始時以 `launch_page_operation(..., owner="lm")` 登錄任務，使用預設的 `CancellationPolicy.COOPERATIVE` 與 `ShutdownPolicy.CANCEL_AND_DRAIN`（定義於 `app/tasks/operation_registry.py`）。
- 背景執行緒只寫入 `TaskSession`（`add_log`、`set_progress`）。UI 不會從執行緒直接更新，而是由 `_poll_session` 在 Flet event loop 上以 `PollerHandle` 每 0.2 秒呼叫 `_sync_from_session`，同步進度條、`LogView.sync_entries` 日誌與統計卡。
- 任務結束（狀態 `DONE` 或 `ERROR`）時，輪詢停止、按鈕復位；`DONE` 且已請求取消時顯示「已取消」。
- 取消：`cancel_clicked` 呼叫 `session.request_cancel()`，狀態改為「取消中…」。取消在三個位置檢查：`cancel_scope`（等待 API 限流時可打斷）、`translate_batch` 的 `should_cancel`、以及共用迴圈在批次邊界的 `is_cancelled()`。已完成的批次照常保留。
- 換頁卸載：`will_unmount` 只停止輪詢，任務繼續執行；`did_mount` 若任務仍在追蹤中，重新啟動輪詢。

## 狀態與資料

- 頁面狀態：`session`、`_ui_timer_running`、`_poller`、`_started_at`、`_resume_db_settings_snapshot`。
- 控制項：`input_path`、`output_path`、`dry_run_switch`、`export_lang_checkbox`、`write_new_cache_switch`，以及 `lm_db_options`（`app/views/moddb/lm_db_options.py` 的 `LmDbOptions`）。
- 讀取的設定：
  - `lm_translator.lm_translate_folder_name`：未填輸出時的預設資料夾名（預設 `LM翻譯後`）。
  - `lm_translator.batch_write_interval`：快取寫入頻率。
  - `lm_translator.provider`：決定統計卡顯示 ChatGPT 或金鑰健康。
  - `translator.parallel_execution_workers`：抽取時的並行數。
  - `lm_translator.rate_limit.sleep_seconds_between_batches`：批次之間的等待秒數。
  - `lm_translator` 區段的 `batch_shrink_factor`、`min_batch_size`：批次縮小參數。
  - UI 日誌行數（`load_ui_logging_config` 的 `tail_lines`）。
- 產出檔案：
  - 輸出資料夾中的譯文 JSON；`lang/en_us.json` 寫成同資料夾的 `zh_tw.json`，Patchouli 書的 `patchouli_books/<書>/en_us/…` 寫進同書的 `zh_tw/…`（`translation_path_writer.map_lang_output_path`）；勾選「輸出 .lang」時，路徑含 `lang` 的來源檔另外輸出同名 `.lang`。另有 `translation_map.json`（逐筆對照紀錄）。Patchouli 書寫出前會與來源 `en_us` 比對（`patchouli_compat.find_patchouli_mismatch`），結構或格式標記數量不同時記警告，檔案仍照常寫出。
  - 翻譯快取（`translation_tool/utils/cache_manager.py`）：譯文一律寫入；`write_new_cache` 決定寫入方式：關閉時寫入編號分片，開啟時每批另存成獨立的時間戳分片。
  - Checkpoint：`get_data_root()/logs/translation_checkpoint.json`。只有 `translator.enable_cache_saving`（預設開啟）為真時才寫入；整批 `DONE` 且處理完畢才清除，中斷時保留供續跑使用。
  - Mod 資料庫寫回（`translation_tool/core/lm_translator_db.py` 的 `flush_write_back`）。
- 續跑：`resume_interrupted(task)` 還原輸入、輸出、Dry-run 與 `.lang` 設定；Mod 資料庫設定依任務內的快照還原，不採用目前頁面值。若快照缺失但任務使用了資料庫，會顯示提示並停止續跑。

## 錯誤處理

- 啟動前檢查：輸入為空、已有任務執行中、應用程式正在關閉，都只以狀態列或 snackbar 提示，不會啟動任務。
- 服務例外（`run_lm_translation_service`）：完整堆疊經 `mirror_session_log` 寫入後台與任務日誌（`level="error"`），呼叫 `session.set_error()`，最後在 `finally` 中清除 `UI_LOG_HANDLER` 並呼叫 `session.finish()`。畫面顯示「任務發生錯誤」。
- 批次層錯誤：`_classify_batch_error` 依錯誤類型決定換金鑰、換模型、重試或縮小批次；Gemini 503 與 ChatGPT 額度錯誤各有專用處理（`_handle_service_unavailable_error`、`_handle_chatgpt_api_error`）。
- 共用迴圈（`lm_translator_shared_loop.py`）的終態：
  - `ALL_KEYS_EXHAUSTED`：停止，訊息為「翻譯中斷」。
  - `FAILED`（含快取寫入失敗、checkpoint 寫入失敗、整批無任何成果）：停止並在畫面顯示一行摘要，堆疊寫入後台。
  - `CANCELLED`：訊息為「翻譯已取消，完成 x/y 筆」。
- 損毀的 checkpoint 會被改名為 `.corrupt` 隔離，並記錄警告，視為沒有 checkpoint。
- 後台 log 使用 `translation_tool/utils/log_unit.py` 的 `log_info`、`log_warning`、`log_error`。畫面日誌透過 `UI_LOG_HANDLER` 轉入任務 session。
- 統計卡的金鑰健康讀取若失敗，`refresh_key_stat` 會靜默略過該卡片的更新，不影響翻譯。
