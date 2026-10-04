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
| `cancel_button` | Button | 執行中可按；呼叫 `session.request_cancel()`，在下一個檢查點停止 |
| `progress_bar` | ProgressBar | 進度條 |
| `log_view` | LogView | `mode="tail"`，tail_lines 由 `load_ui_logging_config()` 讀取（預設 250） |

## 呼叫鏈

```
start_clicked()（event loop）
  ├─ 驗證 input_path 非空
  ├─ session = TaskSession(); session.start()
  ├─ output_dir = output_path 或 lm_translate_folder_name 設定值
  ├─ threading.Thread(run_lm_translation_service, args=(input, output, session, dry_run, export_lang, write_new_cache)).start()
  └─ start_ui_timer()

run_lm_translation_service()               [lm_service.py，worker thread]
  ├─ ensure_pipeline_logging()             ← 重新讀 config 並設定 logger
  ├─ session.start()；UI_LOG_HANDLER.set_session(session)
  ├─ dry_run → session.add_log("[DRY-RUN] ...")
  ├─ with cancel_scope(session.cancel_requested):
  │     for update in lm_translate_gen(..., should_cancel=session.cancel_requested)
  │       ├─ GLOBAL_LOG_LIMITER.filter()   ← 只節流 log / progress，error 等欄位保留
  │       ├─ log → session.add_log / progress → session.set_progress
  │       └─ error → session.set_error() 並結束
  ├─ session.finish()                      ← 已 ERROR 時維持 ERROR
  └─ finally: UI_LOG_HANDLER.set_session(None)
```

`lm_translate_gen` = `translation_tool.core.lm_translator.translate_directory_generator`（快取讀寫與 API 批次邏輯在 core 層）。

## UI 輪詢（start_ui_timer / _poll_session）

`start_ui_timer()` 以 `page.run_task(self._poll_session)` 在 **Flet event loop** 上執行輪詢 coroutine；worker thread 只寫 TaskSession，不直接碰 control。

每 `_POLL_INTERVAL_SEC`（0.2 秒）`await asyncio.sleep(...)` 一次，`_sync_from_session()`：
- `progress` → `progress_bar.value`
- `logs` → `log_view.sync_entries(logs)`（LogView 內部管理 tail 截斷）
- `status == "ERROR"` → 「任務發生錯誤」；`"DONE"` 且已要求取消 → 「已取消」；否則 → 「任務完成」
- 終止條件：DONE / ERROR（恢復按鈕），或 `page.update()` 拋 RuntimeError（頁面已關閉；背景任務照常完成）

## 取消

- `cancel_clicked` → `session.request_cancel()`；service 以 `cancel_scope(session.cancel_requested)` 讓 core 看得到取消（一鍵流水線另有外層 scope，巢狀為 OR）。
- **檢查點**：
  - 批次之間：`translate_directory_generator` 每批開始前檢查 `should_cancel()` / `is_cancelled()`。
  - 等待中：所有限流 / 重試等待都用 `interruptible_sleep()`，取消時拋 `TaskCancelled`，在翻譯迴圈攔截。
- **已翻譯的批次保留**：取消後跳出迴圈，照常走收尾流程寫出已完成的檔案與對照表，最後一筆日誌為「⏹ 翻譯已取消，完成 N/M 筆」。session 以 `finish()` 結束（DONE），UI 依 `cancel_requested` 顯示「已取消」。
- 目前進行中的 API 請求不會被中斷；取消在該請求回來後的下一個檢查點生效。

## 與 cache 的關係

LMView 本身不直接操作 cache_manager，只透過 `write_new_cache_switch` 控制是否寫入快取；實際快取讀寫在 `lm_translator.py` 內部。

## 核心翻譯邏輯（`translate_directory_generator`）

流程階段與進度：
1. **初始化**（progress 0.0）：`validate_api_keys()` → `reload_translation_cache()`（每次重新讀取分片，手動改快取立即生效）
2. **掃描**（0.0）：`scan_translatable_files` 分出 patchouli / lang（掃描失敗僅 warn，不中斷）
3. **抽取**（0.0→0.2）：`extract_items_parallel` 並行抽取，每完成 5% 檔案 yield 一次
4. **Cache 命中比對**（0.2）：分流兩類，`value_fully_translated()` 判定完整譯文
5. **批次翻譯**：`translate_items_with_cache_loop`（shared_loop）→ `translate_batch_smart`（main）→ 寫回輸出檔

### Cache 命中判定（lang vs patchouli key 不同）

| 類型 | Key | 命中條件 |
|------|-----|----------|
| patchouli | `{path}\|{source_text}` | `dst` 存在 + `value_fully_translated(dst)` |
| lang | `path`（無 src 複合） | `dst` 存在 + `value_fully_translated(dst)` **且 `entry_src == src_text`**（src 一致才命中） |

- patchouli 無 `source_text` → 直接當待翻譯
- 命中 → `item["text"] = cached_value`，不送 API

### 批次與速率限制（`lm_translator_main.py`）

- 常數：`RPM_COOLDOWN_SEC=0`、`OVERLOAD_RETRY_WAIT_SEC=12`、`MIN_LANG_BATCH_SIZE=20`、`DEFAULT_BATCH_SIZE=50`、`DEFAULT_DRY_RUN=False`
- 等待秒數可由 `config.json` 的 `lm_translator` 覆寫：`rpm_cooldown_sec`（預設 0；無效值退回預設）、`overload_retry_sec`、`key_rotation_buffer_sec`、`request_interval_sec`
- **各 cache_type 預設批次**（`_get_default_batch_size`）：ftbquests=100、kubejs=200、patchouli=100、md=100、lang=300（可被 config `initial_batch_size_*` 覆寫）
- 每批結束 `batch_size = min(batch_size, remaining_count)` 動態縮小；一個 batch 全部完成且 `rpm_cooldown_sec > 0` 時 `interruptible_sleep(rpm_cooldown_sec)`（免費層 RPM 保護；預設 0 = 不等待）
- 所有等待（冷卻、429 retry_after、overload 重試、換 Key 緩衝）都用 `interruptible_sleep()`，可被取消打斷；不要在翻譯流程中使用 `time.sleep()`
- 批次內也支援縮小（400 或 429 相關錯誤 → break 交給 batch shrink）

### 錯誤處理與 Key 輪替（`translate_batch_smart`）

- **只有 503（overloaded）才累積 overload 計數**，且**逐把 key** 計數（`lm_config_rules` 的 `record_overload()` / `clear_overload()`）：不同 key 的 overload 不互相累加；同一把 key 累積到門檻（`overload_threshold`，預設 3，見 `lm_batch_actions`）才對它 `mark_failed()` 並換 key（`BatchAction.ROTATE_KEY`），成功後 `reset()` 開始新的 cycle
- **404** → 模型不存在，跳過該模型
- **403** → Key 無權限，`rotate_api_key`，無 Key 可換 → RuntimeError「所有 API Key 均無權限」
- **400** FAILED_PRECONDITION → 此地區未啟用 Gemini 免費方案；否則縮小 batch
- **429 RESOURCE_EXHAUSTED** → 解析 Quota ID（RPD/RPM）→ RPM 依 API 回傳的 `retry_after` 等待（無值時 10 秒）、RPD 換 Key；所有 Key 耗盡回傳 `"ALL_KEYS_EXHAUSTED"`。此等待與 `rpm_cooldown_sec` 無關，冷卻設為 0 時仍會依 API 要求等待

## 檔案結構

- `app/views/lm_view.py` — UI（約 335 行）
- `app/services_impl/pipelines/lm_service.py` — service 封裝（PR21 抽離）
- `translation_tool/core/lm_translator.py` — `translate_directory_generator`（核心翻譯流程）

## 維護注意

1. 目錄選擇回呼（`on_input_dir_picked` / `on_output_dir_picked`）接受帶 `.path` 的事件物件；`_async_pick_*` 會包 FakeEvent 觸發（FilePicker 相容層）。
2. 輸出資料夾名稱由 `get_lm_translate_folder_name()`（`app/views/lm_view.py`）讀取 `lm_translator.lm_translate_folder_name`，下次任務套用（見 `CONFIG_APPLY_TIMING.md`）。
3. 與 Extractor/Translation 頁共用同一套 TaskSession + event loop 輪詢模式；背景執行緒不可直接修改 control 或呼叫 `page.update()`。
4. 翻譯流程新增等待時使用 `interruptible_sleep()`；新增長迴圈時在迭代之間檢查 `is_cancelled()`。
