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
from pathlib import Path
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
                    timed(
                        "quality_count_sort_and_page",
                        lambda: list_result(quality_criteria),
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

            return {
                "rows": count,
                "database": "temporary; removed on exit",
                "sqlite_version": sqlite3.sqlite_version,
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


if __name__ == "__main__":
    raise SystemExit(main())
