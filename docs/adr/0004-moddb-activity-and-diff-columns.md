# ADR-0004：最近人工活動與「版本不同」改讀觸發器維護的欄位

- 日期：2026-10-15
- 狀態：實作
- 基準：`1f5d146`（ADR-0003 的統計摘要）

## 背景

ADR-0003 之後，條目清單仍有三類查詢要對整個版本逐筆計算（70 萬條目的備份，
1.20.1 版約 27 萬條目，新 process、新連線）：

- 狀態「版本不同」「人工」「其他譯文」：每個條目做一次跨版本的關聯 `EXISTS`
  （同內容其他版本的生效譯文是否不同）。
- 「最近人工活動」排序：每個條目彙總 `MAX(history.at)` 後整個版本暫存排序。
- 「目前譯文更新時間」排序：連接 `translation` 後整個版本暫存排序。

## 決策

### `entry_activity`（`translation_tool/translation_db/entry_activity.py`）

`entry_activity(entry_id, mc_version, last_at)`，索引 `(mc_version, last_at DESC, entry_id)`。
`history` 正常只會新增（條目刪除時串聯刪除）：`AFTER INSERT ON history` 觸發器
對 `ACTIVITY_ACTIONS`（與排序、時間篩選「全部」共用的同一組動作）做 upsert、取較大時間。
單筆紀錄被外部 SQL 修改或刪除時，更新與刪除觸發器依剩餘紀錄重算該條目的最近活動時間
（沒有符合的紀錄就移除該列）；條目刪除由外鍵串聯刪除，條目改版本時同步版本欄。

### `effective` 衍生欄位（`translation_tool/translation_db/effective_columns.py`）

`effective` 新增 `mc_version`、`diff`、`upd`，索引 `(mc_version, diff, source)` 與
`(mc_version, upd DESC, entry_id)`。

- `diff` 與清單原本的定義相同。同一版本內同內容只有一個條目，所以等同「同內容各條目的
  生效譯文不只一種」：觸發器以一次 `COUNT(DISTINCT zh_tw)` 重算整組，全庫重算以一次
  `GROUP BY` 找出不一致的組。
- 觸發器：生效列新增、改文字、刪除時重算同內容組；改來源或譯文 `updated_at`、譯文新增或
  刪除時同步 `upd`。刪除條目時 SQLite 先刪條目再串聯刪除生效列，生效列的刪除觸發器已找
  不到條目，所以由條目的 `BEFORE DELETE` 觸發器把它排除後重算同組；條目內容或版本改變時
  變動前重算舊組（排除自己）、變動後重算新組。
- 外部 SQL 改變生效列的條目編號、或譯文列的條目／來源時，欄位無法逐項調整：
  `effcol_effective_move`、`effcol_translation_identity` 把 `effective_columns_valid` 設為 0，
  讀取改用原本的查詢，下次可寫開啟時重建。
- 整批重建生效譯文以 `meta.effective_columns_frozen` 暫停觸發器（與 `stats_frozen` 並用），
  插入時直接帶入版本與更新時間，結束時全庫比對重算一次。重建改為「刪除已無譯文的條目＋
  upsert 只改寫結果不同的列」，結果與清空後重插相同，但優先序只影響部分條目時寫入少得多，
  抵銷了新索引的維護成本。

### 讀取端

- 「版本不同」「人工」「其他譯文」改用 `f.diff`；這三種狀態的筆數從 `effective` 的版本索引
  計數，條件用到條目欄位時才連接條目。
- 兩種時間排序分兩段：第一段沿衍生表的版本索引取有時間的條目（條件中的條目版本改用衍生表的
  版本欄，條目以 `CROSS JOIN` 接在後面以固定沿索引讀取），第二段依 entry id 取其餘條目；
  第一段整頁落空時以第一段的總筆數換算第二段的起點。新增 `idx_entry_version`
  （`entry(mc_version)`）讓第二段沿索引依編號讀取。兩段的查詢計畫都沒有暫存排序。
- 衍生資料不屬於 `schema_version`，以各自的 `meta` 版本與有效旗標標記；可寫開啟時缺少、
  無效或觸發器定義不同就在單一交易內安裝並重算。唯讀開啟的舊資料庫、旗標無效或暫停中
  時，改用原本的查詢。

## 取捨與風險

- 較舊的程式開啟已安裝的資料庫仍會經由觸發器維護欄位，但它的整批重建不會暫停觸發器，
  每列都重算同內容組，70 萬條目會慢很多。
- 不可對 `effective` 使用 `INSERT OR REPLACE`（刪除觸發器不會執行）；upsert 的
  `DO UPDATE` 會執行更新觸發器。
- `diff` 依賴「同版本同內容只有一個條目」，這由 `entry` 的唯一鍵保證。

## 正確性保護

`tests/test_translation_db_effective_columns.py` 與 `tests/test_translation_db_entry_activity.py`
比對每種狀態、排序、篩選與偏移（含第一段／第二段交界）在衍生資料與原本查詢下的 ids、
`diff` 與總筆數，檢查分頁與筆數查詢沒有暫存排序，逐一移除每個衍生欄位觸發器時至少一步驗證
失敗，並測試串聯刪除、原文改變、暫停與交易回復、唯讀舊資料庫退回、無效旗標與重建、觸發器
定義過期。`tests/test_translation_db_stat_summary.py` 的共用檢查在每條寫入路徑後一併呼叫
兩者的 `verify()`。
