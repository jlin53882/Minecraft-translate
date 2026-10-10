"""Benchmark Mod DB batch paths on disposable 10k/100k databases.

Run with ``uv run python -m tools.benchmark_moddb_batch``. Timings depend on
the machine and SQLite build; they are diagnostic evidence, not CI thresholds.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import time
import tracemalloc
from pathlib import Path
from statistics import median
from threading import Event

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    QualityFilter,
    ScanItem,
    TimeFilter,
    TranslationDB,
)
from translation_tool.translation_db.schema import SRC_JAR_TW
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope


def timed(label: str, operation):
    started = time.perf_counter()
    result = operation()
    return {
        "name": label,
        "seconds": round(time.perf_counter() - started, 4),
        "result": result,
    }


def profiled(label: str, operation, db: TranslationDB, *, repeats: int = 5):
    """Collect repeatable wall/CPU, Python peak allocation, and SQL counts."""
    wall_samples = []
    cpu_samples = []
    peak_bytes = []
    query_counts = []
    result = None
    for _ in range(repeats):
        query_count = 0

        def count_query(_sql: str) -> None:
            nonlocal query_count
            query_count += 1

        db._conn.set_trace_callback(count_query)
        tracemalloc.start()
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        try:
            result = operation()
        finally:
            cpu_samples.append(time.process_time() - cpu_started)
            wall_samples.append(time.perf_counter() - wall_started)
            _current, peak = tracemalloc.get_traced_memory()
            peak_bytes.append(peak)
            tracemalloc.stop()
            db._conn.set_trace_callback(None)
            query_counts.append(query_count)

    def percentile(values, ratio):
        ordered = sorted(values)
        index = min(len(ordered) - 1, int((len(ordered) - 1) * ratio + 0.999999))
        return round(ordered[index], 4)

    return {
        "name": label,
        "repeats": repeats,
        "wall_seconds": wall_samples,
        "wall_p50_seconds": round(median(wall_samples), 4),
        "wall_p95_seconds": percentile(wall_samples, 0.95),
        "cpu_seconds": cpu_samples,
        "cpu_p50_seconds": round(median(cpu_samples), 4),
        "cpu_p95_seconds": percentile(cpu_samples, 0.95),
        "peak_tracemalloc_bytes_max": max(peak_bytes),
        "sql_statements_per_sample": query_counts,
        "result": result,
    }


def repeated_timed(label: str, operation, *, repeats: int = 5):
    """Measure ordinary elapsed/CPU time without profiling hooks or tracemalloc."""
    wall_samples = []
    cpu_samples = []
    result = None
    for _ in range(repeats):
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        result = operation()
        cpu_samples.append(time.process_time() - cpu_started)
        wall_samples.append(time.perf_counter() - wall_started)

    def percentile(values, ratio):
        ordered = sorted(values)
        index = min(len(ordered) - 1, int((len(ordered) - 1) * ratio + 0.999999))
        return round(ordered[index], 4)

    return {
        "name": label,
        "repeats": repeats,
        "wall_seconds": [round(value, 4) for value in wall_samples],
        "wall_p50_seconds": round(median(wall_samples), 4),
        "wall_p95_seconds": percentile(wall_samples, 0.95),
        "cpu_seconds": [round(value, 4) for value in cpu_samples],
        "cpu_p50_seconds": round(median(cpu_samples), 4),
        "cpu_p95_seconds": percentile(cpu_samples, 0.95),
        "result": result,
    }


def _explain(db: TranslationDB, sql: str, params: list) -> list[list]:
    return [list(row) for row in db._q("EXPLAIN QUERY PLAN " + sql, params)]


def benchmark(count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"moddb-batch-bench-{count}-") as folder:
        db = TranslationDB(Path(folder) / "benchmark.db")
        try:
            items = [
                ScanItem(
                    KIND_LANG,
                    "benchmark",
                    f"item.bench.{index:06d}",
                    f"Source {index} %s",
                    f"譯文舊{index:06d}" if index % 2 else f"譯文舊{index:06d} %s",
                )
                for index in range(count)
            ]
            measurements = [
                timed("ingest_setup", lambda: db.ingest("1.21.1", items).new_entries)
            ]
            ordinary_criteria = EntryFilter(version="1.21.1", source=SRC_JAR_TW)
            criteria = EntryFilter(
                version="1.21.1",
                source=SRC_JAR_TW,
                time=TimeFilter(
                    kind="entry_created",
                    start_utc="2000-01-01 00:00:00",
                    end_utc="2100-01-01 00:00:00",
                    unknown_policy="exclude",
                ),
            )
            quality_criteria = EntryFilter(
                version="1.21.1",
                source=SRC_JAR_TW,
                time=criteria.time,
                quality=QualityFilter(status="mismatch"),
                sort_by="effective_updated_newest",
            )

            def query_sql(filter_: EntryFilter, *, page: bool) -> tuple[str, list]:
                where, params = db._filter_sql(filter_)
                sql = f"{db._entry_select_sql()} {db._query_source()} WHERE {where}"
                if not filter_.quality.active and page:
                    sql += f" ORDER BY {db._sort_sql(filter_.sort_by)} LIMIT ? OFFSET ?"
                    params.extend((50, 0))
                return sql, params

            def filtered_count(filter_: EntryFilter) -> int:
                where, params = db._filter_sql(filter_)
                return int(
                    db._cached_count(
                        f"SELECT COUNT(*) {db._query_source()} WHERE {where}", params
                    )
                )

            def list_result(filter_: EntryFilter) -> dict[str, int]:
                rows, total = db.list_entries(criteria=filter_, limit=50)
                return {"rows": len(rows), "total": total}

            measurements.extend(
                [
                    timed(
                        "ordinary_filtered_count",
                        lambda: filtered_count(ordinary_criteria),
                    ),
                    timed(
                        "ordinary_list_page",
                        lambda: list_result(ordinary_criteria),
                    ),
                    timed(
                        "time_filter_count_and_page",
                        lambda: list_result(criteria),
                    ),
                ]
            )

            def full_materialization_reference() -> dict[str, object]:
                all_rows = db._query_entry_rows(quality_criteria)
                page = all_rows[:50]
                return {
                    "rows": len(page),
                    "total": len(all_rows),
                    "ids": [row[0] for row in page],
                }

            def quality_list_result() -> dict[str, object]:
                rows, total = db.list_entries(criteria=quality_criteria, limit=50)
                return {
                    "rows": len(rows),
                    "total": total,
                    "ids": [row.id for row in rows],
                }

            quality_reference = profiled(
                "quality_filter_full_materialization_reference",
                full_materialization_reference,
                db,
            )
            quality_streamed = profiled(
                "quality_filter_streamed_page",
                quality_list_result,
                db,
            )
            quality_reference_wall = repeated_timed(
                "quality_filter_full_materialization_wall_cpu",
                full_materialization_reference,
            )
            quality_streamed_wall = repeated_timed(
                "quality_filter_streamed_page_wall_cpu",
                quality_list_result,
            )
            if quality_reference["result"] != quality_streamed["result"]:
                raise AssertionError("品質篩選分頁結果與完整候選參考結果不同")
            if quality_reference_wall["result"] != quality_streamed_wall["result"]:
                raise AssertionError("品質篩選分頁輸出與無 profiling 參考結果不同")
            measurements.extend(
                [
                    quality_reference,
                    quality_streamed,
                    quality_reference_wall,
                    quality_streamed_wall,
                ]
            )

            def repair_preview_summary(limit: int | None) -> dict[str, int]:
                total, candidates = db.mismatched_translation_entries(
                    "1.21.1", [], limit=limit
                )
                return {"total": total, "selected": len(candidates)}

            measurements.extend(
                [
                    timed(
                        "quality_repair_preview_limit_100",
                        lambda: repair_preview_summary(100),
                    ),
                    timed(
                        "quality_repair_preview_unlimited",
                        lambda: repair_preview_summary(None),
                    ),
                    timed(
                        "quality_repair_remaining_count",
                        lambda: db.mismatched_translation_entries(
                            "1.21.1", [], limit=1
                        )[0],
                    ),
                ]
            )

            list_sql, list_params = query_sql(criteria, page=True)
            quality_sql, quality_params = query_sql(quality_criteria, page=True)
            sibling = db._q(
                "SELECT kind,mod_id,key,en_us FROM entry WHERE mc_version=? LIMIT 1",
                ("1.21.1",),
            )[0]
            sibling_sql = (
                f"{db._entry_select_sql()} {db._query_source()} "
                "WHERE (e.kind,e.mod_id,e.key,e.en_us) IN ((?,?,?,?)) "
                "AND e.en_us<>'' ORDER BY e.kind,e.mod_id,e.key,e.en_us,e.mc_version"
            )
            sibling_params = list(sibling)

            def sibling_query() -> int:
                roots = db._query_entry_rows(
                    EntryFilter(version="1.21.1", include_ids=(1,))
                )
                return sum(
                    len(group) for group in db._batch_sibling_rows(roots).values()
                )

            measurements.append(
                timed(
                    "batch_sibling_query",
                    sibling_query,
                )
            )
            preview = timed(
                "batch_preview",
                lambda: db.preview_batch_replace(criteria, "舊", "新"),
            )
            plan = preview["result"]
            preview["result"] = {
                "roots": len(plan.root_ids),
                "updates": plan.update_count,
                "skipped": plan.skipped_count,
            }
            measurements.append(preview)

            cancelled = Event()

            def request_cancel(stage: str, _progress: float) -> None:
                if stage == "建立替換預覽":
                    cancelled.set()

            def cancel_preview() -> str:
                try:
                    with cancel_scope(cancelled.is_set):
                        db.preview_batch_replace(
                            criteria,
                            "舊",
                            "新",
                            progress_callback=request_cancel,
                        )
                except TaskCancelled:
                    return "cancelled at first candidate checkpoint"
                raise AssertionError("preview cancellation was not observed")

            measurements.append(timed("batch_preview_cancel", cancel_preview))
            batch_result = timed("batch_commit", lambda: db.execute_batch_replace(plan))
            committed = batch_result.pop("result")
            batch_result["result"] = {
                "updated": committed.updated,
                "skipped": committed.skipped,
            }
            measurements.append(batch_result)

            changed_entry_id = plan.changes[0].entry_id
            db.save_manual(changed_entry_id, "後續人工修改", propagate=False)
            batch_id = db._one(
                "SELECT batch FROM history WHERE action='batch_replace' ORDER BY id DESC LIMIT 1"
            )[0]
            revert = timed(
                "batch_revert_partial_conflict",
                lambda: db.revert_batch_replace(batch_id),
            )
            revert_result = revert.pop("result")
            revert["result"] = {
                "reverted": revert_result.reverted,
                "skipped": revert_result.skipped,
                "total": revert_result.total,
            }
            measurements.append(revert)

            generation_before = db._data_gen()
            noop_tx = timed("no_op_transaction_generation", lambda: _noop_tx(db))
            generation_after = db._data_gen()
            noop_tx["result"] = {
                "generation_before": generation_before,
                "generation_after": generation_after,
                "generation_advanced": generation_before != generation_after,
            }
            measurements.append(noop_tx)

            return {
                "rows": count,
                "database": "temporary; removed on exit",
                "sqlite_version": sqlite3.sqlite_version,
                "cache_note": (
                    "five repeated quality samples; first follows fixture setup; "
                    "OS page cache is not flushed"
                ),
                "measurements": measurements,
                "explain_query_plan": {
                    "ordinary_list_with_source_and_time": _explain(
                        db, list_sql, list_params
                    ),
                    "quality_filter_source_time_and_full_candidate_scan": _explain(
                        db, quality_sql, quality_params
                    ),
                    "batch_sibling_query": _explain(db, sibling_sql, sibling_params),
                },
            }
        finally:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows",
        nargs="+",
        type=int,
        default=[10_000, 100_000],
        help="database sizes to benchmark (default: 10000 100000)",
    )
    args = parser.parse_args()
    if any(count < 1 for count in args.rows):
        parser.error("row counts must be positive")
    reports = [benchmark(count) for count in args.rows]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


def _noop_tx(db: TranslationDB) -> None:
    with db._tx():
        pass


if __name__ == "__main__":
    raise SystemExit(main())
