# Mod DB View 架構

`ModDbView`（`app/views/moddb_view.py`）維持資料庫連線與頁籤生命週期；`EntriesPanel`（`app/views/moddb/entries_panel.py`）負責條目篩選、編輯與背景刷新。資料庫查詢與寫入經由 `app/services_impl/moddb_service.py`，核心資料存取由 `translation_tool/translation_db/` 提供。

## 學名候選流程

條目校對的 `species_scan_btn` 依目前 `version`、`mod_id` 與 `kind` 開啟 `SpeciesCandidateDialog`。對話框分成兩個階段：

1. `scan_species_candidates` 分頁讀取 `TranslationDB.list_entries`，用 `is_species_candidate` 判斷：值要符合 `is_potential_species_name` 的格式，且 key 有 `has_species_key_hint` 的提示詞（`SPECIES_KEY_HINTS`）或值含雜交／亞種／變種記號；`require_key_hint=False`（對話框的放寬選項）時只看格式。最多保留 500 筆候選。
2. 使用者按單筆「查詢」或「查詢全部候選」後，背景作業才呼叫 `lookup_species`。結果是 `SpeciesLookup`（`found`／`not_found`／`rate_limited`／`error`），回到 UI 執行緒顯示；不會寫入 `entry`、`translation` 或 `effective`。

每個候選列同時提供 `開啟條目`，透過 `EntriesPanel.select` 回到編輯器。掃描與查詢使用 `launch_page_operation`、`CommitPolicy.EPHEMERAL` 與 generation 檢查；視窗關閉或範圍過期時丟棄晚到結果。批次查詢使用 `CancellationPolicy.COOPERATIVE`，每筆查詢前檢查 `_cancel` 事件；`launch_page_operation` 拒絕啟動時由呼叫端還原忙碌狀態。每次只重建完成的那一筆候選列，批次進度由 `_flush` 節流重繪。

候選列的狀態文字與按鈕必須分開放：帶 `expand` 的文字放進 `wrap=True` 的 `Row` 時 Flutter 無法排版，畫面會出現灰色圖塊。

## 維護邊界

- `app/services_impl/moddb_species_service.py` 不依賴 Flet，只處理候選資料結構、資料庫分頁與學名規則。
- `app/views/moddb/species_dialog.py` 只處理視窗控制項、背景作業生命週期與結果呈現。
- 學名快取的網路請求與快取檔案格式由 `translation_tool/utils/species_cache.py` 管理（相同學名同時查詢只送一次請求，快取寫入有鎖）；Mod DB 功能不重複實作 API 或快取。
