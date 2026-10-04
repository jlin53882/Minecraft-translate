# PIPELINE_VIEW_ARCHITECTURE.md

## 定位

PipelineView（`app/views/pipeline/pipeline_view.py`）是**翻譯工作台**：在單一頁面串起整條流水線（抽取 → 語系比對 → 翻譯 → 打包），可逐步執行或「一鍵製作」自動跑完。`PipelineView` 另建了一個 API 金鑰管理區塊（`api_view`），但目前並未掛到頁面上（`controls` 只含 `workbench_view`）。

> 與各單頁的關係：此頁直接呼叫 service（`run_lang_extraction_service` / `run_book_extraction_service`、`run_merge_folder_batch_service` / `run_merge_zip_batch_service`、`run_lm_translation_service`、`build_bundle_staging` + `run_bundling_service`），不經由 ExtractorView / MergeView / LMView / BundlerView。

## 檔案結構（app/views/pipeline/）

| 檔案 | 內容 |
|------|------|
| `pipeline_view.py` | 主視圖：`PipelineConfig`、`PipelineStepChip`、`PipelineProgressPanel`、`PipelineView` |
| `pipeline_extract_dialog.py` | `open_extract_dialog(...)` 抽取參數對話框 |
| `pipeline_merge_dialog.py` | `open_merge_dialog(...)` 語系比對參數對話框 |
| `pipeline_translate_dialog.py` | `open_translate_dialog(...)` 翻譯參數對話框 |
| `pipeline_bundle_dialog.py` | `open_bundle_dialog(...)` 打包參數對話框（含 `_load_version_data()`） |
| `pipeline_one_click_dialog.py` | `open_one_click_dialog(...)` 一鍵製作參數對話框（含 `_load_version_data()`） |

## 核心類別

### PipelineConfig（路徑設定）
以 input/output 目錄為根，串出各階段路徑（資料夾名稱讀 `config.json` 的 `lang_merger` / `output_bundler`）：

| 階段 | 路徑 |
|------|------|
| 抽取輸出 | lang：`<output>/jar_mod_extract/_提取lang_輸出`；book：`<output>/jar_mod_extract/_提取book_輸出` |
| 語系比對輸出 | `<output>/locale_sort/_整理輸出`（`merge_output_dir`） |
| 翻譯輸入 | `translate_input_dirs`：`<merge_output>/lang_output/<待翻譯整理需翻譯>`、`<merge_output>/patchouli_output/<待翻譯>` |
| 翻譯輸出 | `<output>/lm_translate/_翻譯輸出` |
| 打包暫存 | `<output>/_打包暫存`（`bundle_staging_dir`） |
| 打包來源 | `bundle_sources`（優先序由低到高）：`<merge_output>/lang_output` → `<merge_output>/patchouli_output` → 翻譯輸出 |
| 打包輸出 | `<output>/<output_bundler.output_zip_name>` |

### PipelineStepChip
單一步驟晶片，`set_status("waiting"|"running"|"done"|"failed"|"cancelled")` 切換 icon（CIRCLE/PENDING/CHECK_CIRCLE/ERROR/STOP_CIRCLE）與顏色。

### PipelineProgressPanel
四步驟狀態列（抽取資源→語系比對→啟動翻譯→打包資源，以 ARROW_FORWARD 相連）+ `current_label` + `LogView`（`mode="append"`、`max_lines=500`、`height=120`）。
- `start()` / `set_step_running(step_num, name)` / `add_log(msg, level, is_success)` / `finish_step(step_num, success, cancelled)` / `finish_all(success, cancelled)` / `set_running(running)` / `hide()` / `clear_logs()`
- `cancel_button`（「取消」）：執行中顯示，按下呼叫 `PipelineView._on_cancel`
- 這些方法都會修改 control，只能在 event loop 上呼叫
- 日誌使用 `LogView` widget；外層為 `kit.section_card("執行進度")`（`container`，預設隱藏，`start()` 後顯示）

## 主要流程

### 工作台（workbench_view）
```
[填路徑] input_path_text / output_path_text（kit.text_field，旁邊的 kit.pick_button 以 file_picker 選資料夾）
  → 「流水線步驟」卡片四列（`_step_row`，各有「執行」按鈕）：抽取資源 / 語系比對合併 / 啟動翻譯 / 打包資源
  → 各 _on_*_click()：驗證路徑非空 → 開啟對應 dialog → 回呼到 _run_*()（_run_extraction / _run_merge / _run_translate / _run_bundle）
  → _start_single_step()
```

頁面佈局由 `_build_ui` 組合：`kit.page_header`（右側為「一鍵製作」按鈕，`_build_one_click_button`）下方分兩欄——左欄為「專案路徑」卡片（`_build_pipeline_paths_card`）與「流水線步驟」卡片，右欄為進度狀態卡（`progress_status` + `progress_bar`）與 `progress_panel.container`；步驟與狀態卡由 `_build_pipeline_steps_and_status_cards` 建立。

### 執行模型（worker thread → TaskSession → event loop）

```
按鈕 / 一鍵製作（event loop）
  → _begin_run()：清除 _cancel_event、停用按鈕、顯示取消按鈕
  → threading.Thread(worker)
       → _run_session_step(step_num, name, service_fn)   [worker thread]
            session = TaskSession()
            page.run_task(_watch_session, session, done)  ← event loop 上的 watcher
            with cancel_scope(_cancel_event.is_set):
                result = service_fn(session)              ← service 只寫 session
                generator → 完整迭代（未迭代就不會執行）
            done.set() → 等 watcher 送出最後一批日誌
            ok = 未取消 且 not _session_failed(session)
            _ui(finish_step / add_log / _update_progress)
  → _ui(_end_run)：恢復按鈕
```

- **背景 worker 不直接修改 Flet control**，也不呼叫 `page.update()`。所有 UI 更新都經 `_ui(fn)`（`page.run_task` 包一層 coroutine）或 `_watch_session` 在 event loop 上執行。
- **`TaskSession` 是 worker 與 UI 之間唯一的狀態邊界**：worker 只呼叫 `add_log` / `set_progress` / `set_summary` / `set_error` / `finish`；UI 只讀 `snapshot()`。
- **`_watch_session`**（coroutine）：每 `_POLL_INTERVAL_SEC`（0.2 秒）`await asyncio.sleep(...)`，依 log `seq` 只取新日誌、批次 `log_view.add_many()`，並更新進度；`done` 事件設定後再同步一次就結束。
- **session 狀態**：`IDLE → RUNNING → DONE | ERROR`。`set_error()` 之後 `finish()` 不會把 ERROR 改回 DONE。
- **步驟結果判定**（`_session_failed`）：`session.error` 為真，或 summary 中 `failed_zips` / `failed_folders` / `errored_files` 任一大於 0 → 失敗。已要求取消 → 「已取消」（不算成功）。其餘 → 成功。

### 個別步驟
`_start_single_step(step_num, name, service_fn)` 以同一個 `_run_session_step` 執行單一步驟：
- 抽取：依 mode 呼叫 lang / book 抽取 service；dual 模式 lang 失敗就不執行 book。
- 語系比對：dialog 選擇 folder → `run_merge_folder_batch_service(input_dir=...)`；選擇 ZIP → `run_merge_zip_batch_service(zip_paths=[...])`。
- 翻譯：`run_lm_translation_service`。
- 打包：`_bundle_into_session` → `run_bundling_service`（使用者指定的輸入資料夾，不建立暫存）。

### 一鍵製作（_on_one_click_execute）
`_on_one_click_execute` 委派三個方法：`_prepare_one_click`（檢查來源／輸出資料夾存在、至少一個語系代碼，並組出 `PipelineConfig` 與合併選項，不通過則提示並中止）→ `_one_click_steps`（回傳四個 `(step_num, name, fn)`）→ `_start_one_click_worker`（背景執行緒依序跑，結束後 `finish_all` 並 `_end_run`）。

四個步驟依序以 `_run_session_step` 執行，任一步回傳失敗或已取消就停止，後續步驟不執行：

1. **抽取資源**：依 mode 抽取到 lang / book 輸出資料夾。任何 JAR 無法處理（`stats.failures > 0`）或收到 `error` → 抽取 session 為 ERROR（見下方 Service 契約）。
2. **語系比對**：抽取結果是**資料夾**，因此一律走 `run_merge_folder_batch_service`：
   - lang 輸出 → `only_process_lang=True`
   - book 輸出 → `only_process_lang=False`（需處理 Patchouli 內容）
   - 兩者都輸出到 `merge_output_dir`；前一個來源失敗（`_session_failed`）就不處理下一個。
   - **不可把資料夾傳給 `run_merge_zip_batch_service`**：它逐一以 ZIP 開啟 `zip_paths`，資料夾會變成 `failed_zips`，實際上什麼都沒合併。
3. **啟動翻譯**：只翻譯 `translate_input_dirs` 中有檔案的資料夾；沒有待翻譯內容 → 記錄並略過（步驟仍成功）。任一輸入 session.error → 停止。
4. **打包資源**：
   - `build_bundle_staging(bundle_sources, bundle_staging_dir)`：清空暫存後，依序疊加各來源的 `assets/`；同路徑 JSON 逐 key 合併（後者覆蓋），其他檔案直接覆蓋，略過待翻譯資料夾。
   - 暫存為空 → session.set_error()。
   - 否則 `_bundle_into_session(input_root_dir=bundle_staging_dir, ...)`；bundle generator 回傳 `error` → session.set_error()。
   - 先建暫存的原因：打包 generator 遇到多個來源的同路徑檔案會改名（`_1`），不會合併；既有譯文與新譯文必須先疊成一份。

全部成功 → `finish_all(True)`；失敗或取消 → `finish_all(False, cancelled=...)`，並恢復按鈕。

## 取消（cancellation）

```
取消按鈕 → _on_cancel()
  → _cancel_event.set()；目前 session.request_cancel()
  → _run_session_step 以 cancel_scope(_cancel_event.is_set) 包住 service
       → service 可再疊自己的 cancel_scope（例如 lm_service 的 session.cancel_requested）
       → core 在檢查點呼叫 is_cancelled() / raise_if_cancelled() / interruptible_sleep()
       → raise TaskCancelled → 在 service / 翻譯迴圈 / _run_session_step 攔截
```

`translation_tool/utils/cancellation.py` 的契約：
- **`cancel_scope(check)`** 以 thread-local 註冊檢查函式；**巢狀為 OR**：任一層要求取消都算取消，離開時恢復外層。
- **`TaskCancelled` 繼承 `BaseException`**：流程中有許多 `except Exception` 備援，取消不能被它們吞掉；只在 service、翻譯迴圈、步驟邊界明確攔截。
- **`interruptible_sleep(sec)`**：有 cancel scope 時以小步 sleep 並檢查取消；沒有 scope 時等同 `time.sleep`。
- **檢查點**：翻譯在批次之間與等待限流時；抽取在 JAR 之間（`is_cancelled()`）；generator 型 step 在每次 yield 之間。
- **已完成的輸出保留**：翻譯中途取消會照常寫出已完成的批次；取消只阻止後續工作。
- **尚未執行的步驟不執行**：`_run_session_step` 開始前檢查 `_cancel_event`；取消的步驟回傳失敗，一鍵流程就此停止。
- **粒度限制**：抽取已送進 thread pool 的 JAR 會處理完才停止，不保證立即中止單一 JAR。

## Service 層契約（pipelines/*_service.py）

### Update dict
core generator 與 service 以 dict 回報進度，欄位皆為選用：

```python
{
    "log": str,        # 日誌文字（可被節流、合併）
    "progress": float, # 0.0 ~ 1.0（節流時仍保留最新值）
    "error": bool,     # 控制欄位：此步驟失敗
    "result": ...,     # 控制欄位：最終結果（例如批次查詢）
    "stats": dict,     # 控制欄位：統計（例如抽取 success / warnings / failures）
    "phase": str,      # 控制欄位：dual 抽取的 "lang" / "book"
}
```

### GLOBAL_LOG_LIMITER.filter()
- 只節流 `log`（合併多筆）與 `progress`（保留最新值）。
- **`log` / `progress` 以外的欄位一律原樣保留**，不可被剝掉。
- **帶控制欄位的 update 不受節流影響**：會連同先前累積的 log 立即輸出，pending log 不會重複送出。
- 例外 → service yield `{log: 完整 traceback, error: True, progress: 0}`。

### 生命週期判定
- 關鍵失敗（`error`、`stats.failures`）**以原始 update 為準**，不依賴 limiter 的回傳值；limiter 只負責 UI 日誌節流。
- 抽取（`_run_extraction_with_session`）：收到 `error` → set_error；結束時 `failures > 0` → 寫入 summary、記錄錯誤、set_error；否則 finish。取消 → 停止並記錄。
- 語系比對：部分失敗寫入 summary 的 `failed_*` / `errored_files`（單頁維持批次語意，session 可能是 DONE），一鍵流程以 `_session_failed` 視為失敗。
- 打包：`_bundle_into_session` 見到 `error` 即 set_error 並停止迭代。

## API 金鑰管理（api_view）

`api_view` 內含 `keys_container`（以 `_add_key_field` / `_delete_key_field` 增刪 API Key 列）與「儲存設定」按鈕；該按鈕尚未綁定 `on_click`，且 `api_view` 未被加入頁面，所以目前不會顯示。

## 與其他 View 的關係

- 共用 `TaskSession` + `LogView` widget + `_page.run_task()` 的 UI 更新模式
- 使用與 BundlerView 相同的 `run_bundling_service`（打包核心），但此頁走 dialog 流程
- 抽取/語系比對/翻譯 service 與各單頁共用（`app/services_impl/pipelines/*`）
- `set_view_registry` 由 `AppShell` 建立頁面後注入 view registry（`set_registry` 為相容別名，轉呼叫前者）

## 維護注意

1. `_set_buttons_disabled` 只停用 `_run_buttons`（`_step_row` 建立的「執行」按鈕）與 `_one_click_button`；新增會觸發任務的按鈕要自行加入其中。
2. 背景執行緒中不可直接修改 control 或呼叫 `page.update()`；一律經 `_ui()` 或寫入 TaskSession。
3. 新 service 若以 generator 回傳，`_run_session_step` 會負責迭代；若自行判斷失敗，請寫入 `session.set_error()` 或 summary 的 `failed_*`，不要只輸出含「錯誤」的日誌字串。
4. 一鍵流程中資料夾型輸入一律走 folder batch service，不可包成 `zip_paths`。
5. 新增步驟時要同步：`PipelineProgressPanel.steps`、`_one_click_steps` 的 steps、`PipelineConfig` 路徑 property、`_build_pipeline_steps_and_status_cards` 的步驟列。

## 檔案結構（拆分後）

- `app/views/pipeline/pipeline_view.py`：`PipelineView` 主體（步驟執行、一鍵製作流程、取消與按鈕狀態）。
- `app/views/pipeline/pipeline_widgets.py`：`PipelineWidgetsMixin`，控制項與版面組裝（`_build_ui`、各 `_build_*` 卡片、`_step_row`）。
- `app/views/pipeline/pipeline_*_dialog.py`：五個設定對話框（translate、bundle、extract、merge、one_click）。每個對話框用一個 `ctx`（`types.SimpleNamespace`）共享狀態與控制項，handler 是以 `ctx` 為第一個參數的模組層級函式（例如 `_extract_start_extraction`），畫面建構拆成 `_<對話框>_build_*` 函式；測試以 `open_*_dialog` 公開入口驅動（`tests/test_pipeline_*_dialog_behavior.py`）。
