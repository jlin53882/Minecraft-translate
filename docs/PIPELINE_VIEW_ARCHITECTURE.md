# PIPELINE_VIEW_ARCHITECTURE.md

## 定位

PipelineView（`app/views/pipeline/pipeline_view.py`，約 843 行）是**翻譯工作台**：在單一頁面串起整條流水線（抽取 → 語系比對 → 翻譯 → 打包），可逐步執行或「一鍵製作」自動跑完。另有獨立的 API 金鑰管理頁（`api_view`）。

> 與各單頁的關係：此頁直接呼叫 service（`run_lang_extraction_service`、`run_merge_*_batch_service`、`run_lm_translation_service`、`run_bundling_service`），不經由 ExtractorView / MergeView / LMView / BundlerView。

## 檔案結構（app/views/pipeline/）

| 檔案 | 內容 |
|------|------|
| `pipeline_view.py` | 主視圖：PipelineConfig、PipelineStepChip、PipelineProgressPanel、PipelineView |
| `pipeline_extract_dialog.py` | `open_extract_dialog(...)` 抽取參數對話框 |
| `pipeline_merge_dialog.py` | `open_merge_dialog(...)` 語系比對參數對話框 |
| `pipeline_translate_dialog.py` | `open_translate_dialog(...)` 翻譯參數對話框 |
| `pipeline_bundle_dialog.py` | `open_bundle_dialog(...)` 打包參數對話框（含 `_load_version_data()`） |
| `pipeline_one_click_dialog.py` | `open_one_click_dialog(...)` 一鍵製作參數對話框（含 `_load_version_data()`） |

## 核心類別

### PipelineConfig（路徑設定）
以 input/output 目錄為根，串出各階段路徑（皆讀 `config.json` 的 `lang_merger` / `output_bundler`）：
- 抽取輸出：`<output>/jar_mod_extract/_提取lang_輸出`、`_提取book_輸出`
- 語系比對輸入：`<output>/jar_mod_extract`；輸出：`<output>/locale_sort/_整理輸出`
- 翻譯輸入：`<output>/locale_sort/_整理輸出/待翻譯整理需翻譯`；輸出：`<output>/lm_translate/_翻譯輸出`
- 打包輸入：`<output>/lm_translate/_翻譯輸出`；輸出：`<output>/可使用翻譯.zip`（`output_bundler.output_zip_name`）

### PipelineStepChip
單一步驟晶片，`set_status("waiting"|"running"|"done"|"failed")` 切換 icon（CIRCLE/PENDING/CHECK_CIRCLE/ERROR）與顏色。

### PipelineProgressPanel
四步驟狀態列（抽取資源→語系比對→啟動翻譯→打包資源，以 ARROW_FORWARD 相連）+ `current_label` + `LogView`（`mode="append"`、`max_lines=500`、`height=120`）。
- `start()` / `set_step_running(step_num, name)` / `add_log(msg, level, is_success)` / `finish_step(step_num, success)` / `finish_all(success)` / `hide()` / `clear_logs()`
- 使用 **LogView widget**（取代舊 LogPresenter）

## 主要流程

### 工作台（workbench_view）
```
[填路徑] input_path_text / output_path_text（FilePicker）
  → 四顆步驟按鈕（抽取資源/語系比對/啟動翻譯/打包資源）
  → 各 _on_*_click()：驗證路徑非空 → 開啟對應 dialog → 回呼到 _run_*()
```

### 個別步驟
每個 `_run_*` 模式一致：
```
session = TaskSession()
progress_panel.set_step_running(n, name)
threading.Thread(worker).start()
_poll_session(session)
```
`worker()` 內呼叫 service 後，用 `self._page.run_task(async ...)` 把完成/錯誤結果切回 UI 執行緒更新 `progress_panel`。

### 一鍵製作（_on_one_click_execute）
`run_step(step_num, name, service_fn)` 依序執行：
1. 抽取資源（lang/book/dual，依 mode）
2. 語系比對（`run_merge_zip_batch_service`，zip=[merge_input_dir]）
3. 啟動翻譯（`run_lm_translation_service`，dry_run / write_new_cache 由 dialog 設定）
4. 打包資源（`_do_bundle` → `run_bundling_service`，min/max_format=0、pack_image / extra_folders 由 dialog 設定）

任一步失敗 → 顯示錯誤、`_reenable_buttons()` 並中止；全部完成 → `finish_all(True)`。

## Poller（_poll_session）

```
while session.status in ("RUNNING", "IDLE"):
    time.sleep(0.5)
    snap = session.snapshot()
    _update_progress(progress, f"{int(progress*100)}%")
    逐條日誌 → progress_panel.add_log(le.text)（透過 page.run_task）
完成 → _update_progress(1.0, "完成")
```

## Service 層契約（pipelines/*_service.py）

- 每個 `run_*_service` 都是 generator wrapper：迭代 core 的 generator，經 `GLOBAL_LOG_LIMITER.filter()` 過濾高頻日誌後 yield update dict
- 例外 → yield `{log: 完整 traceback, error: True, progress: 0}`
- core generator 的 update dict 契約：`progress` / `log` / `error?`（`bundle_outputs_generator`、`translate_directory_generator`、各 merge/extract generator 皆同）
- 一鍵製作依賴此契約：`run_step` 迭代 service 的 yield 寫入 TaskSession（session.add_log / set_progress / set_error），`_poll_session` 再從 snapshot 讀出更新 UI

## API 金鑰管理（api_view）

`keys_container` 動態增刪 API Key 列（`_add_key_field` / `_delete_key_field`）+ 儲存設定按鈕。

## 與其他 View 的關係

- 共用 `TaskSession` + `LogView` widget + `_page.run_task()` 的 UI 更新模式
- 使用與 BundlerView 相同的 `run_bundling_service`（打包核心），但此頁走 dialog 流程
- 抽取/語系比對/翻譯 service 與各單頁共用（`app/services_impl/pipelines/*`）
- `set_view_registry` 由 main.py 注入 view registry（保留 `set_registry` 相容舊代碼）

## 維護注意

1. `_set_buttons_disabled` 靠「Row.spacing==10 且 Button.height==55」判斷工作台按鈕 — 改佈局時易誤傷其他按鈕。
2. `pipeline_view.py:283` `self.log_content` 為死 widget（註記「下個 commit 移除」）。
3. 一鍵製作的語系比對只走 ZIP 模式（`zip_paths=[cfg.merge_input_dir]`），不支援 folder。
4. 新增步驟時要同步：PipelineProgressPanel.steps、run_step 順序、PipelineConfig 路徑 property。
