# 智慧校對歷史 JSON 儲存

智慧校對的候選（原文快照、context、模型回應）以 JSON 保存，不寫進 SQLite。SQLite `history` 的
`smart_review_apply` audit 是寫入事實；`projection_state=pending` 的 marker 會在 JSON 對帳前受到保護，
JSON 是候選狀態投影，兩者的協議見
[`smart-review-status.md`](smart-review-status.md)。本文件說明 JSON 目錄的檔案格式、復原、
維護工具與保留策略。

SQLite `history` 一般每個條目只保留最新五筆；但 `projection_state=pending` 的
`smart_review_apply` 不計入刪除候選，直到 JSON 回寫或讀取對帳成功。從 schema v8
升級時，舊資料沒有投影狀態可供判斷，migration 會先把既有 `smart_review_apply`
保守標為 `pending` 再執行保留清理，避免遺失 SQLite 已提交、JSON 尚未同步時所需的
故障復原證據。下一次對帳成功後才會轉為 `projected`，並在後續交易中依一般政策清理。

程式分工（`translation_tool/translation_db/`）：

| 模組 | 負責 |
|---|---|
| `smart_review_history.py` | 對外門面 `SmartReviewHistoryStore`：批次生命週期與候選寫入交易 |
| `smart_review_history_lock.py` | 程序內鎖與跨程序檔案鎖 `.smart_review.lock` |
| `smart_review_history_codec.py` | 候選／批次 JSON 結構與摘要計算（純函式）、`SCHEMA_VERSION` |
| `smart_review_history_manifest.py` | manifest、分片與 sidecar 的檔案存取、原子寫入、發布 |
| `smart_review_history_sidecar.py` | status sidecar 的格式（純函式） |
| `smart_review_history_recovery.py` | 擁有者程序已不存在的 `running` 批次復原 |
| `smart_review_history_query.py` | 分頁、計數、最近批次標籤（唯讀） |
| `smart_review_history_migrations.py` | schema migration registry、一致性檢查、修復 |
| `tools/smart_review_history_tool.py` | 維護 CLI：check／repair／backup／restore／prune |

## 目錄配置

歷史目錄是 `<資料庫名稱>.smart_review/`（`history_directory_for_db()`），與資料庫檔案同層：

```
review.smart_review/
  <run_id>.json                    批次 manifest；小批次則是含全部候選的單檔 JSON
  <run_id>.chunks/                 分片批次的候選目錄
    000000.json                    候選分片（依 ordinal 排序）
    000000.status                  該分片的 status sidecar（內容是 JSON）
  quarantine/<時間戳>/...          repair 搬走的孤立檔案
  .smart_review.lock               跨程序檔案鎖
```

`run_id` 只允許 `[A-Za-z0-9_-]{1,128}`，路徑一律由 `ChunkManifestRepository` 產生以拒絕路徑穿越。

### 單檔與分片

候選數不超過 `smart_review.storage_chunk_size` 時使用單檔 JSON，候選放在 `candidates` 清單。
超過時使用 `storage_format = "chunked_json_v1"` 的 manifest，候選依 ordinal 切到多個分片，
更新時只原子替換受影響的分片與 manifest。兩種格式的 `schema_version` 目前都是 1，舊版沒有
`schema_version` 欄位的檔案視為 v1。

### Manifest 欄位（分片批次）

- 批次資料：`id`、`provider`、`model`、`started_at`、`finished_at`、`status`、`owner_pid`、`selected_count` 等。
- `chunks`：每個分片的 `index`、`ordinal_start`、`ordinal_end`、`count`、`status_counts`、`error_counts`、`chunk_revision`。
- `chunk_revisions`／`chunk_signatures`：manifest 最後一次 checkpoint 時各分片的 revision 與檔案
  `[mtime_ns, size]`，用來判斷分片是否被其他程序或外部改過。
- `status_counts`／`error_counts`／`processed_count` 等：全批次摘要，由分片內容計算。
- `projection`：`revision`（每次候選投影變更遞增）與 `state`。

### 寫入與復原

所有 JSON 寫入都是暫存檔、`flush`、`fsync`、`os.replace`，再 fsync 目錄。

- **建立大批次**：分片與 sidecar 先完整寫進 `.<run_id>.chunks.<uuid>.tmp` 暫存目錄，改名為
  `<run_id>.chunks` 後才寫入 manifest；manifest 是最後發布的檔案。發布前中止只會留下沒有
  manifest 的目錄，下一個程序的 recovery 會清掉。
- **更新候選**：先替換分片，再寫 sidecar，最後寫 manifest。分片替換後、manifest 寫入前中止時，
  manifest 的 `chunk_signatures` 與實際分片不符；下一次讀取或寫入偵測到不符，就從分片重建摘要。
- **失聯批次**：`HistoryRecovery` 把 `owner_pid` 已不存在的 `running` 批次收斂為 `interrupted`，
  沒有 `owner_pid` 的舊檔不改寫。

## Status sidecar

每個分片旁有 `NNNNNN.status`，只保存查詢狀態需要的欄位，不含原文與 context：

```json
{
  "schema_version": 1,
  "sidecar_version": 1,
  "run_id": "<run_id>",
  "chunk_index": 3,
  "chunk_revision": "<manifest 登記的分片 revision>",
  "projection_revision": 12,
  "updated_at": "2025-01-01 00:00:00",
  "candidate_count": 200,
  "rows": [[601, 123456, "ready", null], [602, 123457, "invalid", "bad_reason"]]
}
```

`rows` 的每列是 `[ordinal, entry_id, status, validation_error 摘要]`，錯誤文字最多保留 500 字元。
副檔名刻意不是 `.json`，避免被當成分片或批次 manifest。

- **新鮮度**：`chunk_revision` 等於 manifest `chunk_revisions` 中該分片的值，且分片檔案的
  mtime／大小等於 manifest 記錄時，sidecar 才被採用；`candidate_count` 必須等於 manifest 的分片筆數。
  `projection_revision` 是寫入當下 manifest 的 `projection.revision`，供診斷，不參與新鮮度判斷。
- **寫入時機**：建立批次時隨分片一起發布；之後每次分片 checkpoint 在 manifest 發布前寫入。
  sidecar 寫入失敗只記錄警告，不影響候選寫入。
- **讀取**：`latest_run_marks()` 對分片批次只讀 sidecar，遺失、過期或損壞時才讀該分片重建。
  讀取路徑不寫檔；重建結果只在記憶體，下一次寫入該分片或執行 `repair` 時補回檔案。
  單檔批次沒有 sidecar，直接解析 JSON。
- 分頁與狀態計數使用 manifest 的 `status_counts` 與各分片的 `status_counts`，不需要 sidecar。

## Schema 版本與 migration

`SCHEMA_VERSION`（`smart_review_history_codec.py`）是目前寫入的版本，為 1。
`smart_review_history_migrations.py` 的 `MIGRATIONS` 以「來源版本 → 升級函式」登錄，
登錄 N 代表可由 N 升到 N + 1；目前登錄 `1: upgrade_v1_to_v2`，它補上缺少的 `projection` 並把版本記為 2。

- `migrate_payload(payload, target=SCHEMA_VERSION)` 回傳新的 payload 且不改輸入；預設目標等於寫入版本，
  所以現行資料不會變動。升級函式只補缺少的欄位，已達目標版本的資料重跑結果相同。
- 沒有任何寫入流程會自動升版。要採用新版本時：新增升級函式、提高 `SCHEMA_VERSION`、補測試。
- 比 `latest_known_schema_version()` 新的檔案，`check` 回報 `schema_unsupported`（需要較新的程式）；
  比寫入版本舊的回報 `schema_outdated`（警告）。
- sidecar 有自己的 `sidecar_version`；版本不認得時視為過期並從分片重建。

## 一致性檢查與修復

`check_history_directory()` 唯讀掃描並回傳 `HistoryIssue` 清單（`code`、`severity`、`repairable`、
`run_id`、`path`、`chunk_index`、`detail`）。檢查期間持有歷史目錄的寫入鎖。

| code | 意義 | severity | repair |
|---|---|---|---|
| `manifest_corrupt` | manifest 無法解析或不是 object | error | 否 |
| `manifest_invalid` | 分片數量、ordinal 範圍或 `selected_count` 不連續 | error | 否 |
| `run_id_mismatch` | 檔名與內容 `id` 不同 | error | 否 |
| `schema_unsupported` | schema 版本比程式已知的新或無效 | error | 否 |
| `schema_outdated` | schema 版本低於寫入版本 | warning | 否 |
| `chunk_missing` | manifest 登錄的分片不存在 | error | 否 |
| `chunk_corrupt` | 分片損壞，或 run_id／索引／ordinal 與 manifest 不符 | error | 否 |
| `chunk_count_mismatch` | 分片實際筆數與 manifest 不同 | error | 否 |
| `orphan_chunk` | 分片目錄內 manifest 沒有登錄的檔案 | warning | 隔離 |
| `orphan_chunk_directory` | 沒有 manifest 的 `.chunks` 目錄 | warning | 隔離 |
| `stale_temp` | 寫入或發布中斷留下的暫存檔、暫存目錄 | warning | 隔離 |
| `summary_mismatch` | manifest 或分片的 `status_counts`／`error_counts`／`selected_count` 與實際內容不符 | error | 重建 |
| `checkpoint_stale` | `chunk_revisions`／`chunk_signatures` 與分片檔案不符 | warning | 重建 |
| `sidecar_missing`／`sidecar_corrupt`／`sidecar_stale`／`sidecar_mismatch` | sidecar 不存在、損壞、revision 過期、內容與分片不同 | warning | 重建 |

`repair_history_directory(directory, dry_run=False)`：

- 重建 manifest 摘要、checkpoint 與全部 sidecar，資料來源是分片實際內容；候選本身不會被修改。
- 孤立檔案與暫存檔搬到 `quarantine/<UTC 時間戳>/`（保留相對路徑），不刪除。確認不需要後再手動刪除該目錄。
- 缺少或損壞的分片、損壞的 manifest 無法由工具重建，列在 `unresolved`，請從備份還原。
  該批次的摘要不重建，避免把缺少的分片算成待處理。
- `dry_run=True` 只回傳動作清單，不改任何檔案。重複執行不再有動作。

## 維護 CLI

```
python tools/smart_review_history_tool.py (--db <資料庫> | --dir <歷史目錄>) [--json] <子命令>
```

| 子命令 | 行為 | 結束碼 |
|---|---|---|
| `check` | 列出問題 | 0 無問題、1 有問題 |
| `repair [--dry-run]` | 重建摘要與 sidecar、隔離孤立檔案 | 0 全部解決、1 仍有未解決問題 |
| `backup --output <zip>` | 持有寫入鎖，整個目錄（含 `quarantine/`，排除鎖檔與暫存檔）打包；輸出檔不可已存在或位於歷史目錄內 | 0 |
| `restore --input <zip> [--force]` | 解壓到同層暫存目錄，成功後把現有目錄改名為 `<目錄>.pre-restore-<時間戳>`，再換上還原內容並執行 `repair`；現有目錄非空時需 `--force`；拒絕絕對路徑或含 `..` 的成員 | 0 |
| `prune [--retention-days N] [--keep-latest M] [--apply]` | 預設只列出；`--apply` 才刪除 | 0 |

使用錯誤（缺檔、備份已存在、路徑不安全、參數為負）結束碼為 2。`restore` 與 `prune --apply`
會改動歷史目錄，執行前先關閉應用程式。

## Retention 與 archive 策略

- **保留規則**：`prune` 刪除同時符合「`started_at` 早於 `--retention-days`（預設 90 天）」與
  「不在最近 `--keep-latest` 批（預設 10 批）之內」的批次，包含其分片目錄。
  `running` 狀態、`started_at` 無法辨識或 manifest 損壞的批次一律保留，交給 `check`／`repair`。
- **archive**：清理前先 `backup`；zip 內是完整目錄，可整個還原，也可手動取出單一批次的 `.json` 與 `.chunks/` 放回。
- **被清理的批次**：無法再檢視候選或補套用，SQLite 的 `smart_review_apply` audit 與已套用的翻譯不受影響。
- **建議流程**：`check` → `backup` → `prune`（先 dry-run 檢視清單）→ `prune --apply` → `check`。
- **還原後**：`restore` 會自動重建 checkpoint 與 sidecar，因為 zip 不保留檔案 mtime；候選內容不變。
- **quarantine**：`repair` 不會自動清空；確認不需要後手動刪除舊的時間戳目錄。
