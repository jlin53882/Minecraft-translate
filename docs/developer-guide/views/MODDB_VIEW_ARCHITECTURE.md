# Mod DB View 架構

`ModDbView`（`app/views/moddb_view.py`）維持資料庫連線與頁籤生命週期；`EntriesPanel`（`app/views/moddb/entries_panel.py`）負責條目篩選、編輯與背景刷新。資料庫查詢與寫入經由 `app/services_impl/moddb_service.py`，核心資料存取由 `translation_tool/translation_db/` 提供。

## 查詢快照與分頁

背景 worker 的唯讀連線使用 `translation_tool/translation_db/read_cache.py` 的有界 JSON
快取，每個消費者取得自己的解碼結果，不共用可變物件。快取鍵包含資料世代、資料庫檔案
身分與 WAL 狀態；寫入交易不讀取或發布共用結果。統計仍可使用資料庫內的世代快取。

一般條目分頁先以篩選、排序所需的最少資料表取得當頁 ID，再載入完整欄位；唯讀分頁的
兩階段查詢位於同一讀取交易，避免中間寫入造成頁面不一致。品質掃描的結果可快取當頁 ID
與總數，完整列仍從相同資料快照取得。快取有容量與筆數上限，不保證第一次查詢或大量批次
工作的固定完成時間；資料變動後須重新計算。

## 更新邊界與回應速度

Flet 的 `update()` 成本與被走訪的控制項數成正比：`page.update()` 會 diff 整棵樹，連頁籤切換、
狀態列刷新都要付出 Mod DB 頁數千個控制項的成本。因此 `ModDbView`、`OverviewPanel`／
`EntriesPanel`／`ScanPanel`／`TranslatePanel`，以及 `EntriesPanel` 的 `filter_card`、`list_view`、
`editor_card`、`history_card` 都用 `app/ui/update_scope.py` 的 `isolate` 標成更新邊界，
由擁有者以 `update_regions` 明確更新；各頁籤提供 `flush()` 把整個頁籤送到畫面，
`EntriesPanel.flush_all` 用於來源不明的變更，熱路徑只更新變動的區塊。新增會改動邊界內控制項的
程式時，必須呼叫對應的更新，否則畫面不會變。

畫面執行緒不碰資料庫的規則：翻頁、選取、輸入影響預覽、儲存／還原／審核與批次機翻範圍計數
都在 worker 執行，結果以請求序號（`_select_ticket`、`ReviewScopeController` 的影響預覽序號、
`ModDbView` 的快照 generation 與請求內容比對）判斷是否過期；沒有背景排程的頁面
（`app/ui/page_post.py` 的 `has_background_scheduler` 為假，例如單元測試）同步執行。
快照世代是否被外部寫入改變，由 worker 讀完後比對（`_mark_snapshot_generation`），
UI 執行緒不在共用連線上查詢，避免與正在寫入的 worker 搶同一把鎖。

模組分工：`entry_row_tile.py`（固定列數的 `EntryTilePool`）、`entry_selection.py`（選取與詳情載入）、
`entry_writes.py`（`run_write`、儲存、還原與寫入後重整）、`entry_save_result.py`（`apply_save_result`：背景儲存結果的畫面套用，來源優先序資訊由 worker 算好）、`background_read.py`（`run_read`：
審核範圍預覽這類背景唯讀工作）、`review_scope_dialog.py`（`compute_impact`、審核確認與解除審核）。

更新範圍的補充規則：

- 對話框（`BulkReviewDialog`、`CleanupDialog`、`SpeciesCandidateDialog`、`SmartReviewDialog`、
  `RepairReviewer`）開啟前以 `isolate` 標成更新邊界，內容只由 `update_regions(self.dialog)`（智慧校對
  是 `SmartReviewContext.update`，其 `dialog` 由 `SmartReviewDialog` 設定）更新；對話框內不得呼叫
  `page.update()`，否則整頁走訪一次要數十到上百毫秒。`app/ui/dialogs.py` 的縮放處理也逐一更新視窗。
- 條目寫入完成由 `entry_writes.after_data_change` 收尾：背景模式只觸發上層快照（`_refresh_active_panel`
  以 `show_loading` 顯示輕量提示），整個面板由快照套用後的 `flush` 重畫一次，回傳值表示編輯區是否
  由快照重畫。`ModDbView._request_panel_refresh` 先把讀取交給 worker，再重畫載入中提示。
  載入期間 `EntriesPanel.set_data_loading(True)` 停用批次取代、批量審核與 Smart Review 等依賴
  範圍資料的入口，快照套用後才恢復；按鈕的停用狀態一改變就直接更新這四顆按鈕，不依賴 `FilterFlush`
  的差異比對（篩選卡可能在載入中被搜尋欄等事件整張送出，比對記錄不知道畫面上已是停用）。
- `apply_filter_options` 回傳選項或選值是否改變，並把有變的下拉選單登記到 `FilterFlush`；
  `EntriesPanel.flush_filter_options` 只更新這幾個下拉選單（選項很多的模組選單 diff 成本高，
  整張 `filter_card` 更貴），進階條件摘要、錯誤文字與四個資料操作按鈕（載入中停用）的狀態與上次送出的不同時
  只更新 `filter_actions_row`／錯誤文字；只有 `flush_all`（來源不明的變更）才重畫整張 `filter_card`。
  `flush` 在清單、編輯區與記錄之後才呼叫 `flush_filter_options`。
- 清單：`EntryTilePool.set_rows` 回傳內容有變的列，`EntriesPanel` 只更新這些列（儲存／審核／還原／解除審核
  通常只有一列）；有變的列超過 `MAX_ROW_UPDATES`、或清單進出空狀態時回傳 `None`，改更新整個 `list_view`。
- 寫入完成（`entry_writes.after_data_change`、`entry_save_result.apply_save_result`）先交出背景重載請求，再顯示提示；
  重載會重畫編輯區與影響預覽，所以儲存結果不另外重算影響預覽、也不先重畫編輯區。
- `TranslatePanel` 的範圍變更：`flush_loading` 只更新載入提示與筆數文字、`flush_refresh` 只更新筆數文字與
  有變的下拉選單（`ModDbView._flush_loading`／`_update_panel` 在頁籤已顯示時才走這兩條）；上限欄位逐字輸入只更新
  `count_text`。工作結束後的範圍筆數交給 `_request_scope_refresh`（背景快照），畫面執行緒不查資料庫。
- `SmartReviewDialog` 建構期間 `SmartReviewContext.building` 為真，面板初始化的 `update()` 不做事；「歷史批次」
  分頁切到時才讀取與繪製（開啟視窗不讀歷史），因為歷史卡片很多時整個視窗的 patch 可達 100 KB。
- 沒有畫面變更的事件處理函式（全域按鍵 `KeyboardShortcutHandler.handle_keyboard`、
  `EntriesPanel._on_search_input`）呼叫 `suppress_auto_update`，避免 Flet 在事件後補一次自動更新。
- 畫面執行緒連線上的 `TranslationDB._persist_stat_cache` 以 `busy_timeout = 0` 寫入：其他連線持有
  寫入鎖時直接放棄這次快取寫入，不凍結畫面。

## 學名候選流程

`SpeciesCandidateDialog` 開啟時先顯示呼叫端帶入的預填值，再以維護型背景作業依學名候選品質條件建立模組清單與目前生效來源對照；模組切換只重用已載入的對照，不重新掃描資料庫。切換範圍只清除舊掃描結果，不會自動重新掃描；關閉視窗會丟棄晚到的範圍結果。

條目校對的 `species_scan_btn` 依目前 `version`、`mod_id` 與 `kind` 開啟 `SpeciesCandidateDialog`。對話框分成兩個階段：

1. `scan_species_candidates` 以 `QualityFilter` 的 `species_name`（預設）或 `species_format`（對話框的放寬選項）呼叫 `TranslationDB.list_entries`，由 SQL 預篩加 `is_species_candidate` 判斷取出候選，不逐筆載入整個版本；候選不設上限，對話框用 `kit.Pager` 每頁 `PAGE_SIZE` 筆顯示。`species_name` 要求值符合 `is_potential_species_name` 的格式，且 key 有 `has_species_key_hint` 的提示詞（`SPECIES_KEY_HINTS`）或值含雜交／亞種／變種記號。
2. 使用者按單筆「查詢」或「查詢全部候選」後，背景作業才呼叫 `lookup_species`。批次查詢屬於頁面而不是對話框：`bulk_lookup_for(page)` 回傳頁面共用的 `SpeciesBulkLookup`，它以 `tag_session` 建立的 `TaskSession`（`view_key="moddb"`）和 `launch_page_operation` 執行，可由 `SpeciesBulkLookup.stop` 或頂列任務取消；對話框與 `SpeciesSuggestion` 透過 `add_listener` 以弱參照訂閱 `lookup`／`finished` 事件，「背景執行」（`_run_in_background`）、「開啟條目」與 Esc 關閉只退訂；「關閉」（`_close`）先呼叫 `SpeciesBulkLookup.stop` 再關閉。結果是 `SpeciesLookup`（`found`／`not_found`／`rate_limited`／`error`），回到 UI 執行緒顯示；不會寫入 `entry`、`translation` 或 `effective`。

條目校對的建議區另有「學名」分頁，由 `app/views/moddb/species_suggestion.py` 的 `SpeciesSuggestion` 負責：只讀 `cached_species_lookup`（不連線），查詢時才用 `lookup_species`，結果回到 UI 後經 `EntriesPanel._on_species_changed` 重繪；「套用」呼叫 `EntriesPanel._apply_suggestion` 只更新譯文框。

每個候選列同時提供 `開啟條目`，透過 `EntriesPanel.select` 回到編輯器。掃描與查詢使用 `launch_page_operation`、`CommitPolicy.EPHEMERAL` 與 generation 檢查；視窗關閉或範圍過期時丟棄晚到結果。批次查詢使用 `CancellationPolicy.COOPERATIVE`，每筆查詢前檢查 `_cancel` 事件；`launch_page_operation` 拒絕啟動時由呼叫端還原忙碌狀態。每次只重建完成的那一筆候選列，批次進度由 `_flush` 節流重繪。

## AI 智慧校對候選

Smart Review 的執行分頁與歷史分頁共用 `smart_review_planner.py`：新批次最多讀取**目前版本**最近兩份可解析 JSON manifest，其他版本不佔額度，依完整 CAS 快照選擇最新可重用結果；`applied`／`no_change` 僅在快照有效且啟用跳過時重用，`ready` 保留待審查，暫時失敗或未完成的最新結果才可在這個兩批界線內回看較舊有效結果。執行前預覽透過 `include_context=False` 與 `compare_context=False` 只讀取 CAS 欄位，不為每筆候選建立 local context；背景工作會回報目前候選、同版本歷史 JSON 分片與規劃比對階段，且預覽的 `actual_ai_count` 才是批次估算的分子。預覽的重用數量是快速估算，真正執行仍建立完整 context 並依完整 CAS 指紋規劃。原批次續跑由 `prepare_smart_review_resume` 重新核對 SQLite 現況，沿用原 `run_id`；歷史頁先顯示指定 run 的狀態確認視窗，模型和批次大小可輸入覆寫，確認後才把該 run 的完整範圍／原上限同步回執行頁再啟動。晚到的初始範圍查詢不能覆蓋這份快照；執行 log 以資料層重新驗證後的實際數量為準，續跑 ETA 只用本次新增處理量與準備完成後的耗時計算。未變更 CAS 欄位的候選沿用 JSON context，只有快照變動時才重建 context；人工修改造成的失效候選寫入 `attempt_history`，只將可重試的項目排回 `pending`，並把 `quota_exhausted`／`auth_required` 等執行層停止原因與未送出數量保留在 metrics。這些判定同時供唯讀預覽、背景 job 與歷史卡片使用。

批量審核與 Smart Review 的範圍控制項都只顯示真正有未審核候選的模組；來源選項依選定模組重建。開啟對話框、切換版本／類型／品質條件時，模組與來源範圍查詢使用維護型背景作業；模組切換只重用已載入的來源映射，不再次查詢整個資料庫。查詢結果以序號驗證，舊結果不會覆蓋新條件；範圍變更只更新預填值、來源選單與筆數估算，不能繞過使用者明確按下預覽、掃描或開始按鈕。

`entry_filters.py` 的 `批量審核` 按鈕只建立 `BulkReviewDialog`，並把 EntriesPanel 目前的版本、模組與 effective source 預填進對話框；預填不會自動產生預覽或執行，使用者仍須明確按下預覽與確認。`智慧校對` 按鈕只建立 `SmartReviewDialog`，並把 EntriesPanel 的版本、模組、kind、effective source、未審核狀態與搜尋條件帶入；不把模型流程塞入 `EntriesPanel`。視窗只是外殼，三個分頁各自一個模組：`SmartReviewRunPanel`（範圍設定、進度、統計與 log）、`SmartReviewReviewer`（每頁一筆的逐筆審查）、`SmartReviewBatchReviewer`（分頁勾選與批量處理）、`SmartReviewHistory`（歷史批次）。`SmartReviewContext` 集中保存 UI 世代與資料庫身分（世代值取 `cache_generation_nowait`，不等共用連線的鎖）；執行頁的預估文字只以 `update_regions(estimate_text, plan_preview_text)` 更新，候選計數與歷史規劃的背景查詢帶 `QueryInterrupt`，被新請求取代或視窗隱藏時中斷；審查／歷史分頁的初始資料讀取透過 `run_background` 啟動（審查分頁讀取共用批次摘要前先呼叫兩個面板的 `show_run_loading` 顯示讀取中），逐筆／批量面板內的換頁／換筆則用 `run_inline` 在同一個 UI 事件內完成，兩者都會檢查世代、資料庫身分與呼叫端的 `still_valid`。校對本身由 `SmartReviewJob` 執行（頁面層級單例，`job_for_page`），以 `TaskSession` 登記進度與 log，關閉視窗不會中斷。服務層 `moddb_smart_review_service.py` 重新以資料庫 path、priority 與 config 開啟連線；`translation_tool/translation_db/smart_review.py` 是 facade，實作依職責分在同目錄的 `smart_review_*.py`：候選 snapshot（`smart_review_filters.py`）、typed local context（`smart_review_context.py`）、provider structured output（`smart_review_provider.py`、`smart_review_request.py`、`smart_review_repair.py`）、批次流程（`smart_review_run.py`）、JSON 批次持久化（`smart_review_runs.py`、`smart_review_projection.py`）與 CAS apply（`smart_review_apply.py`）。預覽使用唯讀連線，執行與套用透過 `launch_page_operation` 在背景進行。

`SmartReviewDialog` 的審查內容區使用獨立的寬高設定，且在頁面尺寸變更時沿用同一組設定；逐筆與批量工具列以可收合 `SectionCard` 保留摘要資訊又能讓出檢視空間。開啟審查批次時先載入逐筆模式，只有切換到批量模式才呼叫 `SmartReviewBatchReviewer.load_run`，避免隱藏控制項啟動候選分頁查詢。
智慧校對套用成功後，`open_smart_review` 會先以 `refresh_entry_filter(panel, keep_selection=True)` 重載目前條目頁，再通知 `ModDbView` 外層狀態變更，確保條目列表旁的來源／審核標籤與中央編輯器同步。
條目編輯器在目前生效譯文為 `reviewed` 或相容舊資料的 `legacy_unknown` 時才顯示「解除人工審核」；動作以目前譯文、來源、審核者與狀態作 CAS，只把狀態改回 `unreviewed`，不改譯文或來源，並寫入可由 `revert()` 還原的 `review_unmark` history。CAS 失敗時重新載入目前條目，不把舊畫面當成成功。
`SmartReviewRunPanel` 開啟或變更範圍時，會以唯讀 count query 顯示符合範圍的未審核 AI 譯文總數、本次上限與批次數；上限輸入 `0` 代表不限，每批筆數仍必須大於 `0`。歷史規劃預覽會捕捉完整範圍、開關、model 與資料庫查詢世代，晚到的舊結果不得覆蓋新預估；模型切換也明確失效並重算預覽。預覽第二行與計畫摘要共用同一個 `actual_ai_count`，再以使用者填入的 batch size 計算批次，避免同時顯示兩個不同口徑的數字。預覽的背景查詢只建立無 context 的候選快照，並以階段訊息回報長時間 JSON 讀取；預覽結果是可重用數量估算，真正執行仍建立完整 context 並依完整 CAS 指紋規劃。plan 的 `actual_ai_count` 語義是 unique candidate 已進入 provider request 邊界，真正的 network attempt 另由 `network_attempts` 診斷指標表示。機翻、既有譯文修復與 Smart Review 都透過 `translation_tool/core/common_batch_executor.py` 的共用批次生命週期回報安全結構化事件；各自的 candidate、prompt、provider retry、validation、CAS／快取／JSON 寫入仍由專屬策略保留。Smart Review 事件分別記錄 batch start、外層 attempt、provider API、底層 network attempt、HTTP／timeout／connection 錯誤分類、Retry-After、配額等待、retry backoff、回應解析與 JSON 寫入耗時；重試等待期間仍透過 `TaskSession` 更新 UI，不把 retry backoff 混入配額等待。完成時再寫入 selection／planning／prompt／API／配額／retry／解析／驗證／寫入／total 的 monotonic timing、valid／invalid／needs_context／no_change／suggest_revision 與 validation error 分布。事件不得包含 API key、完整 prompt、完整 response 或錯誤原文。事件欄位由 `translation_tool/core/batch_events.py` 的 `BATCH_EVENT_SCHEMA_VERSION` 版本化 schema 定義：core 只輸出 canonical 欄位（耗時一律是毫秒 `*_ms`，嘗試序號是 `attempt`，批次序號是 `batch_index`），耗時累加統一經過 `BatchTiming.add()`；舊版 `*_seconds`、`smart_review_attempt` 等欄位只由 `to_legacy_event()` 在進度回呼與持久化 metrics 的輸出邊界補上。事件的中文日誌只由 `format_batch_event()` 產生，`smart_review_job.py` 透過 service 層呼叫它，讀到舊歷史事件時會先 `canonicalize_event()`。

### Mod DB 資料變更與 resident UI 狀態

所有會提交 Mod DB 寫入的面板完成回呼，都必須通知 `ModDbView._on_database_changed`（EntriesPanel 仍保留 `_on_entries_changed` 相容轉接）。這個共通入口會遞增資料世代、使 overview／entries／scan／translate 的快照與 pending 結果失效，刷新目前頁籤，並把隱藏頁籤留在待刷新狀態。EntriesPanel 在寫入後的 row 重載不能取代這個入口，因為模組／來源選項、分頁、總覽 KPI 與其他已掛載頁籤可能仍是 resident snapshot。

EntriesPanel 的編輯器另有獨立的 resident 草稿契約：`EditorDraftStore.remember()` 以條目 id 保存編輯框相對於目前資料庫值的差異；`apply_refresh_snapshot` 在替換清單與詳情前先呼叫它，`_show_editor` 再以草稿優先於資料庫快照。完整刷新仍可更新清單、篩選選項、審核標籤與統計，但不會覆蓋未儲存輸入。資料庫身分改變時，先清除草稿並重置選取條目、詳情與編輯框，再套用新資料庫的清單或背景快照，避免相同條目 id 把舊資料庫草稿帶入新資料庫。儲存回呼只有在編輯框仍等於剛寫入的原始文字時才清除，儲存完成後繼續輸入則保留並標示尚未儲存。草稿儲存元件位於 `app/views/moddb/editor_drafts.py`，讓 resident state 的規則可獨立維護與測試。

資料庫身分 transition 的共通 owner 是 `EntriesPanel._sync_database_identity`，實際清理由 `editor_drafts.reset_editor_for_database_change` 集中處理。身分改變包含從未知身分恢復為實際資料庫；除了人工草稿、選取與詳情，也必須使機翻 `entry_ids`、`drafts`、`_flagged_batch` 與審查預覽失效，不能只清理編輯框。`ModDbView._validate_entries_snapshot` 是 Entries 快照的套用邊界：完整與 filter-only 背景快照都必須同時符合請求身分、快照身分、目前 panel 身分與 `db_generation`；身分不符時清除舊 scope 並重新載入資料庫，資料代次不符時丟棄快照並重新查詢。過期快照不可把舊資料庫的 source scope、清單或批次條件套用到新資料庫。

`BulkReviewDialog` 是另一個 resident scope：它只顯示仍有未審核候選的模組／來源。批量審查或還原成功後，除了呼叫外層完成回呼，也必須重新啟動 `_start_scope_options_load`；否則資料庫已正確提交，但對話框下拉會保留舊 scope，直到使用者再次切換條件。資料清理／還原完成回呼同樣不可只呼叫 `OverviewPanel.refresh`，必須走上述共通入口。
`SmartReviewReviewer` 依候選狀態分兩條套用路徑：`ready` 走 `apply_smart_review_candidate`；`SMART_REVIEW_MANUAL_EDIT_STATUSES` 內的狀態（已套用、已保留原譯文、需補 context、不用改、無效、格式不符、已過期）走 `apply_smart_review_manual_edit`，載入時先以 `realign_smart_review_candidate` 對齊快照，並由 `is_manual_edit_row`、`initial_draft_text`、`manual_edit_hint` 決定草稿起點與提示。人工編輯路徑的 CAS 比對對齊後的快照，快照改變（含英文原文）只回傳 `changed`、不把候選降成 `stale`；CAS 先於格式檢查，格式檢查使用交易內的最新原文。SQLite 寫入後批次 JSON 回寫失敗時回傳 `state_sync_failed`，`_after_action` 仍重載條目頁並顯示專屬訊息，後續由 `reconcile_applied` 補回狀態。

`SmartReviewBatchReviewer` 使用 `kit.Pager` 每頁 25 筆，選取以 `run_id` 與候選 `ordinal` 保留，因此可跨頁勾選；「全選全部可套用」會在背景載入目前篩選的完整候選並只選取格式診斷通過的 `ready` 項目；「全選全部可覆蓋」則載入所有 stale 候選，只選取有草稿且格式診斷通過者。批量套用、保留原譯文與覆蓋已過期都先開確認視窗，再由 `apply_moddb_smart_review_batch_from_path` 以部分成功策略逐筆執行；前兩者呼叫原有 CAS 動作，覆蓋動作先以 `realign_smart_review_candidate` 更新 JSON 的 `current_zh_tw` 及其他快照欄位，再呼叫 `apply_smart_review_manual_edit` 以新快照做 CAS。資料庫在對齊後再次變更、已有人工審核列或格式不符時不覆蓋；每筆結果分為成功、stale／不適用、格式問題或錯誤，使用者可看到進度、取消後續項目與最終摘要。批量模式仍不會自動處理 `needs_context`、`no_change`、`invalid` 或其他人工編輯狀態。

候選卡同時顯示 AI reason 與問題類型；首次載入、重新整理與批量操作後才取回 run breakdown，單純換頁只查詢當頁，避免重複做整批統計。

批量審查的 `kit.Pager` 換頁與狀態篩選在 UI 事件內同步查詢當頁，不排入 `run_background`，所以頁碼、選取狀態和卡片內容會同一次回呼更新；「全選全部可套用／可覆蓋」這類需要讀取完整範圍的動作仍保留背景執行。

審查分頁依職責分成幾個模組，`SmartReviewReviewer` 只負責 Flet 控制項與事件串接：`SmartReviewReviewerState`（`smart_review_reviewer_state.py`）保存批次、篩選、頁碼、選取候選、草稿與請求序號，每次發出會改寫畫面的讀取就以 `begin_request` 登記序號，回呼以 `is_current` 判斷是否已過期，過期結果丟棄；使用者換筆／篩選時由 `run_inline` 在事件內呼叫 `SmartReviewCandidateLoader.read_position`，只有鄰近候選的預載才在背景執行；`SmartReviewCandidateLoader` 以同一個 `moddb_smart_review_read_session` 唯讀連線完成批次資訊、統計、目前位置與前後各一筆的讀取，並以 `remember`／`take`／`invalidate` 管理預載快取（套用、保留、存草稿、換批次、換篩選或資料庫內容改變時失效；需要 `realign` 的人工編輯候選不預載）；`SmartReviewEvidencePresenter`（`smart_review_evidence.py`）把本地證據與草稿格式診斷轉成文字，並提供依狀態決定的 `not_applicable_reason`、`manual_edit_hint`、`initial_draft_text`；`SmartReviewActionService`（`smart_review_actions.py`）呼叫 service 儲存草稿、套用或保留原譯文，並把結果整理成 `ActionOutcome`（提示訊息、顏色、是否重載條目頁）。批量模式由 `SmartReviewBatchReviewer`（`smart_review_batch_reviewer.py`）持有分頁與跨頁選取狀態，使用同一個唯讀 session 載入候選；批量寫入由 `apply_moddb_smart_review_batch_from_path` 封裝，使用既有 `TranslationDB` CAS 與歷史／projection 規則，UI 不直接逐筆碰資料庫。
候選選取由 `_dedupe_same_text_rows` 依（版本、模組、種類、英文、目前譯文、來源、審核狀態）去重，被略過的條目 id 存在候選的 `twin_entry_ids`，套用時由 `_apply_smart_review_twins` 於同一交易內只對這些條目（且狀態仍相同者）一併寫入；UI 只看到代表條目。
背景結果排回畫面執行緒統一用 `app/ui/page_post.py` 的 `post_to_ui`：頁面已卸載或排程失敗時結果回呼不會執行，呼叫端以 `on_failure` 回收純背景執行中旗標（批量審核與清理對話框的 busy state、學名對話框的掃描與單筆查詢狀態、`SpeciesSuggestion._running`、`SpeciesBulkLookup.running`、`EntriesPanel._save_running`）。這些 recovery callback 可能在 worker thread 執行，不直接觸碰 Flet controls；頁面重新掛載時由目前狀態重新同步控制項，進度通知失敗不回收。批量審核的範圍選單載入屬於維護工作，排程失敗只回收自己的 `_scope_loading`，不可清掉之後才開始的批量審核 busy state。批量審核與清理對話框另以關閉世代丟棄晚到結果，避免已關閉視窗被背景工作改寫。

頁籤背景快照（`panel_refresh.py` 的 `load_panel_snapshot`、`load_entries_filter_snapshot`）透過 service 的 `moddb_read_snapshot` 取得 worker 專用唯讀連線與單一讀取交易，離開時一律 rollback 並關閉；資料世代由 `database_data_generation` 讀取，內容包含 `data_gen` 與外部 SQLite 寫入序號，view 不碰連線內部，也不可直接呼叫 `_data_gen()`。因此另一個連線完成寫入時，篩選結果與清單快照會被判定為過期並重新載入。批次取代對話框、規則頁與圖示預覽頁各自維護的分頁（前後頁、跳頁輸入）與 `kit.Pager` 的頁碼按鈕語意不同，維持各自實作並由行為測試固定。

`EntriesPanel` 的換頁與篩選透過 `ModDbView._on_entries_filter_changed` 發出背景快照：只換頁時 `page_only` 為真，worker 走 `load_entries_filter_snapshot` 且不重算下拉選項；篩選條件改變時才重算選項（`filter_options_ready`）。`apply_filter_options` 回傳選項或選值是否改變，沒有改變就不登記到 `FilterFlush`，篩選列因此不會因存檔、還原或翻頁而重畫。

只有篩選條件改變（搜尋、狀態、來源、審核狀態、智慧校對狀態、進階條件）時，`refresh_entry_filter(defer_options=True)` 讓 worker 只讀清單與總筆數（第一段，`options_deferred`）；`ModDbView._continue_filter_options` 在清單套用後把第二段交給 `FilterOptionsLoader`（`app/views/moddb/filter_options_loader.py`），它在 worker 呼叫 `panel_refresh.load_entries_options_snapshot`，結果回到畫面執行緒後由 `ModDbView._apply_entries_options` 檢查資料世代與目前條件，才經 `EntriesPanel.apply_options_snapshot` 套用並以 `flush_filter_options` 只重畫篩選列。解析後的版本／模組／類型／來源和請求不同（`scope_changed`）時改走完整重載。載入器同一時間只跑一個查詢、只保留最新 payload，並先等條件穩定（`SETTLE_SECONDS`，等待期間被新請求取代就不查）。

條目查詢的 worker 連線由 `moddb_read_snapshot(interrupt=QueryInterrupt())` 開啟：較新的請求到來時 `_request_panel_refresh` 對進行中的查詢呼叫 `cancel()`（SQLite `interrupt` 加上 `cancel_scope` 讓平行掃描的各區間停下），worker 回報 `cancelled`，`_reload_after_cancel` 以目前條件重新載入；被中斷的結果從不顯示。

`SmartReviewDialog` 切換到「審查」時只在目前批次尚未載入時啟動 `SmartReviewReviewer.load_run` 或 `SmartReviewBatchReviewer.load_run`；兩者的資料庫讀取都透過 `SmartReviewContext.run_background`，分頁本身先立即顯示載入狀態。`SmartReviewHistory.refresh` 的批次清單與「載入更多」也透過 `run_background`，以 request sequence、資料庫身分與視窗世代丟棄晚到結果；因此切換分頁不會被歷史 JSON／SQLite 讀取卡住，也不會用舊清單覆蓋新請求。

智慧校對有四個語意不同的狀態層，標籤各自維護、不互相硬併：（1）候選層級 `SmartReviewStatus`，條目列標記 `MARK_STYLES` 與審查頁 `FILTERS` 各自顯示文字，`tests/test_smart_review_status_contract.py` 要求兩者加上明確的「不標記／不可篩選」清單後涵蓋全部成員；（2）執行批次層級 `SmartReviewRunStatus`（`smart_review_run_status.py`），`run_status_label` 與 `run_status_color` 是執行頁、歷史頁與背景任務提示共用的單一來源，已取消與部分完成同為 gold；（3）批量審查單筆結果 `BatchOutcome`（`smart_review_batch_outcome.py`），`summarize_outcomes` 把每筆結果恰好歸入套用、保留、略過、失敗其中一組，未知字面值計入略過，所以「完成 N / M」恆等於分類加總；（4）條目生效審核狀態 `REVIEW_STATUS_LABELS`（未審核、人工已審核；歷史狀態 `legacy_unknown` 與人工已審核同標籤），與前三層無關。

條目列的智慧校對狀態標籤由 `smart_review_marks.py` 的 `smart_review_mark_chip` 繪製，資料來自 `TranslationDB.smart_review_marks`（`SmartReviewHistoryStore.latest_run_marks` 只完整解析最近一份可解析的批次檔，其餘批次只讀檔頭開始時間，並以檔案修改時間與大小快取；結構損壞的批次略過）；`panel_refresh.py` 的背景快照與 `EntriesPanel._load_list` 都會帶入 `smart_review_marks`，`entry_row_tile.py` 與詳情區的 `smart_chip` 只負責顯示。篩選則由 `entry_filters.py` 的 `smart_review_filter` 下拉（選項定義在 `smart_review_marks.py` 的 `SMART_REVIEW_FILTER_OPTIONS`）寫入 `EntryFilter.smart_review`；`TranslationDB._filter_sql` 以 `smart_review_entry_ids` 取得最近批次中符合狀態群組（`SMART_REVIEW_FILTER_GROUPS`）的條目 id，用 `json_each` 子查詢限制清單，所以清單、筆數、分頁與模組選項使用同一組條件。
智慧校對批次與候選不寫入翻譯 SQLite，而是放在資料庫同目錄的 `<資料庫檔名>.smart_review`：小批次使用 `<run_id>.json`，大型批次使用 `<run_id>.json` manifest 與 `<run_id>.chunks/*.json` 候選分片；checkpoint 只原子替換受影響分片，既有單檔 JSON 仍可讀取。套用候選時仍只在 SQLite 交易內寫入翻譯與正式 history audit。既有 SQLite 智慧校對批次保留讀取與審查相容性。

local context 的 evidence id 必須可回到資料庫 row 或 bundled official locale；同 key reviewed reference 要比對相同 kind／mod／key／英文原文，same-module reviewed lang glossary 可供 Patchouli、Guide、Advancement 使用，衝突資料帶限制與低信心標記。`smart_review_response_schema` 要求每筆具體 `reason` 且禁止外部 URL；`smart_review_candidate` 儲存 response reason、evidence、draft 與 snapshot。

候選與原有 `translation`／`effective` 解耦，批次 JSON 保存 effective 及 manual 快照。`apply_smart_review_candidate` 與 `save_smart_review_draft` 仍重新比對條目身份、原文、effective source/value/review/revision、manual row 與格式；任何差異都標成 `stale` 或拒絕草稿，不覆蓋目前資料；既有已審核 manual row 不能被智慧校對重設為未審核。套用只寫 `SRC_MANUAL` 的未審核列和 `smart_review_apply` history；`revert()` 與 `entry_activity` 已納入此 action。`保留原譯文` 只把候選標成 `rejected`，不觸碰翻譯與快取。

run 狀態會持久化為 `completed`、`partial`、`cancelled` 或 `failed`；請求失敗候選為 `request_failed`，尚未送出的取消候選為 `cancelled`，回應驗證失敗才是 `invalid`。UI 以 `Pager` 每頁最多 50 筆，顯示 reason／evidence／canonical format diagnostics，並允許人工編修、儲存 draft、套用、拒絕、上一筆／下一筆、輸入筆次跳轉與輸入 run id 重新開啟。審查導覽會沿用目前篩選的總筆數，只取得目標候選；載入期間會停用位置輸入與導覽按鈕，避免快速連點排入並行查詢。

候選列的狀態文字與按鈕必須分開放：帶 `expand` 的文字放進 `wrap=True` 的 `Row` 時 Flutter 無法排版，畫面會出現灰色圖塊。

## 格式診斷與進階篩選

View 不自行判斷格式，一律透過 `app/services_impl/moddb_format_diff_service` 取得 `analyze_format_diagnostics` 的 `FormatDiagnosticsResult`（使用者：`FormatDiffReview`、`FormatDiffViewer`、`RepairReviewer`、`EntriesPanel` 共用）。表格列由 `format_diff_review` 的診斷列與 `extra_guide_issues` 列組成，高亮、標籤與色調共用 `format_issue_rendering`；新增種類只需在診斷層登記策略，不需要在 view 加分支。

`AdvancedFilters` 的 `sort_by` 由排序下拉值與方向狀態組合：`default`／`default_desc` 與各日期排序的 `*_newest`／`*_oldest`；日期欄位由 `picked_date` 轉成本地日期，日期條件在「時間依據」為不限時間時會自動改用條目匯入時間。。清單因日期範圍而為空時，`EntriesPanel._render_list` 呼叫 `AdvancedFilters.announce_no_data`：由 `TranslationDB.time_data_range` 查出該時間依據實際最早／最晚時間，顯示在空狀態並彈出提示。

會影響結果範圍的條件同時作用於模組與目前生效來源選單：模組、來源、類型、條目狀態、審核狀態、智慧校對狀態、搜尋與進階時間／品質都由 `panel_refresh._resolve_entries_scope` 交給 `TranslationDB.filter_option_values` 使用與條目列相同的 predicate。它會保留使用者目前選定、但在新條件下沒有交集的模組／來源，讓查詢結果正確顯示為空；不可因為選單沒有交集而清除條件並放寬到其他範圍。

## 修復結果逐筆審查

`RepairReviewer`（`repair_reviewer.py`）只負責畫面與草稿：寫入、原因分類與重設基準都在 `app/services_impl/moddb_retranslate_service`。`apply_and_record_repair_review` 回傳 `applied`、`already_applied`、`stale` 或 `stale:<原因>`（原因由 `_diagnose_stale_review_item` 依資料庫現值判斷），View 依原因顯示說明；`rebase_repair_review_item` 以現值重設審查基準，供「重新讀取」使用。已套用與保留舊譯文的項目是完成狀態，草稿唯讀，不會被編輯或重新讀取降回待審查。審查項目狀態（`pending`／`kept_old`／`applied`／`stale`／`invalid`）、套用結果與未寫入原因集中定義在 `translation_tool/translation_db/repair_status.py`（`RepairReviewStatus`、`RepairApplyOutcome`、`RepairStaleReason`），值即 sidecar 檔存的字串；它與智慧校對候選的狀態是兩套詞彙，不可混用，View 與 service 不寫裸字面值（`tests/test_repair_status_contract.py`），View 經 `moddb_repair_review_store`、`moddb_retranslate_service` 取用。

## 維護邊界

### 翻譯範圍計數與批次規劃

`panel_refresh._load_translate_snapshot` 在背景刷新時只讀取版本／模組清單、未翻譯筆數與可沿用筆數，避免每次範圍變更都載入整個版本並建立翻譯 session。`TranslatePanel` 以這些數字顯示快速批次估算；按下預覽或開始機翻後，`moddb_translate_service` 才建立完整 items、附加同模組 session 與已審核術語，並由共用 planner 產生精確批次數。預覽與實際翻譯仍使用相同的批次規劃路徑；格式修復的即時進度會快取剩餘 canonical plan，只有批次容量、token 預算或 session history 使下一批大小改變時才重新規劃剩餘範圍。

`TranslatePanel` 依 `summary["status"]` 顯示結果：`CANCELLED` 為「已取消」、`DONE_WITH_WARNINGS` 為「機翻完成（有警告）」（有品質拒絕、來源回填、MAX_TOKENS、回應格式錯誤或未解決的品質重試時）、其他非 `DONE` 為「未完成（狀態）」。`TaskSession.status` 仍只有 `DONE`／`ERROR`。畫面日誌每批一行，完整品質清單由 `moddb_translate_report` 寫入 `logging.log_dir` 下的報告檔，畫面只顯示數量與路徑。

- `app/services_impl/moddb_species_service.py` 不依賴 Flet，只處理候選資料結構、資料庫分頁與學名規則。
- `app/views/moddb/species_dialog.py` 只處理視窗控制項、掃描與單筆查詢的背景作業，以及結果呈現。
- `app/views/moddb/species_bulk_lookup.py` 負責批次查詢的背景工作、任務膠囊進度、停止條件與事件通知，不持有任何控制項。
- 學名快取的網路請求與快取檔案格式由 `translation_tool/utils/species_cache.py` 管理（相同學名同時查詢只送一次請求，等待者共用同一次結果，包括查無與暫時失敗；暫時失敗不寫入快取，之後仍可重試。快取寫入有鎖）；Mod DB 功能不重複實作 API 或快取。
