# 智慧校對候選狀態機

候選（candidate）狀態的唯一定義在 `translation_tool/translation_db/smart_review_status.py`。
資料層（SQLite、JSON history）、服務層與 UI 都從 `SmartReviewStatus` 取得狀態名稱，
不得在其他模組硬寫狀態字串；`tests/test_smart_review_status.py` 會掃描並擋下。

`SmartReviewStatus` 是 `StrEnum`，值與 JSON／SQLite 內保存的字串相同，所以既有資料不需遷移，
`status == "ready"` 這類比較仍成立。未知字串由 `coerce_status()` 原樣保留（相容其他版本寫入的資料）。

## 狀態與分組

| 分組 | 內容 | 用途 |
|---|---|---|
| `PROCESSED_STATUSES` | ready、no_change、needs_context、invalid、request_failed | AI 已處理的計數 |
| `VALID_STATUSES` | ready、no_change、needs_context | AI 回應通過驗證 |
| `MANUAL_EDIT_STATUSES` | applied、rejected、needs_context、no_change、invalid、invalid_format、stale | 審查者可手動編輯並覆蓋套用 |
| `DECISION_STATUS` | no_change→no_change、suggest_revision→ready、needs_context→needs_context | AI decision 對應狀態 |

## 允許的狀態轉移

`TRANSITIONS` 為來源 → 可到達目標：

- `pending`：只會被 AI 處理結果（ready／no_change／needs_context／invalid／request_failed）或 `cancelled` 取代。
- `ready`：人工 `applied`／`rejected`，或套用當下偵測到 `stale`／`invalid_format`。
- `no_change`、`needs_context`、`invalid`：可人工 `applied`（覆蓋），或重新執行得到新的 AI 結果。
- `request_failed`、`cancelled`：只能由重新執行得到新的 AI 結果。
- `applied`、`rejected`、`stale`、`invalid_format`：只能再人工 `applied`。
- 只有 `ready` 可以 `rejected`；`pending` 不是任何轉移的目標。

`can_transition()` 回傳是否允許，`validate_transition()` 對非法轉移丟出 `InvalidStatusTransition`
（`reason` 可寫入 audit）。套用與保留原譯文仍由 CAS／revision 檢查把關，狀態機不取代這些防護。

## JSON 與 SQLite 的一致性協議

兩個儲存之間沒有共同交易，邊界如下：

- **SQLite `history` 的 `smart_review_apply` audit 是「寫入事實」**：套用先提交 SQLite（含 CAS 檢查），再回寫 JSON；JSON 還未確認時 `projection_state` 為 `pending`，對帳成功後才改為 `projected`。
- **JSON 是「候選狀態投影」**：保存候選、狀態與驗證結果；完整候選不放進 SQLite，避免資料庫檔案膨脹。
- 每次 JSON 候選變更都會遞增批次內的 `projection.revision`，並記錄 `projection.state`（`ProjectionState`）：
  `projected`（依最近一次寫入更新）、`reconciled`（由對帳依 audit 補回）；
  `pending_projection`（audit 已提交、JSON 尚未反映）與 `projection_failed`（回寫失敗並放棄本次重試）
  只會出現在診斷回報，因為這時 JSON 本來就寫不進去。
- 對帳 `reconcile_applied` 具冪等性：已標成同一次套用的候選不再改寫，重複呼叫不改任何檔案。
  讀取標籤與候選列表時，若 `history` 的最大 id 比上次對帳新，會先對帳。
- `history` 的每條目五筆保留政策不會刪除 `pending` marker；因此即使 JSON 回寫失敗後同一條目又有五次歷史寫入，下一次讀取仍能完成對帳。確認 JSON 後 marker 轉為 `projected`，再回到一般保留政策。
- `TranslationDB.smart_review_projection_report(run_id)` 唯讀比對 audit 與 JSON，回傳
  `state`、`revision` 與 `pending_ordinals`。

## 行程內快取

- `SmartReviewHistoryStore.clear_caches(directory=None)`：清除起始時間、最新標記、損壞批次與復原掃描快取。
- `clear_smart_review_caches(db_path=None)`：清除對帳進度（只影響效能，下一次會重新對帳）。

兩者都不動磁碟資料；測試隔離、切換大量資料庫或外部程序改寫歷史檔後呼叫。

## 批次執行器的責任邊界

`CommonBatchExecutor` 只管批次生命週期（識別碼、事件、取消檢查、計時、摘要）。
retry 條件、配額等待、context 過大縮批與 validation repair 由機翻、本地修復與智慧校對各自決定。
