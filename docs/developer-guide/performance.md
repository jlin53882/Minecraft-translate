# Performance measurement contract

This document defines a reproducible measurement contract, not an optimization
target. Recorded numbers are not kept in the repository: they depend on the
machine and drift quickly, so attach them to the PR or issue that needs them. The
same command and fixture must be used before and after a change; numbers from
different platforms or browser versions are not directly comparable.

## Metrics

### Mod DB entry-list ordering

`translation_tool/translation_db/schema.py` installs `idx_entry_list_order` on
`entry (mc_version, mod_id, kind, key, id)`. The leading version column constrains
the selected Minecraft version; the remaining columns follow the default entry
list ordering, including its deterministic ID tie-breaker.

Writable database initialization uses `CREATE INDEX IF NOT EXISTS`, including
for databases already at the current schema version. An existing index is not
rebuilt on every open. Read-only repository connections do not install indexes;
a database must be initialized through a writable connection to obtain it.
This index does not require a schema-version change or rewrite entry,
translation, review, or history data.

Compare the complete public entry-list operation, including its total count,
on independent SQLite-backup copies with and without this index. Check first,
middle, and partial final pages, IDs/order/totals, multi-version filters, query
plans, index-installation cost, database size, and ingest/update cost. Do not
report an OFFSET selecting the last 50 rows as a partial final UI page. Retain
raw UI timings separately from query timings, including slower or unstable
first-load observations. No wall-clock threshold is asserted in unit tests;
index installation, query-plan shape, and result parity are tested instead.

### Mod DB: no temporary sorts

List, paging and candidate queries in `translation_tool/translation_db/` must not
produce `USE TEMP B-TREE FOR ORDER BY` in `EXPLAIN QUERY PLAN`. A temporary sort
reads the whole candidate set before returning the first row, so its cost grows
with the table instead of with the page size. Obtain the order from an index
whose column order matches the `ORDER BY`, from a two-phase query (select the page
IDs first, then read the wide columns), or from a trigger-maintained derived
column or table.

`tests/test_moddb_no_temp_sort.py` runs the public read APIs over every state,
sort, quality status and common filter, plus the module/source scope options
(`bulk_review_scope_options`, `smart_review_scope_options`), `cleanup_candidates` and
`cleanup_batches`, captures each SELECT through
`sqlite3.Connection.set_trace_callback`, and fails when a plan contains the temporary
sort. An exception is acceptable only when the sorted set has a hard bound that
does not depend on the table size; record the bound in `ALLOWED_PLAN_MARKERS` or
`ALLOWED` in that test. Current exceptions:

- Sorting the page's own IDs in the second phase of a two-phase query (bounded by the page size).
- `entry_newest` / `entry_oldest` sorts: `entry.created_at` has no index, and
  adding one slows every import for a rarely used sort.
- Untranslated candidate lists, reusable candidates and same-as-source AI entries: the sorted
  set is the number of missing or matching rows in one version.
- `kinds()`: sorts the distinct entry kinds only.
- The mismatch list: sorts only rows that pass the special-character prefilter.
- `cleanup_batches()`: sorts the cleanup batches created by manual cleanup operations only.
- Not measured: a 700k-row database. The plan checks run on a small fixture; no large database was available to compare timings.

### Mod DB 已審核 lang 術語候選

`TranslationDB.reviewed_lang_glossary` 先依模組、Minecraft 版本、`lang` 種類、
已審核狀態與有效譯文載入候選快照，再由 Python 的 `casefold()` 與既有詞界／
子字串匹配器依目前批次挑選術語。SQLite 的 `lower()` 不得作為 Unicode 候選的
排除條件，因為它不能完整涵蓋 Python 的大小寫折疊結果。

候選快照透過 `TranslationDB._cached` 儲存，快取鍵包含模組與版本，快取世代包含
`data_gen` 及外部寫入序號。相同資料世代的後續翻譯任務會重用候選集合，不會再次
掃描整個模組；資料庫寫入或外部變更使世代改變時，舊快照自動失效。來源相關性、
多譯文衝突排除與最終數量上限仍在 Python 端執行，不得提前在候選 SQL 階段截斷。

### 共用 Rich Text Shield 快取契約

翻譯中的 `$C...$`、`$P...$` 與 `$E...$` 只是單次執行的暫時佔位符，不能
直接成為跨執行快取的正式內容。共用翻譯迴圈在通過品質閘門後，將保護片段
還原成原始 Rich Text，再寫入快取；快取分流命中時會先依目前原文的 Shield
資訊驗證並還原，再把正式文字交給 FTB Quests、KubeJS、Guide／Patchouli
與其他輸出端，避免任何輸出端直接取得暫時 placeholder。

Shield 預期的 placeholder 映射只能來自 `ShieldPiece`；`clean` 文字中原本就存在的
`$P99$`、`$C17$` 等普通文字不得被正規表示式誤認為程式產生的保護片段。
Shield 生成器會避開原文已占用的 `$C...$`、`$P...$`、`$E...$` 名稱，避免程式產生
的暫時 ID 與原文普通文字碰撞。

品質閘門同時支援兩種既有資料：新快取的已還原文字會比對原始保護片段，舊
快取若仍含全域計數器產生的佔位符，只能在快取讀取路徑依保護類別與原文位置
做受限制的相容對齊；每個類別超過一個片段時沒有完整映射，必須視為 cache
miss。當次 AI 回應與品質重試一律使用目前 Shield 的精確 placeholder 身分與
數量，但允許翻譯因自然語序改變 placeholder 出現順序。缺少、重複、新增或
未知 placeholder 都會使 AI 重試與快取命中檢查失敗；因此 FTB Quests、KubeJS、
Guide／Patchouli 與一般共用迴圈必須使用同一套檢查。

### Mod DB trigger-maintained derived data

`translation_tool/translation_db/stat_summary.py`,
`translation_tool/translation_db/entry_activity.py` and
`translation_tool/translation_db/effective_columns.py` keep derived tables and
columns exact through SQLite triggers. Each module provides `verify()` (compare
with a full recompute using the original query definition) and `rebuild()`;
readers use the derived data only when its `meta` flags mark it valid and not
frozen, and otherwise fall back to the original SQL. Bulk rewrites of
`effective` run inside the modules' `frozen()` blocks and recompute once.

`translation_tool/translation_db/quality_flags.py` keeps `entry.qflags` (one bit
per quality status) for quality-filtered lists. Its triggers only mark affected
entries pending (`qflags IS NULL`); `TranslationDB._tx` calls `settle()` before
commit to recompute them with `translation_tool/translation_db/quality.py`, so
the flags never duplicate the quality rules in SQL. Guide structure rules depend
on `entry.kind`, so the triggers also watch `kind`; a rule change bumps
`quality_flags.SCHEMA_VERSION`, which recomputes every entry on the next open. Readers check for pending
entries and use the row-by-row scan while any exist. Its `verify()` compares
every entry with a recompute that calls `matches_quality` once per status.

Measure list operations through `TranslationDB.list_entries` on independent
copies of the same database, in a fresh process with the read-only connection
and an empty `stat_cache`, comparing against the commit before the change.
Report first, middle and last pages for each affected state and sort, and
write cost for single and repeated manual saves, ingest, and a full effective
rebuild with and without a priority change. Tests assert result parity with the
original queries, the absence of `USE TEMP B-TREE FOR ORDER BY` in the paged
queries, and `verify()` after every write path; no wall-clock threshold is
asserted.

### Other metrics

- AppShell mount time and browser-ready time from the real Flet smoke report
- First lazy build and same-session warm revisit for every registered view
- Dashboard cache-overview assembly/active-shard read, replace-rule file read,
  and Python-side activity/control refresh with a fixed task list
- Explicit-list JAR scan throughput for deterministic 10- and 100-JAR fixtures,
  including nested JAR paths
- LM batch selection cost with token budgeting enabled and disabled
- Python-side construction cost for 1k, 5k, and 10k-row Flet ListViews

The baseline uses no real Gemini API and performs no external network requests.
Synthetic JARs, translation items, Dashboard cache shards and replace rules are
generated in temporary directories. Dashboard task rendering is measured as
Python-side control refresh; it is not browser frame or scroll performance.

### Smart Review apply-record reconciliation

`schema.py` installs `idx_history_smart_review_apply` on `history (note)` with
`WHERE action='smart_review_apply'`. `_reconcile_json_applied_candidates` reads a
run's apply records with a `note >= prefix AND note < upper` range (not `LIKE`)
so the plan is a `SEARCH` on that partial index, and orders the rows by `id` in
Python to avoid a temporary sort. The index is created by the same writable
`CREATE INDEX IF NOT EXISTS` initialization as above and needs no schema-version
change. `tests/test_smart_review_manual_edit.py` asserts the plan has no `SCAN`
or `TEMP B-TREE`.

## Commands

Generate the real-Flet timing source:

```bash
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-source
```

Generate JSON and Markdown performance reports:

```bash
# Run the same real-Flet views matrix three times. Pass all three reports below.
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-run-1
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-run-2
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-run-3

uv run --isolated python tools/performance_baseline.py \
  --ui-report .artifacts/ui-smoke/performance-run-1/report.json \
  --ui-report .artifacts/ui-smoke/performance-run-2/report.json \
  --ui-report .artifacts/ui-smoke/performance-run-3/report.json \
  --output-dir .artifacts/performance/current \
  --repeats 7 \
  --runs 3
```

The number of `--ui-report` arguments must be either zero, one, or exactly the
requested `--runs` value. Supplying one report for multiple runs is supported
for engine-only diagnostics, but the output records `ui_report_reused=true` and
must not be interpreted as independent UI measurements. For a complete baseline,
use one freshly generated real-Flet report per run as shown above.

Outputs (under the Git-ignored `.artifacts/`):

- `.artifacts/performance/current/performance.json` — machine-readable values
- `.artifacts/performance/current/performance.md` — human-readable summary
- `performance.json` preserves each complete run under `runs[]`; every direct
  benchmark keeps its `samples_ms` list, plus median, min, max, population
  standard deviation, repeat count, environment and source commit.
- The top-level `summary` preserves run medians, run-to-run range and standard
  deviation for UI startup/cold/warm timings, JAR, batching, lists and Dashboard.
- `measurement` records the requested run count, repeat count, UI report paths,
  whether one UI report was reused, and the exact command. A single UI report
  reused across multiple engine runs is explicitly marked and is not equivalent
  to three complete real-Flet runs.
- The linked UI smoke report preserves each first-build/revisit timing as a case;
  the performance report references that report and summarizes those cases.

Run with `PYTHONPATH`, `PYTHONHOME`, `VIRTUAL_ENV`, and
`UV_PROJECT_ENVIRONMENT` unset when the shell inherits another Python runtime.

## Reproducibility

Record and compare all of the following:

- OS and hardware
- Python, Flet, Playwright, and browser versions
- Git commit and whether the worktree contains uncommitted changes
- exact command, repeats, theme, viewport, and fixture sizes
- cold first construction or warm same-session revisit status

Here, **cold** means a view's first lazy construction in a fresh Flet session;
**warm** means navigating back to the already-built view in that same session.
This does not flush the operating-system disk cache and must not be reported as a
cold-disk measurement.

Use the median as the primary value and retain raw samples, min/max and standard
deviation to expose both within-run and run-to-run variance. The report is
traceable because each raw sample remains under its numbered `runs[]` entry,
alongside the source commit, environment, input report path and command.

## Regression policy

1. Run at least three complete baselines on the same machine, including three
   real-Flet UI reports, before proposing a threshold.
2. Compare medians and observed ranges. Investigate changes that exceed normal
   variance; do not immediately classify them as product regressions.
3. Keep this report non-blocking initially. Add a CI warning or hard gate only
   after the fixture and machine class are stable.
4. Do not mix optimization into a measurement-only change. Any optimization
   needs its own behavior tests and before/after evidence.
5. Browser/render timing, Python view construction, JAR I/O, and token
   estimation are separate metrics; do not combine them into one score.

The protocol is measurement-first: a baseline describes observed behavior and
variance; it does not by itself establish a performance budget. Set a budget or
CI threshold only after the fixture, platform, renderer, and acceptable variance
are explicitly agreed upon.

## Large-list and event-loop follow-up

List construction is measured now because Cache, Rules, and QC can display
large datasets. Real scroll-frame timing and the longest blocking synchronous
handler require renderer instrumentation beyond this first baseline. Add those
measurements only with a deterministic interaction and an explicit owner; do not
infer event-loop blocking from Python construction time alone.
