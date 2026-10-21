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

## 學名候選流程

`SpeciesCandidateDialog` 開啟時先顯示呼叫端帶入的預填值，再以維護型背景作業依學名候選品質條件建立模組清單與目前生效來源對照；模組切換只重用已載入的對照，不重新掃描資料庫。切換範圍只清除舊掃描結果，不會自動重新掃描；關閉視窗會丟棄晚到的範圍結果。

條目校對的 `species_scan_btn` 依目前 `version`、`mod_id` 與 `kind` 開啟 `SpeciesCandidateDialog`。對話框分成兩個階段：

1. `scan_species_candidates` 以 `QualityFilter` 的 `species_name`（預設）或 `species_format`（對話框的放寬選項）呼叫 `TranslationDB.list_entries`，由 SQL 預篩加 `is_species_candidate` 判斷取出候選，不逐筆載入整個版本；候選不設上限，對話框用 `kit.Pager` 每頁 `PAGE_SIZE` 筆顯示。`species_name` 要求值符合 `is_potential_species_name` 的格式，且 key 有 `has_species_key_hint` 的提示詞（`SPECIES_KEY_HINTS`）或值含雜交／亞種／變種記號。
2. 使用者按單筆「查詢」或「查詢全部候選」後，背景作業才呼叫 `lookup_species`。批次查詢屬於頁面而不是對話框：`bulk_lookup_for(page)` 回傳頁面共用的 `SpeciesBulkLookup`，它以 `tag_session` 建立的 `TaskSession`（`view_key="moddb"`）和 `launch_page_operation` 執行，可由 `SpeciesBulkLookup.stop` 或頂列任務取消；對話框與 `SpeciesSuggestion` 透過 `add_listener` 以弱參照訂閱 `lookup`／`finished` 事件，「背景執行」（`_run_in_background`）、「開啟條目」與 Esc 關閉只退訂；「關閉」（`_close`）先呼叫 `SpeciesBulkLookup.stop` 再關閉。結果是 `SpeciesLookup`（`found`／`not_found`／`rate_limited`／`error`），回到 UI 執行緒顯示；不會寫入 `entry`、`translation` 或 `effective`。

條目校對的建議區另有「學名」分頁，由 `app/views/moddb/species_suggestion.py` 的 `SpeciesSuggestion` 負責：只讀 `cached_species_lookup`（不連線），查詢時才用 `lookup_species`，結果回到 UI 後經 `EntriesPanel._on_species_changed` 重繪；「套用」呼叫 `EntriesPanel._apply_suggestion` 只更新譯文框。

每個候選列同時提供 `開啟條目`，透過 `EntriesPanel.select` 回到編輯器。掃描與查詢使用 `launch_page_operation`、`CommitPolicy.EPHEMERAL` 與 generation 檢查；視窗關閉或範圍過期時丟棄晚到結果。批次查詢使用 `CancellationPolicy.COOPERATIVE`，每筆查詢前檢查 `_cancel` 事件；`launch_page_operation` 拒絕啟動時由呼叫端還原忙碌狀態。每次只重建完成的那一筆候選列，批次進度由 `_flush` 節流重繪。

## AI 智慧校對候選

批量審核與 Smart Review 的範圍控制項都只顯示真正有未審核候選的模組；來源選項依選定模組重建。開啟對話框、切換版本／類型／品質條件時，模組與來源範圍查詢使用維護型背景作業；模組切換只重用已載入的來源映射，不再次查詢整個資料庫。查詢結果以序號驗證，舊結果不會覆蓋新條件；範圍變更只更新預填值、來源選單與筆數估算，不能繞過使用者明確按下預覽、掃描或開始按鈕。

`entry_filters.py` 的 `批量審核` 按鈕只建立 `BulkReviewDialog`，並把 EntriesPanel 目前的版本、模組與 effective source 預填進對話框；預填不會自動產生預覽或執行，使用者仍須明確按下預覽與確認。`智慧校對` 按鈕只建立 `SmartReviewDialog`，並把 EntriesPanel 的版本、模組、kind、effective source、未審核狀態與搜尋條件帶入；不把模型流程塞入 `EntriesPanel`。視窗只是外殼，三個分頁各自一個模組：`SmartReviewRunPanel`（範圍設定、進度、統計與 log）、`SmartReviewReviewer`（每頁一筆的逐筆審查）、`SmartReviewHistory`（歷史批次）。`SmartReviewContext` 集中保存 UI 世代與資料庫身分，所有背景工作透過 `run_background` 啟動，結果只在世代、資料庫與呼叫端的 `still_valid` 都仍成立時才回到 UI。校對本身由 `SmartReviewJob` 執行（頁面層級單例，`job_for_page`），以 `TaskSession` 登記進度與 log，關閉視窗不會中斷。服務層 `moddb_smart_review_service.py` 重新以資料庫 path、priority 與 config 開啟連線；`smart_review.py` 負責候選 snapshot、typed local context、provider structured output、JSON 批次持久化與 CAS apply。預覽使用唯讀連線，執行與套用透過 `launch_page_operation` 在背景進行。
智慧校對套用成功後，`open_smart_review` 會先以 `refresh_entry_filter(panel, keep_selection=True)` 重載目前條目頁，再通知 `ModDbView` 外層狀態變更，確保條目列表旁的來源／審核標籤與中央編輯器同步。
`SmartReviewRunPanel` 開啟或變更範圍時，會以唯讀 count query 顯示符合範圍的未審核 AI 譯文總數、本次上限與批次數；上限輸入 `0` 代表不限，每批筆數仍必須大於 `0`。機翻、既有譯文修復與 Smart Review 都透過 `translation_tool/core/common_batch_executor.py` 的共用批次生命週期回報安全結構化事件；各自的 candidate、prompt、provider retry、validation、CAS／快取／JSON 寫入仍由專屬策略保留。Smart Review 事件分別記錄 batch start、外層 attempt、provider API、底層 network attempt、HTTP／timeout／connection 錯誤分類、Retry-After、配額等待、retry backoff、回應解析與 JSON 寫入耗時；重試等待期間仍透過 `TaskSession` 更新 UI，不把 retry backoff 混入配額等待。完成時再寫入 selection／planning／prompt／API／配額／retry／解析／驗證／寫入／total 的 monotonic timing、valid／invalid／needs_context／no_change／suggest_revision 與 validation error 分布。事件不得包含 API key、完整 prompt、完整 response 或錯誤原文。
`SmartReviewReviewer` 依候選狀態分兩條套用路徑：`ready` 走 `apply_smart_review_candidate`；`SMART_REVIEW_MANUAL_EDIT_STATUSES` 內的狀態（已套用、已保留原譯文、需補 context、不用改、無效、格式不符、已過期）走 `apply_smart_review_manual_edit`，載入時先以 `realign_smart_review_candidate` 對齊快照，並由 `is_manual_edit_row`、`initial_draft_text`、`manual_edit_hint` 決定草稿起點與提示。人工編輯路徑的 CAS 比對對齊後的快照，快照改變（含英文原文）只回傳 `changed`、不把候選降成 `stale`；CAS 先於格式檢查，格式檢查使用交易內的最新原文。SQLite 寫入後批次 JSON 回寫失敗時回傳 `state_sync_failed`，`_after_action` 仍重載條目頁並顯示專屬訊息，後續由 `reconcile_applied` 補回狀態。
候選選取由 `_dedupe_same_text_rows` 依（版本、模組、種類、英文、目前譯文、來源、審核狀態）去重，被略過的條目 id 存在候選的 `twin_entry_ids`，套用時由 `_apply_smart_review_twins` 於同一交易內只對這些條目（且狀態仍相同者）一併寫入；UI 只看到代表條目。
條目列的智慧校對狀態標籤由 `smart_review_marks.py` 的 `smart_review_mark_chip` 繪製，資料來自 `TranslationDB.smart_review_marks`（`SmartReviewHistoryStore.latest_run_marks` 只完整解析最近一份可解析的批次檔，其餘批次只讀檔頭開始時間，並以檔案修改時間與大小快取；結構損壞的批次略過）；`panel_refresh.py` 的背景快照與 `EntriesPanel._load_list` 都會帶入 `smart_review_marks`，`entry_row_tile.py` 與詳情區的 `smart_chip` 只負責顯示。篩選則由 `entry_filters.py` 的 `smart_review_filter` 下拉（選項定義在 `smart_review_marks.py` 的 `SMART_REVIEW_FILTER_OPTIONS`）寫入 `EntryFilter.smart_review`；`TranslationDB._filter_sql` 以 `smart_review_entry_ids` 取得最近批次中符合狀態群組（`SMART_REVIEW_FILTER_GROUPS`）的條目 id，用 `json_each` 子查詢限制清單，所以清單、筆數、分頁與模組選項使用同一組條件。
智慧校對批次與候選不寫入翻譯 SQLite，而是放在資料庫同目錄的 `<資料庫檔名>.smart_review/<run_id>.json`；套用候選時仍只在 SQLite 交易內寫入翻譯與正式 history audit。既有 SQLite 智慧校對批次保留讀取與審查相容性。

local context 的 evidence id 必須可回到資料庫 row 或 bundled official locale；同 key reviewed reference 要比對相同 kind／mod／key／英文原文，same-module reviewed lang glossary 可供 Patchouli、Guide、Advancement 使用，衝突資料帶限制與低信心標記。`smart_review_response_schema` 要求每筆具體 `reason` 且禁止外部 URL；`smart_review_candidate` 儲存 response reason、evidence、draft 與 snapshot。

候選與原有 `translation`／`effective` 解耦，批次 JSON 保存 effective 及 manual 快照。`apply_smart_review_candidate` 與 `save_smart_review_draft` 仍重新比對條目身份、原文、effective source/value/review/revision、manual row 與格式；任何差異都標成 `stale` 或拒絕草稿，不覆蓋目前資料；既有已審核 manual row 不能被智慧校對重設為未審核。套用只寫 `SRC_MANUAL` 的未審核列和 `smart_review_apply` history；`revert()` 與 `entry_activity` 已納入此 action。`保留原譯文` 只把候選標成 `rejected`，不觸碰翻譯與快取。

run 狀態會持久化為 `completed`、`partial`、`cancelled` 或 `failed`；請求失敗候選為 `request_failed`，尚未送出的取消候選為 `cancelled`，回應驗證失敗才是 `invalid`。UI 以 `Pager` 每頁最多 50 筆，顯示 reason／evidence／canonical format diagnostics，並允許人工編修、儲存 draft、套用、拒絕、上一筆／下一筆、輸入筆次跳轉與輸入 run id 重新開啟。審查導覽會沿用目前篩選的總筆數，只取得目標候選；載入期間會停用位置輸入與導覽按鈕，避免快速連點排入並行查詢。

候選列的狀態文字與按鈕必須分開放：帶 `expand` 的文字放進 `wrap=True` 的 `Row` 時 Flutter 無法排版，畫面會出現灰色圖塊。

## 格式診斷與進階篩選

View 不自行判斷格式，一律透過 `app/services_impl/moddb_format_diff_service` 取得 `analyze_format_diagnostics` 的 `FormatDiagnosticsResult`（使用者：`FormatDiffReview`、`FormatDiffViewer`、`RepairReviewer`、`EntriesPanel` 共用）。表格列由 `format_diff_review` 的診斷列與 `extra_guide_issues` 列組成，高亮、標籤與色調共用 `format_issue_rendering`；新增種類只需在診斷層登記策略，不需要在 view 加分支。

`AdvancedFilters` 的 `sort_by` 由排序下拉值與方向狀態組合：`default`／`default_desc` 與各日期排序的 `*_newest`／`*_oldest`；日期欄位由 `picked_date` 轉成本地日期，日期條件在「時間依據」為不限時間時會自動改用條目匯入時間。。清單因日期範圍而為空時，`EntriesPanel._render_list` 呼叫 `AdvancedFilters.announce_no_data`：由 `TranslationDB.time_data_range` 查出該時間依據實際最早／最晚時間，顯示在空狀態並彈出提示。

進階條件同時作用於模組與目前生效來源選單。`panel_refresh._resolve_entries_scope` 會保留使用者目前選定、但在新條件下沒有交集的模組／來源，讓查詢結果正確顯示為空；不可因為選單沒有交集而清除條件並放寬到其他範圍。

## 修復結果逐筆審查

`RepairReviewer`（`repair_reviewer.py`）只負責畫面與草稿：寫入、原因分類與重設基準都在 `app/services_impl/moddb_retranslate_service`。`apply_and_record_repair_review` 回傳 `applied`、`already_applied`、`stale` 或 `stale:<原因>`（原因由 `_diagnose_stale_review_item` 依資料庫現值判斷），View 依原因顯示說明；`rebase_repair_review_item` 以現值重設審查基準，供「重新讀取」使用。已套用與保留舊譯文的項目是完成狀態，草稿唯讀，不會被編輯或重新讀取降回待審查。

## 維護邊界

### 翻譯範圍計數與批次規劃

`panel_refresh._load_translate_snapshot` 在背景刷新時只讀取版本／模組清單、未翻譯筆數與可沿用筆數，避免每次範圍變更都載入整個版本並建立翻譯 session。`TranslatePanel` 以這些數字顯示快速批次估算；按下預覽或開始機翻後，`moddb_translate_service` 才建立完整 items、附加同模組 session 與已審核術語，並由共用 planner 產生精確批次數。預覽與實際翻譯仍使用相同的批次規劃路徑；格式修復的即時進度會快取剩餘 canonical plan，只有批次容量、token 預算或 session history 使下一批大小改變時才重新規劃剩餘範圍。

`TranslatePanel` 依 `summary["status"]` 顯示結果：`CANCELLED` 為「已取消」、`DONE_WITH_WARNINGS` 為「機翻完成（有警告）」（有品質拒絕、來源回填、MAX_TOKENS、回應格式錯誤或未解決的品質重試時）、其他非 `DONE` 為「未完成（狀態）」。`TaskSession.status` 仍只有 `DONE`／`ERROR`。畫面日誌每批一行，完整品質清單由 `moddb_translate_report` 寫入 `logging.log_dir` 下的報告檔，畫面只顯示數量與路徑。

- `app/services_impl/moddb_species_service.py` 不依賴 Flet，只處理候選資料結構、資料庫分頁與學名規則。
- `app/views/moddb/species_dialog.py` 只處理視窗控制項、掃描與單筆查詢的背景作業，以及結果呈現。
- `app/views/moddb/species_bulk_lookup.py` 負責批次查詢的背景工作、任務膠囊進度、停止條件與事件通知，不持有任何控制項。
- 學名快取的網路請求與快取檔案格式由 `translation_tool/utils/species_cache.py` 管理（相同學名同時查詢只送一次請求，等待者共用同一次結果，包括查無與暫時失敗；暫時失敗不寫入快取，之後仍可重試。快取寫入有鎖）；Mod DB 功能不重複實作 API 或快取。
