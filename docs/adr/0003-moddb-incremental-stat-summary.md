# ADR-0003：全庫統計改讀觸發器維護的摘要表

- 日期：2026-10-15
- 狀態：實作
- 基準：PR #201，`bc4c3c8`

## 背景

總覽頁的三項全庫統計（各版本生效來源分桶、各版本條目分布、總覽數字）每次都以
`entry LEFT JOIN effective` 掃描全部條目。ADR-0002 的快取讓資料沒變時直接回傳，
但任何寫入都會遞增資料世代，下一次讀取就要重算。70 萬條目的備份上，三項首次查詢
每次手動儲存後回到總覽都要付這個成本。

## 決策

新增衍生資料表 `stat_src`（版本 × 生效來源 × 審核狀態 → 條目數，沒有生效譯文記為
來源 `-1`、狀態 `''`）與 `stat_counter`（`content`、`mods`、`no_source`、`diff`），
實作在 `translation_tool/translation_db/stat_summary.py`。

- `entry` 與 `effective` 的觸發器在每次寫入時遞增維護。`effective` 的刪除觸發器只在
  條目仍存在時計算：刪除條目時 SQLite 先刪條目再串聯刪除生效列，條目的
  `BEFORE DELETE` 觸發器已一次扣掉條目與其生效譯文。`diff` 以「同內容其他條目的
  生效譯文筆數、最小值、最大值」判斷加入或移出一筆譯文是否讓該組在一致與不一致之間轉換。
- 觸發器都以 `meta.stats_frozen` 不存在為條件。整批重建生效譯文（`_rebuild_effective`）
  在同一交易內設旗標、改寫、以四句 `INSERT … SELECT` 重算摘要、清旗標。不可對
  `effective` 使用 `INSERT OR REPLACE`，否則刪除觸發器不會執行。
- 摘要不屬於 `schema_version`：以 `meta.stats_schema_version` 與 `meta.stats_valid`
  標記。可寫開啟時若摘要缺少、無效或觸發器定義與程式不同，就在單一交易內建立並重算；
  摘要是可隨時重算的衍生資料，不建立備份檔。較舊的程式仍能開啟資料庫，寫入時觸發器
  照常維護摘要（但整批重建不會暫停觸發器，較慢）。
- 讀取端在摘要可用（已安裝、`stats_valid='1'`、不在暫停中）時讀摘要，否則退回原本的
  全庫重算查詢；唯讀開啟的舊資料庫與寫入交易中暫停的狀態都走退回路徑。`_cached`、
  程序內快取與 `stat_cache` 的分層不變。快取世代是 `data_gen` 加上 `meta.ext_change_seq`。`ext_change_seq` 由
  `change_tracker` 在 `entry`、`translation`、`effective`、`history`、`src_change` 上安裝的觸發器遞增，
  只在寫入不在 `_tx()` 內（`meta` 沒有 `tx_active`）時才遞增；`_tx()` 內的寫入由 `data_gen` 負責。
  所以外部 SQL 的任何資料變更（包含總數不變的版本搬移、`src_change` 新增）都會讓持久化統計
  失效，不需逐項比對內容。快取入列前以 `ReadCache.exceeds_capacity()` 依位元組預算估算整個
  結果（含巢狀 list／tuple／dict 與長文字），超過容量的結果不序列化也不快取。
  衍生欄位（`qflags`、`effective.diff` 等）隨來源資料一起變，不另外追蹤。
  程式本身不會把生效列移到別的條目；外部 SQL 這樣改（`effective.entry_id`）時摘要無法逐項
  調整，`stat_effective_move` 觸發器把 `stats_valid` 設為 0，讀取改用原本的查詢，下次可寫開啟時重建。
  衍生資料的結構版本（`stats_schema_version` 等）在觸發器定義增減時遞增，舊安裝在唯讀開啟下因版本不符
  退回原本的查詢；唯讀路徑不逐一驗證觸發器是否被人為刪除，這類破壞要靠可寫開啟時的安裝檢查與 `verify_*()` 發現。
- `rebuild_stat_summary()` 在寫入交易中重建摘要並遞增資料世代；`verify_stat_summary()`
  以 `EXCEPT` 比對摘要與全庫重算，只供測試與維護使用（70 萬條目約 1.1 s）。

選擇逐列觸發器而非在各寫入流程呼叫更新函式，是因為寫入路徑多（掃描、手動儲存、審核、
批次取代、清理與還原、AI 修復、寫回、跨版本沿用、各種還原），觸發器讓任何寫入
（包括直接 SQL 與較舊的程式）都不會漏算。

## 正確性保護

`tests/test_translation_db_stat_summary.py` 在每條寫入路徑後比對摘要與全庫重算，
並要求三個讀取端與重算結果相同；另測試舊資料庫唯讀退回、無效標記退回與重建、
暫停後重算與交易回復、既有資料庫安裝摘要不改動其他資料、觸發器定義過期時重新安裝。
`stat_summary.py` 的每個觸發器若失效，至少有一項測試失敗。
