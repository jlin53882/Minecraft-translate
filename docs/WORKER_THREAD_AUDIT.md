# Flet worker-thread／lifecycle／blocking 契約盤點（#114）

## 契約

Flet 1.0：sync event handler 直接在 app 的 event loop 上執行（handler 執行期間畫面與其他事件都不會被處理）。因此（issue #114 驗收）：

1. worker thread **不得直接** mutate Flet Control，也不得直接 `page.update()` / `show_snack()`；UI 更新必須排回 event loop（`page.run_task`、`self._ui(...)`、`UiBatcher`、`loop.call_soon_threadsafe`）。
2. **sync event handler 不得做阻塞工作**（長 I/O、迴圈、`time.sleep`、等待外部程式）；要 offload（`asyncio.to_thread`／背景執行緒），或改 async。
3. 長工作必須先取得 `OperationRegistry` admission；`TaskSession` 是進度呈現，`TaskManager` 是 registry projection。mounted App 的 fallback thread launcher 只由 operation layer 管理。
4. 所有 subscription／poller／background task 都要有 **owner** 與 **teardown**（View 被卸載或 session 結束時能停止）。

### 「有界的同步 I/O」的界線（本盤點採用的解讀）

sync handler 內只允許**有界、很小**的 I/O：`stat`／`exists`、帶檔案簽名快取的 `load_config()`（每次 2 次 `stat`）、單一小設定檔的存檔。**不允許**：走訪目錄（`glob`／`rglob`／`os.walk`）、逐項檔案／ZIP／圖片 I/O、整個快取／分片的 JSON 解析、等待外部程式（`subprocess.run`／`wait`）。

## 盤點方式

```bash
rg -n "threading\.Thread|threading\.Timer|Thread\(|run_task\(|run_thread\(|asyncio\.to_thread|time\.sleep|asyncio\.sleep|page\.update\(|subprocess\.run|Popen|glob\(|rglob\(|json\.load|read_text\(" app main.py
```

並逐一沿 call graph 檢查：worker 呼叫的 helper 是否碰 UI；sync handler 呼叫的 helper 是否做上述阻塞 I/O。輔助的靜態護欄（`tests/test_worker_thread_inventory.py`）：

- 背景執行緒啟動點必須與下表一致（新增 `threading.Thread` 會讓測試失敗，迫使分類並更新本文件）。
- Thread target 函式本身（不含巢狀 `apply`／lambda）不得賦值控制項屬性或呼叫 `page.update()`。
- `app/` 內不得使用 `time.sleep`。

行為層測試：`tests/test_view_lifecycle_contracts.py`（poller 的 teardown／重 mount／卸載後不更新、阻塞步驟的執行緒身分、卸載後丟棄結果）、`tests/test_shard_reader.py`、`tests/test_cache_history_store.py`、`tests/test_pipeline_extract_dialog_behavior.py`。

## A. 背景執行緒啟動點（`threading.Thread`，7 個 AST 呼叫點）

| 位置 | 回到 UI 的方式 | owner／結束 |
|---|---|---|
| `app/tasks/operation_registry.py`（2 個 raw Thread call） | registry wrapper 對 mounted App work；standalone fallback 仍以 page worker/daemon thread 執行 | reservation 先於 launcher；handle 結束前保留 active membership |
| `app/startup_tasks.py` | 正式入口傳入 AppShell registry；無 App 的 legacy helper 保留 fallback Thread | `startup-index` non-cancellable / drain-only，關閉時等候完成 |
| `app/shell/config_effects.py` | AppShell 注入 registry launcher；fallback 只供獨立測試/嵌入用 | `cache-root-reload` 註冊為 non-cancellable / drain-only |
| `app/views/moddb/scan_panel.py` | mounted App 使用 `launch_page_operation`；module Thread 僅為沒有 registry 的舊測試/standalone fallback | `moddb-scan` handle 由 Registry 擁有；View poller teardown 見 B |
| `app/views/moddb/translate_panel.py`（批次翻譯、舊 AI 重翻） | 兩種工作均經 `launch_page_operation`；無直接 Thread 啟動 | `moddb-translate`、`moddb-retranslate` 由 Registry 擁有；重翻為 page-bound session、cancel-and-drain；逐筆提交見 B |
| `app/views/moddb/retranslation_controller.py`（舊 AI 預覽） | mounted App 使用 `launch_page_operation`；worker 快照 path/priority 後建立並關閉獨立唯讀 `TranslationDB`，不共用 UI DB lock；module-level Thread 僅供 standalone fallback | `moddb-retranslate-preview` 是 non-cancellable / drain-only；結果經 UI loop 套用並以 generation/DB identity 丟棄舊結果 |
| `app/views/pipeline/pipeline_session.py` | AppShell 將 Registry 注入 PipelineRunner；default Thread launcher 僅作 standalone fallback | parent handle 跨完整 sequence；步驟 watcher 由 `PollerHandle` 持有 |

其他 executor：IconPreview 的 `icon_cache`／`icon_index` executor 是 IconPreview Registry operation 的 nested worker；`cache_history_store._MIRROR_EXECUTOR` 是 process-global 衍生 JSON 鏡像 writer，App close 以 `history_flush()` 排空，但目前不在 OperationRegistry membership（剩餘風險見 `docs/OPERATION_LIFECYCLE_CONTRACT.md`）。

## B. 長生命週期輪詢／task 的 owner 與 teardown

| 輪詢 | owner | teardown | 驗證 |
|---|---|---|---|
| 翻譯頁 `_poll_session` | `TranslationView._poller`（`app/ui/poller.PollerHandle`） | `will_unmount()` → `stop_ui_timer`；`did_mount()` → `resume_ui_timer` | `test_translation_*`：卸載後不再同步、重複 teardown、重 mount 只剩一個 poller、卸載期間結束會補最終狀態 |
| LM 頁 `_poll_session` | `LMView._poller` | `will_unmount()`／`did_mount()` | `test_lm_*`（同上） |
| 合併頁 `_poll_merge`（原為 `time.sleep(0.1)` 的 poll 執行緒） | `MergeView._poller` | `will_unmount()`／`did_mount()`；DONE／ERROR 設 `_ui_stop` | `test_merge_*`：不再另開執行緒、卸載停止、重 mount、卸載期間完成只顯示一次摘要 |
| 流水線步驟 `_watch_loop` | `PipelineRunner.poller`（`PollerHandle`） | `will_unmount()` → `runner.on_unmount()`；`did_mount()` → `runner.on_mount()`；worker `finally` 設定 `done` 並補 `_final_sync` | `test_pipeline_runner_behavior`（卸載停止、重掛載單一 poller、卸載期間完成補日誌、取消）、`test_pipeline_step_watcher_always_ends_with_the_step` |
| 提取預覽 `_preview_ui_poller` | 對話框（`preview_state`／`state["cancelled"]`） | 完成或取消即結束；dismiss 設定取消 | `test_extractor_preview_dialog_behavior` |
| 流水線提取預覽 `_extract_preview_poll` | 對話框（`cancel_event`） | 「取消」設旗標、輪詢立即結束、結果丟棄、overlay 移除 | `test_pipeline_extract_dialog_behavior`（探索期間取消、反覆開關不累積） |
| 圖示預覽載入／渲染（`_load_async`、`_prepare_and_fill`、`_open`） | `IconPreviewView._load_generation`／`_render_generation` | `will_unmount()` 遞增世代：結果丟棄、旗標復位；進度刷新在載入結束後不再套用 | `test_icon_*`（卸載中的掃描／渲染／zh 讀取被丟棄） |
| Debouncer（快取頁、規則頁、圖示頁） | View | `will_unmount()` 呼叫 `cancel()` | `test_views_with_debounced_work_cancel_it_on_unmount` |
| `AppShell` 的任務／config 訂閱 | `AppShell` | `dispose()` | 既有測試 |
| `BundlerView` config 訂閱 | `BundlerView` | `will_unmount()` | 既有測試 |
| `UiBatcher` 排程 | 該次 worker | worker 的最後一次 `flush(force=True)`；有限次、非常駐 | 既有測試 |

其餘 `page.run_task` 皆為**一次性有限工作**（選檔、剪貼簿、套用結果、focus、debounce 觸發），不會常駐。

### TaskSession terminal 契約（所有背景任務）

`start()` 之後任何結束路徑都要有一次 `finish()`，失敗順序為 `set_error()` → `finish()`（`TaskSession.finish()` 對已出錯的維持 ERROR）；`TaskManager` 以 `finish` 為結案事件。盤點結果：`_task_runner.run_callable_task`（FTB／KubeJS／MD）原本就符合；本 PR 補齊 extract（lang／book／dual／取消）、LM、merge（folder／zip）、`MergeView`／翻譯頁的 worker 例外邊界，以及 `PipelineActions`（dual 抽取、一鍵 merge、打包）。護欄：`tests/test_task_terminal_lifecycle.py`（真正的 `TaskManager`，每條路徑都驗證 `active() == []` 與 recent 狀態）。

#### `session.start()` 的 owner 分類（`rg "session\.start\(" app`）

| 位置 | 類型 | terminal finish 在哪裡 |
|---|---|---|
| `extract_service`（lang／book／dual，`manage_session=True`） | single owner（service） | `_run_extraction_with_session` 與例外分支（`_end_failed`）；取消也 `finish()` |
| `lm_service`（`manage_session=True`） | single owner（service） | `finally`（成功／錯誤／例外／取消皆經此） |
| `PipelineActions.merge`（單獨語系比對） | single owner（action 啟動，service 結案） | `merge_service` 的結尾與 `finally`（`generator.close()` 也會結案） |
| `PipelineActions.bundle(manage_session=True)` | single owner（action） | `finally`（`except` 先 `set_error()`） |
| `PipelineActions._extract_into`（dual） | composite outer owner | `finally`；內部 lang／book 以 `manage_session=False` 呼叫 |
| `PipelineActions._step_merge` | composite outer owner | `finally`；內部以 `finish_session=False` 呼叫 |
| `PipelineActions._step_translate` | composite outer owner | `finally`；內部以 `manage_session=False` 呼叫 |
| `PipelineActions._step_bundle` | composite outer owner | `finally`；內部 `bundle(manage_session=False)` |
| `_task_runner.run_callable_task`（FTB／KubeJS／MD） | single owner | `finally` |
| `MergeView`、翻譯頁（FTB／KJS／MD）的 `session.start()` | UI owner（View 啟動，service 結案；worker 例外邊界補 `set_error()`→`finish()`） | service；View 例外邊界 |
| `LMView` | **不再 start**（原本與 service 重複 start，會清掉剛寫入的日誌） | `lm_service` |

merge 的取消檢查點（`merge_service`，經 `raise_if_cancelled()`；`PipelineRunner` 以 `cancel_scope` 包住 service，`TaskCancelled` 繼承 `BaseException` 不會被內部 `except Exception` 吞掉，會經 service `finally` 結案）：folder 階段 1 與階段 2 的每個 update、ZIP 的每個 update、ZIP 與 ZIP 之間——停止消費核心 generator 即中止其後續處理，執行中的來源不會「跑完才標記取消」。測試以「10 個 update、第 1 個之後取消、`processed == [0]`」驗證（folder／ZIP 各一）。

取消檢查點：composite action 在 inner operation 之間檢查 `session.error`／`is_cancelled()`（dual 抽取的 lang→book、一鍵 merge 的來源之間、一鍵翻譯的輸入之間），取消後不再執行下一個；`PipelineRunner` 迭代 generator 時偵測取消會 `close()` 並補 `finish()`。

#### 會被 `PipelineRunner` `close()` 的 generator（close-safe 盤點）

| 路徑 | finish 位置 | close-safe |
|---|---|---|
| `PipelineActions.merge`→`run_merge_folder_batch_service` | `finally`（`finished` 旗標避免重複）＋Runner 補 `finish()` | ✅ |
| `PipelineActions.merge`→`run_merge_zip_batch_service` | `finally`（`finished` 旗標）＋Runner 補 `finish()` | ✅ |
| `PipelineActions.bundle` | `finally`（`manage_session=True`） | ✅ |
| 一鍵 `_step_merge` | outer `finally` | ✅ |
| 一鍵 `_step_bundle` | outer `finally` | ✅ |
| extract／LM（非 generator，用取消旗標＋`cancel_scope`） | 取消檢查點／`finally` | ✅ |

護欄：`tests/test_terminal_contract_matrix.py`（success／error／exception／cancel × single／composite，真正的 `TaskManager`）與 `tests/test_task_terminal_lifecycle.py`。

## C. event loop 上的同步阻塞（本 PR 前 → 後）

| 位置 | 原本 | 現在 |
|---|---|---|
| `icon_preview_view._on_load_clicked`：`_detect_source_mode`（glob／rglob）、`_try_use_cached_entries`（L2 快取 JSON）、`_begin_scan`（glob／rglob 計步） | 在 handler 內同步 | `_load_async` 內逐步 `asyncio.to_thread`；世代守衛 |
| `icon_preview` 詳情：`_open_mod_detail`（zh_tw.json 搜尋含 rglob fallback、讀取） | 同步 | `_find_zh_file` 在 `to_thread` |
| `icon_preview` 詳情：`_render_current_page`（每列 `resolve_icon_with_reason`、開 JAR ZIP、PIL 開圖／縮放／寫檔） | 在 event loop 逐列同步 | `prepare_row_icon` 在 `to_thread`；event loop 只建構 `LangItemRow(prepared_icon=...)` |
| `icon_preview` 儲存 `_save_current_zh`（寫 zh_tw.json） | 同步 | `_write_zh_file` 在 `to_thread` |
| `pipeline_extract_dialog._extract_show_preview_result`：`find_jar_files(mods)` | 同步走訪資料夾 | 在預覽 worker 內執行 |
| `open_output_folder`（macOS／Linux `subprocess.run(check=True)`） | 等外部程式結束 | `Popen` 不等待；Windows `os.startfile`；失敗回傳 False 並記錄 |
| `merge_view._open_output_folder`（`Popen(["explorer"], shell=True)`，僅 Windows） | Windows 專用且 `shell=True` | 改走 `open_output_folder` |
| 快取分片頁 `_load_shard_rows`（每次渲染解析**所有**分片 JSON）、`_load_shard_keys` | 每次同步解析 | `shard_reader` 以檔案簽名記憶；背景（`_fetch_overview`、`run_cache_action`）預熱 |
| 快取歷史 `history_load_recent`（掃描整個 jsonl）、`history_append_event`（每次讀寫整個 ≤10000 筆的 json 陣列） | 同步 | 索引記憶 + 就地更新；json 鏡像由單一背景執行緒寫入 |
| Mod DB 舊 AI 重翻預覽：候選查詢、樣本分組與批次估算 | `preview_retranslation()` 在 UI handler 同步查詢 | Registry-owned `moddb-retranslate-preview` 背景操作；worker 依 path/priority 建立獨立唯讀 DB connection，結果經 `page.run_task` 套用，generation 與 DB identity 不符即丟棄 | 查詢不再共用 UI 的 SQLite connection/lock 或阻塞 UI；單次 SQLite 查詢仍不可即時中斷，shutdown 採 drain-only，最大查詢時間尚未證明 |
| `merge_view` 輪詢執行緒的 `time.sleep(0.1)` | worker 睡眠並反覆排程 | 改為 event loop 輪詢；`app/` 不得有 `time.sleep`（護欄測試） |

## D. 仍保留的同步 I/O（皆在「有界」界線內或為非 UI 路徑）

- `load_config()`／`load_config_shared()`：有檔案簽名快取，handler 內每次只 2 次 `stat`。對話框初始化、`PipelineConfig` 建構使用。
- `config_view` 存檔：單一設定檔寫入。
- 一鍵／打包對話框的 `_load_version_data()`：讀隨程式附帶的小型 JSON（`resource_pack_version.json`）。
- 快取頁 `_load_shard_entry`：僅在記憶體快取找不到該筆時才 fallback 讀分片（LRU 只留最近 2 個）；正常路徑由 `cache_get_entry_service` 提供。
- `icon_preview_row.LangItemRow` 未提供 `prepared_icon` 時的同步計算：**只剩沒有 event loop 的測試替身路徑**；正式路徑一律先 `prepare_row_icon`。
- 對話框 `os.path.isdir` 之類的單次 `stat` 驗證。

## E. 非驗收 blocker 的後續改善（不影響 #114 契約）

- 把 `PollerHandle` 推廣到其他一次性 `run_task`（目前不需要）。
- 統一各設定對話框改用 `page.show_dialog`／`pop_dialog`（目前以 `close_overlay_dialog` 保證關閉與移除）。
- 真實 Windows 桌面版的卸載時序與大型 Mods 資料夾（>400 個 JAR）驗證：見 `docs/WINDOWS_VERIFICATION.md`；本沙箱以 Flet 網頁版與行為測試驗證。
