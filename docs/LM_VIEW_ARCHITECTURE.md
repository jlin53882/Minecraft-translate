# LM_VIEW_ARCHITECTURE.md

## 定位

LMView 是 **LM（Large Model）翻譯執行頁**，對已提取的 assets 資料夾進行批量 AI 翻譯。屬於翻譯管線的翻譯環節。

## 主要 UI 元件

版面：`kit.page_header`「機器翻譯」（右側 actions 為 `cancel_button`、`start_button`）下方分左右兩欄：

- 左欄（`expand=5`）：「翻譯設定」卡片（輸入/輸出路徑列、三個 `kit.SwitchRow`、`batch_interval_info`）
- 右欄（`expand=8`）：四張統計卡一列（`stat_progress`、`stat_elapsed`、`stat_keys`、`stat_cache`）→「執行狀態」卡片（`status_chip` + `progress_bar`）→「執行日誌」卡片（`log_view`）

| 元件 | 類型 | 說明 |
|------|------|------|
| `input_path` | `kit.text_field` | 要翻譯的 assets 資料夾（必填） |
| `output_path` | `kit.text_field` | 輸出資料夾（留空則用 `get_lm_translate_folder_name()`，即設定 `lm_translator.lm_translate_folder_name`，預設 `LM翻譯後`） |
| `dry_run_switch` | Switch（`SwitchRow.switch`） | Dry-run 模式：只分析、不送 API（預設關） |
| `export_lang_checkbox` | Switch（`SwitchRow.switch`） | 輸出 `.lang` 檔而非 `.json`（預設關） |
| `write_new_cache_switch` | Switch（`SwitchRow.switch`） | 每次 API 回傳單獨寫入一筆快取（UI 預設關） |
| `batch_interval_info` | Text | 顯示 `lm_translator.batch_write_interval` 目前值（`get_batch_write_interval()`） |
| `stat_progress` / `stat_elapsed` | `kit.stat_card` | 進度百分比 / 已用時間（`format_elapsed`） |
| `stat_keys` | `kit.stat_card` | 可用 API Key 數（`refresh_key_stat()`，經 `summarize_keys(get_key_health_snapshot())`，讀不到設定時不更新） |
| `stat_cache` | `kit.stat_card` | 快取寫入頻率（每 N 批次寫入一次） |
| `status_chip` | Chip | 狀態顯示（`_set_status(text, tone)`，tone 為色組名稱） |
| `start_button` / `cancel_button` | `kit.button` | 執行中 `_set_running(True)`：停用開始、啟用取消；`cancel_clicked` 呼叫 `session.request_cancel()` |
| `progress_bar` | `kit.progress_bar` | 進度條 |
| `log_view` | LogView | `mode="tail"`，`tail_lines` 由 `load_ui_logging_config()` 讀取（預設 250），每次開始任務時以 `set_tail_lines` 套用最新設定 |

## 呼叫鏈

```
start_clicked()（event loop）
  ├─ 已在執行中（_ui_timer_running）→ 提示並返回，避免重複啟動
  ├─ 驗證 input_path 非空
  ├─ session = tag_session(TaskSession(), "機器翻譯", "lm"); session.start()
  ├─ output_dir = output_path 或 get_lm_translate_folder_name()
  ├─ threading.Thread(run_lm_translation_service, args=(input, output, session, dry_run, export_lang, write_new_cache)).start()
  └─ start_ui_timer()

run_lm_translation_service()               [lm_service.py，worker thread]
  ├─ ensure_pipeline_logging()             ← 重新讀 config 並設定 logger
  ├─ session.start()；UI_LOG_HANDLER.set_session(session)
  ├─ dry_run → session.add_log("[DRY-RUN] ...")
  ├─ with cancel_scope(是否已要求取消的 callable):
  │     for update in lm_translate_gen(..., should_cancel=同一個 callable)
  │       ├─ GLOBAL_LOG_LIMITER.filter()   ← 只節流 log / progress，error 等欄位保留
  │       ├─ log → session.add_log / progress → session.set_progress
  │       └─ error → session.set_error() 並結束
  ├─ 迭代結束後 GLOBAL_LOG_LIMITER.flush()，再 session.finish()（已 ERROR 時維持 ERROR）
  └─ finally: UI_LOG_HANDLER.set_session(None)
```

`lm_translate_gen` = `translation_tool.core.lm_translator.translate_directory_generator`（快取讀寫與 API 批次邏輯在 core 層）。service 層的 `write_new_cache` 參數預設 True，但 `LMView` 一律明確傳入 `write_new_cache_switch` 的值；generator 自身預設為 False。

## UI 輪詢（start_ui_timer / _poll_session）

`start_ui_timer()` 經 `PollerHandle`（`app/ui/poller.py`）以 `page.run_task` 在 **Flet event loop** 上執行 `_poll_session(alive)` coroutine；worker thread 只寫 TaskSession，不直接碰 control。

**Lifecycle（#114）**：輪詢由 View 的 `self._poller` 持有（保存 Future）。`will_unmount()`（換頁／關閉）呼叫 `_poller.stop()`——可重複呼叫，`alive()` 立刻變 False，輪詢不會再多跑一輪去碰已卸載的控制項；翻譯任務本身照常執行（`_ui_timer_running` 保留）。`did_mount()` 在任務仍被追蹤時重新啟動輪詢（`start()` 會先停止舊的，所以重複 mount 只剩一個輪詢；任務在卸載期間結束時，第一次同步就補上最終狀態與按鈕）。

每 `_POLL_INTERVAL_SEC`（0.2 秒）`await asyncio.sleep(...)` 一次，`_sync_from_session()`：
- `progress` → `progress_bar.value`，並由 `_update_stats` 更新進度 / 已用時間 / Key 統計卡
- `logs` → `log_view.sync_entries(logs, update=False)`（LogView 內部管理 tail 截斷；最後統一 `page.update()`）
- `status == "ERROR"` → 「任務發生錯誤」；`"DONE"` 且已要求取消 → 「已取消」；否則 → 「任務完成」
- 終止條件：DONE / ERROR（恢復按鈕）、`page.update()` 拋 RuntimeError（頁面已關閉；背景任務照常完成），或輪詢被 `stop()`（unmount）

## 取消

- `cancel_clicked` → `session.request_cancel()`，狀態顯示「取消中…」；service 以 `cancel_scope(...)` 讓 core 看得到取消（一鍵流水線另有外層 scope，巢狀為 OR）。
- **檢查點**：
  - 批次之間：共用迴圈 `translate_items_with_cache_loop` 每批開始前檢查 `is_cancelled()`；傳給 `translate_batch_smart` 的包裝函式也會檢查 `should_cancel()`，已取消則拋 `TaskCancelled`。
  - 等待中：所有限流 / 重試等待都用 `interruptible_sleep()`，取消時拋 `TaskCancelled`，在翻譯迴圈攔截。
- **已翻譯的批次保留**：取消後跳出迴圈，照常走收尾流程寫出已完成的檔案與對照表（`translation_map.json`），最後一筆日誌為「⏹ 翻譯已取消，完成 N/M 筆」（由 `_directory_final_message` 產生）。session 以 `finish()` 結束（DONE），UI 依 `cancel_requested` 顯示「已取消」。
- 目前進行中的 API 請求不會被中斷；取消在該請求回來後的下一個檢查點生效。

## 與 cache 的關係

LMView 本身不直接操作 cache_manager，只透過 `write_new_cache_switch` 控制是否寫入快取；實際快取讀寫在 `lm_translator.py` 與共用迴圈 `lm_translator_shared_loop.py` 內部。

## 核心翻譯邏輯（`translate_directory_generator`）

流程階段與進度：
1. **初始化**（progress 0.0）：`validate_api_keys()` → `reload_translation_cache()`（每次重新讀取分片，手動改快取立即生效）；建立輸出資料夾
2. **掃描**（0.0）：`scan_translatable_files` 分出 patchouli / lang（掃描失敗僅 warn，不中斷；找不到任何 JSON 檔直接結束）
3. **抽取**（0.0→0.2）：`extract_items_parallel` 並行抽取（worker 數取自 `translator.parallel_execution_workers`），進度每增加 5% 才 yield 一次
4. **Cache 命中比對**（0.2）：`_split_directory_items` 分流，命中項目先寫出（dry-run 不寫）
5. **dry-run**：只輸出 `_dry_run_preview.json` 與 `_dry_run_cache_hit_preview.json` 後結束（progress 1.0）
6. **checkpoint（未完成標記，#151）**：指紋涵蓋全部抽取項目（快取分流之前）；`_note_directory_checkpoint` 比對指紋，相符記錄「接續上次」、不符或舊格式則明確警告並捨棄。**不依位置跳過項目**——已完成批次的譯文在翻譯快取，快取分流自然把它們當命中寫回輸出。每批「快取落盤 → 輸出寫入 → `save_checkpoint`（原子寫入＋fsync）」，完整完成後 `clear_checkpoint`；`translator.enable_cache_saving` 關閉時不寫 checkpoint（沒有可還原的來源）
7. **批次翻譯**：`run_translator_skeleton` → `translate_items_with_cache_loop`（`lm_translator_shared_loop.py`）→ `translate_batch_smart`（`lm_translator_main.py`）→ 每批寫回輸出檔，結束時寫 `translation_map.json`

### Cache 命中判定（lang vs patchouli key 不同）

| 類型 | Key | 命中條件 |
|------|-----|----------|
| patchouli | `{path}\|{source_text}` | `dst` 存在 + `value_fully_translated(dst)` |
| lang | `path`（無 src 複合） | `dst` 存在 + `value_fully_translated(dst)` **且 cache 的 `src` 等於當前原文**（原文為空則不命中） |

- 命中 → `item["text"] = cached_value`，不送 API

### 批次與速率限制

- `lm_translator_main.py` 常數：`RPM_COOLDOWN_SEC=0`、`OVERLOAD_RETRY_WAIT_SEC=12`、`OVERLOAD_KEY_SWITCH_THRESHOLD=3`、`MIN_LANG_BATCH_SIZE=20`、`DEFAULT_DRY_RUN=False`
- 等待秒數可由 `config.json` 的 `lm_translator` 覆寫：`rpm_cooldown_sec`（無效值退回預設）、`overload_retry_sec`、`key_rotation_buffer_sec`（預設 5）、`request_interval_sec`（預設 4）。其中只有 `rpm_cooldown_sec` 在設定頁有欄位，其餘需手動編輯 `config.json`
- **每批項目數上限**：共用迴圈以 `_get_default_batch_size`（`lm_translator_shared_loop.py`）依 cache_type 讀 `initial_batch_size_*`（ftbquests / kubejs / patchouli / md / lang），再由 `select_batch_size` 依 token 預算取前綴；`translate_batch_smart` 內另以 `_build_batch_runtime` 讀同一組設定。預設值以設定頁（`config_schema.py`）為準
- 每批成功後 `batch_size = min(batch_size, remaining_count)` 動態縮小；全部項目完成且 `rpm_cooldown_sec > 0` 時 `interruptible_sleep(rpm_cooldown_sec)`（免費層 RPM 保護；預設 0 = 不等待）
- 所有等待（冷卻、429 retry_after、overload 重試、換 Key 緩衝）都用 `interruptible_sleep()`，可被取消打斷；不要在翻譯流程中使用 `time.sleep()`
- 縮小 batch（`BatchAction.SHRINK_BATCH`）的情況：400 INVALID_ARGUMENT（payload 過大 / 格式錯誤）、回應被截斷或漏翻、逾時 / 5xx 類錯誤；縮小係數 `batch_shrink_factor`、下限 `min_batch_size`（lang 類型下限為 `MIN_LANG_BATCH_SIZE`）。縮到極限時以原文回填（標記 `_untranslated`，不寫入快取）並繼續後續項目

### 錯誤處理與 Key 輪替（`_handle_batch_error`）

- **只有 503（overloaded）才累積 overload 計數**，且**逐把 key** 計數（`ApiKeyCycle` 的 `record_overload()` / `clear_overload()`，`lm_config_rules.py`）：不同 key 的 overload 不互相累加；同一把 key 累積到 `OVERLOAD_KEY_SWITCH_THRESHOLD`（3）才對它 `mark_failed()` 並換 key（`BatchAction.ROTATE_KEY`），成功後 `record_success()` 會 `reset()` 開始新的 cycle；非 overload 的 503 則等待 `request_interval_sec` 後換下一個模型
- **404** → 模型不存在，跳過該模型
- **403** → Key 無權限，`mark_failed(reason="forbidden")` 換 Key；無 Key 可換 → RuntimeError「所有 API Key 均無權限」
- **400** 含 FAILED_PRECONDITION → RuntimeError（此地區未啟用 Gemini 免費方案）；`maxOutputTokens` 不被模型支援 → 換下一個模型（無則報錯請調低上限）；其餘縮小 batch
- **429** → 解析 Quota ID：RPM 依 API 回傳的 `retry_after` 等待（無值時 10 秒）後重試同一把 Key；RPD（同專案模式）把**該模型**標記為今日耗盡並換下一個模型——配額算在「專案 × 模型」，換 Key 沒有幫助，所以不換 Key；耗盡的模型到太平洋時間午夜（台灣夏令 15:00 / 冬令 16:00）才恢復。所有啟用的模型都耗盡才回傳 `"ALL_KEYS_EXHAUSTED"`；其他配額錯誤才換 Key。此等待與 `rpm_cooldown_sec` 無關，冷卻設為 0 時仍會依 API 要求等待

## 檔案結構

- `app/views/lm_view.py` — UI
- `app/services_impl/pipelines/lm_service.py` — service 封裝（PR21 抽離）
- `translation_tool/core/lm_translator.py` — `translate_directory_generator`（目錄翻譯流程編排）
- `translation_tool/core/lm_translator_shared_loop.py` — 共用批次迴圈 `translate_items_with_cache_loop`
- `translation_tool/core/lm_translator_main.py` — `translate_batch_smart`（模型 / Key / 錯誤處理狀態機）
- `translation_tool/core/lm_batch_actions.py` — `BatchAction` 與錯誤到動作的對應

## 維護注意

1. 目錄選擇回呼（`on_input_dir_picked` / `on_output_dir_picked`）接受帶 `.path` 的事件物件；`_async_pick_input_directory` / `_async_pick_output_directory` 會包 FakeEvent 觸發（FilePicker 相容層）。
2. 輸出資料夾名稱由 `get_lm_translate_folder_name()`（`app/views/lm_view.py`，後備值為 `DEFAULT_LM_TRANSLATE_FOLDER_NAME`）讀取 `lm_translator.lm_translate_folder_name`，下次任務套用（見 `CONFIG_APPLY_TIMING.md`）。
3. 背景執行緒只寫 `TaskSession`，不可直接修改 control 或呼叫 `page.update()`；畫面更新一律由 `_poll_session` 在 event loop 上進行。
4. 翻譯流程新增等待時使用 `interruptible_sleep()`；新增長迴圈時在迭代之間檢查 `is_cancelled()`。
