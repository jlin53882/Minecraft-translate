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

條目校對的 `species_scan_btn` 依目前 `version`、`mod_id` 與 `kind` 開啟 `SpeciesCandidateDialog`。對話框分成兩個階段：

1. `scan_species_candidates` 以 `QualityFilter` 的 `species_name`（預設）或 `species_format`（對話框的放寬選項）呼叫 `TranslationDB.list_entries`，由 SQL 預篩加 `is_species_candidate` 判斷取出候選，不逐筆載入整個版本；候選不設上限，對話框用 `kit.Pager` 每頁 `PAGE_SIZE` 筆顯示。`species_name` 要求值符合 `is_potential_species_name` 的格式，且 key 有 `has_species_key_hint` 的提示詞（`SPECIES_KEY_HINTS`）或值含雜交／亞種／變種記號。
2. 使用者按單筆「查詢」或「查詢全部候選」後，背景作業才呼叫 `lookup_species`。批次查詢屬於頁面而不是對話框：`bulk_lookup_for(page)` 回傳頁面共用的 `SpeciesBulkLookup`，它以 `tag_session` 建立的 `TaskSession`（`view_key="moddb"`）和 `launch_page_operation` 執行，可由 `SpeciesBulkLookup.stop` 或頂列任務取消；對話框與 `SpeciesSuggestion` 透過 `add_listener` 以弱參照訂閱 `lookup`／`finished` 事件，「背景執行」（`_run_in_background`）、「開啟條目」與 Esc 關閉只退訂；「關閉」（`_close`）先呼叫 `SpeciesBulkLookup.stop` 再關閉。結果是 `SpeciesLookup`（`found`／`not_found`／`rate_limited`／`error`），回到 UI 執行緒顯示；不會寫入 `entry`、`translation` 或 `effective`。

條目校對的建議區另有「學名」分頁，由 `app/views/moddb/species_suggestion.py` 的 `SpeciesSuggestion` 負責：只讀 `cached_species_lookup`（不連線），查詢時才用 `lookup_species`，結果回到 UI 後經 `EntriesPanel._on_species_changed` 重繪；「套用」呼叫 `EntriesPanel._apply_suggestion` 只更新譯文框。

每個候選列同時提供 `開啟條目`，透過 `EntriesPanel.select` 回到編輯器。掃描與查詢使用 `launch_page_operation`、`CommitPolicy.EPHEMERAL` 與 generation 檢查；視窗關閉或範圍過期時丟棄晚到結果。批次查詢使用 `CancellationPolicy.COOPERATIVE`，每筆查詢前檢查 `_cancel` 事件；`launch_page_operation` 拒絕啟動時由呼叫端還原忙碌狀態。每次只重建完成的那一筆候選列，批次進度由 `_flush` 節流重繪。

候選列的狀態文字與按鈕必須分開放：帶 `expand` 的文字放進 `wrap=True` 的 `Row` 時 Flutter 無法排版，畫面會出現灰色圖塊。

## 格式診斷與進階篩選

View 不自行判斷格式，一律透過 `app/services_impl/moddb_format_diff_service` 取得 `analyze_format_diagnostics` 的 `FormatDiagnosticsResult`（使用者：`FormatDiffReview`、`FormatDiffViewer`、`RepairReviewer`、`EntriesPanel` 共用）。表格列由 `format_diff_review` 的診斷列與 `extra_guide_issues` 列組成，高亮、標籤與色調共用 `format_issue_rendering`；新增種類只需在診斷層登記策略，不需要在 view 加分支。

`AdvancedFilters` 的 `sort_by` 由排序下拉值與方向狀態組合：`default`／`default_desc` 與各日期排序的 `*_newest`／`*_oldest`；日期欄位由 `picked_date` 轉成本地日期，日期條件在「時間依據」為不限時間時會自動改用條目匯入時間。

## 維護邊界

### 翻譯範圍計數與批次規劃

`panel_refresh._load_translate_snapshot` 在背景刷新時只讀取版本／模組清單、未翻譯筆數與可沿用筆數，避免每次範圍變更都載入整個版本並建立翻譯 session。`TranslatePanel` 以這些數字顯示快速批次估算；按下預覽或開始機翻後，`moddb_translate_service` 才建立完整 items、附加同模組 session 與已審核術語，並由共用 planner 產生精確批次數。預覽與實際翻譯仍使用相同的批次規劃路徑；格式修復的即時進度會快取剩餘 canonical plan，只有批次容量、token 預算或 session history 使下一批大小改變時才重新規劃剩餘範圍。

- `app/services_impl/moddb_species_service.py` 不依賴 Flet，只處理候選資料結構、資料庫分頁與學名規則。
- `app/views/moddb/species_dialog.py` 只處理視窗控制項、掃描與單筆查詢的背景作業，以及結果呈現。
- `app/views/moddb/species_bulk_lookup.py` 負責批次查詢的背景工作、任務膠囊進度、停止條件與事件通知，不持有任何控制項。
- 學名快取的網路請求與快取檔案格式由 `translation_tool/utils/species_cache.py` 管理（相同學名同時查詢只送一次請求，等待者共用同一次結果，包括查無與暫時失敗；暫時失敗不寫入快取，之後仍可重試。快取寫入有鎖）；Mod DB 功能不重複實作 API 或快取。
