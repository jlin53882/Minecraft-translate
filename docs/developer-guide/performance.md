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
  set is the number of missing or matching rows in one version (for same-as-source, the rows carrying the flag bit).
- `kinds()`: sorts the distinct entry kinds only.
- The mismatch list: sorts only rows that pass the special-character prefilter.
- `cleanup_batches()`: sorts the cleanup batches created by manual cleanup operations only.
- Large databases: the plan checks run on a small fixture; timings on a database of 700k or more
  entries are measured with the tools described in "Mod DB large-database measurement" below.

### Mod DB large-database measurement

`tools/benchmark_moddb_700k.py` measures the read operations of `TranslationDB` and of the
Entries page's snapshot (versions, mods, kinds, source codes, filter options, one page, the first
entry's detail, Smart Review marks) on a database of 700k or more entries; `tools/benchmark_moddb_700k_write.py`
measures the write paths on a copy. Use a database built from real mod JARs of at least three Minecraft versions and
make independent copies with SQLite backup or `cp`; never benchmark the shared file with the write tool.

Every case runs in a fresh process and reports three numbers. **Cold** is the first call. **Hot**
repeats the call in the same process, so the process-level read cache is warm. **Miss** repeats it with the
process caches emptied before each call, which is what a new filter combination or the first refresh after a
write costs (the OS file cache stays warm). Each iteration opens a new read-only connection inside a read
transaction, like the background workers do, so SQLite's per-connection page cache is never shared. The
interactive target is the hot value of the combined Entries snapshot; a miss may exceed it, but must scale
with a scan of one version at most and never with the number of operations already performed on the batch.

When a number looks wrong, check in this order: `EXPLAIN QUERY PLAN` of the exact statement
(`set_trace_callback` gives the statement with its parameters expanded), whether the planner starts from a
large table instead of the page's own rows (use `CROSS JOIN` to fix the order), whether a cached value is
invalidated by an unrelated write, and whether Python does per-row work that SQL can aggregate.

### Mod DB module × source facets

`translation_tool/translation_db/option_facets.py` computes, once per filter (with the module and source
selection removed), the number of matching entries for every module and every effective source. The same
facet answers the module and source dropdown options, the total count for any module or source selection, and
the scope dropdowns of bulk review and Smart Review. Facets are cached per data generation through
`TranslationDB._cached`, so paging and changing the module or source never rescans.

Filters that only involve the version, the entry kind, the review status or "no translation" read the facet from
`stat_src` (version, module, kind, source, review), which `stat_summary.py` keeps exact with triggers; all other
filters use one streaming `GROUP BY e.mod_id` scan along `idx_entry_list_order`. `verify()` of `stat_summary` compares
the table with a full recompute, and the facet tests compare every facet result with the row queries after each write
path. A list total is taken from a cached facet when it exists and is otherwise counted separately, so a module-only
service query never scans the whole version.

Text search (`LIKE '%text%'` on key, source text and effective translation) cannot use an index and is a CPU-bound
row-by-row comparison (about 190 ms for 280,000 entries on one connection). It is not backed by derived data: a
trigram posting table was estimated from a sample at about 64 distinct trigrams per entry, i.e. roughly 55 million rows
and 700 MB for 850,000 entries, plus 64 extra index rows per written entry, which does not pay off. Instead
`translation_tool/translation_db/parallel_scan.py` splits `entry.id` into contiguous ranges and scans them on separate
read-only connections at once (SQLite releases the GIL while stepping); the ranged facet SQL groups by module per range
and `option_facets.merge_rows` adds the ranges together. These connections must not use `temp_store = MEMORY` (several
connections doing `GROUP BY` in memory contend on SQLite's global allocator lock and are slower than one connection when
many rows match) and are skipped inside a write transaction (other connections cannot see its changes). A search
without a module or source selection takes its total from this facet (`list_entries`), so the total, the page's
module/source options and later paging share one scan; with a module selected the index already narrows the count.
At most `cpu_count - 1` connections (never more than 4) are used so the UI keeps a core. Cancellation reuses `translation_tool/utils/cancellation.py`, so a superseded request stops all ranges.

Pages: when the total is known and zero, or the offset is past it, the page query is skipped (it would otherwise walk
the whole version looking for rows that do not exist); for the default sort the second half of the list is read from the
other end through the reversed index and flipped.

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

### Mod DB derived "same as source" flag and other read paths

`entry.qflags` carries one extra bit, `quality_flags.SAME_AS_SOURCE` (effective text equals a non-empty source), with
its own partial index `idx_qflags_same_as_source`. The `same` state, `count_same_as_source_ai` and
`same_as_source_ai_entries` read it (the latter two name the index with `INDEXED BY`, because the planner does not
pick a partial index on its own there) and fall back to the original comparison while flags are pending. Changing
the meaning of a bit requires bumping `quality_flags.SCHEMA_VERSION`; the next writable open recomputes every entry in
chunks.

`cleanup_candidates` evaluates the cleanup rules in Python for every entry of a version, so the flagged entries are
cached per data generation (`TranslationDB._cached`); inside a write transaction it is always recomputed.
`time_data_range` for the manual-activity basis starts from `history` (small) and finds entries by primary key; the
reverse order probes the history index once per entry of the version.

### Mod DB web interaction measurement

`tools/perf_moddb_web.py` drives the real Flet web app through Playwright and records, for every operation, one cold
and `--hot` warm runs. Every sample carries four server-side numbers; read them together:

- `server_ms`: from the received browser event to the last patch of the main content (list, editor, history, dialog).
  The shell's 250 ms coalesced refresh and the key poll are never counted.
- `settled_ms`: the same, but including patches that are deliberately sent after the main content: the filter
  dropdown options (`EntriesPanel.flush_filter_options`, which waits `FilterOptionsLoader`'s 0.25 s settle time) and the
  Smart Review estimate text (`_update_estimate_region`). The caller names are listed in `DEFERRED_CALLERS`.
- `wall_ms`: `server_ms` before subtracting a deliberate wait. Typing operations pass `debounce_ms` (the editor's 0.15 s
  stop-typing delay) and `after_last_key` (start at the last key event, because the gap between the characters of a
  multi-character input is the browser's key-event pacing). `first_patch_ms` is the immediate response to the key.
- `list_patch_ms`: until the last `ListView` patch.

Per-sample timelines (`patch` entries with the calling `app/` frames, DB calls with their thread, patch sizes) and
`--profile` (cProfile of the event-loop thread) show where a number comes from. Compare runs on the same copy of the
database, and expect other processes on the machine to move a single sample by tens of milliseconds; use medians.

What the numbers are made of (700k entries, one version = 284k):

- Most of a write's (save, revert, review, clear) interactive time is Flet: a patch costs time proportional to the
  number of controls it has to diff, not to what changed. The page therefore updates only what changed:
  `EntryTilePool.set_rows` reports the rows whose content changed and the panel updates those rows instead of the
  50-row `ListView` (more than `MAX_ROW_UPDATES` changed rows update the whole list, because one patch per row has a
  fixed cost); `FilterFlush` updates only the dropdowns whose options or value changed. Large dropdowns (1,400 module
  options) cost about 25 ms to diff when their options change.
- The write path requests the background reload before it shows the snack bar, and does not recompute the impact
  preview or redraw the editor that the reload redraws anyway.
- A quality or search filter that is new for the current data generation scans one version's flags in the worker
  (about 60-250 ms for 284k entries, depending on the status). The result is cached per data generation, so selecting
  it again is a cache hit. State filters (`none` / `all`) are answered by `stat_src` and never scan.
- The first batch-translation preview of a process used to stop the event loop for several hundred milliseconds:
  loading the `tiktoken` vocabulary holds the GIL. `app/startup_tasks.py` loads it in the startup worker
  (`warm_token_encoder`). The preview itself is worker-side work (one sorted read of the version's untranslated
  entries plus batch planning); it is reported as an interaction time but is not event-loop time.

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

### ModDB 匯入與清理基準

`tools/benchmark_moddb_import.py` 提供不接觸正式資料庫的可重現基準：合成
語言檔寫在暫存目錄，SQLite ingest、清理報告、preview、apply 與 revert
都使用暫存資料庫，程序結束後由暫存目錄一併移除。`--real-root` 僅逐一
唯讀掃描實際的 JAR，不會建立 `TranslationDB` 或寫入資料庫。

### Advancement component round-trip

Advancement 的 `display.title`／`display.description` 使用共用 literal
抽取器與專用 writer。效能成本是每個元件樹一次線性走訪；只走訪
`text`、`fallback`、`with[]` 與 `extra[]`，不會為每個候選重新查詢資料庫或掃描
整個 JSON。`translate`、樣式、事件與 selector／score／NBT 等結構不進入翻譯候選，
writer 也會再次驗證路徑，因此安全檢查不依賴呼叫端自律。

H3 的回歸測試涵蓋根字串、`text`、陣列、巢狀 literal、資料庫 identity／key、
cache 命中寫回、JSON 重新解析，以及 translate key／樣式／事件保留。實際模組
inventory 與 1,000／10,000／70,000／100,000／269,000 列的隔離基準仍分開記錄；
不能把單一 Advancement JSON 的 parser 時間誤報成整體 UI 或外部模型 SLA。

```bash
uv run python -m tools.benchmark_moddb_import --sizes 1000 10000 70000
uv run python -m tools.benchmark_moddb_import \
  --real-root "<mods 資料夾>" --version 1.20.1 --sizes 1000
```

輸出保留每次取樣、median、p95、輸入列數、SQLite 版本、掃描錯誤與資料庫
隔離聲明。合成基準用來比較程式碼前後的資料庫處理成本；實際 JAR 掃描
則另外記錄 archive 數、條目數與總耗時，兩者不可合併成單一 UI SLA。

### Smart Review apply-record reconciliation

`schema.py` installs `idx_history_smart_review_apply` on `history (note)` with
`WHERE action='smart_review_apply'`. `_reconcile_json_applied_candidates` reads a
run's apply records with a `note >= prefix AND note < upper` range (not `LIKE`)
so the plan is a `SEARCH` on that partial index, and orders the rows by `id` in
Python to avoid a temporary sort. The index is created by the same writable
`CREATE INDEX IF NOT EXISTS` initialization as above. Pending projection markers
are protected by the history retention query and become eligible for cleanup only
after JSON confirmation; this does not add a full candidate scan to the normal
retention path. `tests/test_smart_review_manual_edit.py` asserts the plan has no
`SCAN` or `TEMP B-TREE`, and the recovery regression covers five later history
writes for the same entry.

## Mod DB scan throughput

Scanning has two serial-ish stages: JAR parsing (pure Python) and ingest in the single writer.

- **Parsing** runs in a `spawn` process pool (`translation_db/scan_pool.py`) when a scan has at least
  `PROCESS_MIN_JARS` archives and more than one worker; threads are limited by the GIL and get slower with more
  workers, so the fallback (few JARs, or the pool cannot start) is a single reader thread. Process-pool rules: options
  cross the process boundary without the replace-rule objects (plain dicts, rebuilt in the worker initializer);
  cancellation is a shared `Event` set by a watcher thread; worker log records travel back with the result and are
  replayed in the caller; frozen builds must call `multiprocessing.freeze_support()` in the entry point (`main.py`).
  Measure the worker count with the parse-only loop (serial, threads 2/4, processes 2/3/4) on at least 300 JARs
  after the file cache is warm; the process count is bounded by `moddb_scan` in `translation_tool/utils/worker_count.py`.
- **Ingest** commits once per batch: `TranslationDB.ingest_many` writes up to `_IngestBuffer.MAX_JARS` archives
  or `MAX_ITEMS` items in one transaction (every commit costs a WAL write and checkpoint regardless of size) and
  returns per-archive statistics. A cancelled scan still writes the archives already read.
- **Trigger groups** are attributed by dropping them on a scratch copy (`stat_`, `effcol_`, `qflags_`, `chg_`,
  `activity_`) and timing the same batched ingest. `frozen()`-style suspension only pays when the recompute after it
  is cheaper than the triggers it saves. Recomputing by module (counters before/after plus the module's
  `stat_src` rows and `effective` columns) scans every version of the touched modules, which cost as much as the
  triggers on a database with several versions per module, so ingest and `write_back` keep the per-row triggers; the
  full-table `frozen()` is kept for whole-table rewrites such as `_rebuild_effective`. Re-measure before suspending
  triggers for another write path; a scope has to be proportional to the rows written, not to the modules touched.
- Compare `process CPU time` with wall time to see how many cores are used, and run every `verify_*` afterwards.

## Worker 數量

`translator.parallel_execution_workers` 是使用者設定的上限；不同工作的負載不同，所以各用途保有自己的預設值與上限，
共通的只有 `translation_tool/utils/worker_count.py` 的 `resolve_worker_count(purpose, config, cpu)` 解析規則：

- 設定值必須是正整數；`bool`、字串、浮點數、0、負數視為無效，退回該用途的預設值。
- 有效時取設定值，再套該用途的上限（若有）；結果至少 1。
- 用途表 `WORKER_POLICIES`：`count_prescan`（FTB／KubeJS 預掃描，預設 4）、`file_pipeline`（逐檔處理，CPU 一半，上限也是 CPU 一半）、
  `moddb_scan`（上限 CPU 核心數）、`jar_extract`（預設 CPU 一半，上限 32）、`jar_preview`、`jar_browser`、`icon_index`、`icon_cache`、`cache_load`、`lm_extract`。
- 呼叫端：所有讀取 `translator.parallel_execution_workers` 的程式都經過本函式並指定用途（`WORKER_POLICIES` 的鍵）；`tests/test_worker_count.py` 以掃描原始碼確保不會再出現各自讀設定的呼叫點。
- 未知用途會丟 `KeyError`，避免默默用錯上限。測試：`tests/test_worker_count.py`。

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
