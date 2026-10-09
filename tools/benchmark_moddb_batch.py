"""Generate a disposable 10k Mod DB and time list, filter, batch and revert paths.

Run with ``uv run python -m tools.benchmark_moddb_batch``. Timings are machine
and SQLite-build specific; the report is diagnostic evidence, not a CI threshold.
"""

from __future__ import annotations

import json
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
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope


def timed(label: str, operation):
    started = time.perf_counter()
    result = operation()
    elapsed = time.perf_counter() - started
    return {"name": label, "seconds": round(elapsed, 4), "result": result}


def main() -> int:
    count = 10_000
    with tempfile.TemporaryDirectory(prefix="moddb-batch-bench-") as folder:
        db = TranslationDB(Path(folder) / "benchmark.db")
        try:
            items = [
                ScanItem(
                    KIND_LANG,
                    "benchmark",
                    f"item.bench.{index:05d}",
                    f"Source {index} %s",
                    f"譯文舊{index:05d}" if index % 2 else f"譯文舊{index:05d} %s",
                )
                for index in range(count)
            ]
            ingest = timed(
                "ingest_setup",
                lambda: db.ingest("1.21.1", items).new_entries,
            )
            base = EntryFilter(version="1.21.1")
            time_criteria = EntryFilter(
                version="1.21.1",
                time=TimeFilter(
                    kind="entry_created",
                    start_utc="2000-01-01 00:00:00",
                    end_utc="2100-01-01 00:00:00",
                ),
            )
            quality_criteria = EntryFilter(
                version="1.21.1",
                quality=QualityFilter(status="mismatch"),
                sort_by="effective_updated_newest",
            )

            def list_counts(criteria: EntryFilter) -> dict[str, int]:
                rows, total = db.list_entries(criteria=criteria, limit=50)
                return {"rows": len(rows), "total": total}

            measurements = [ingest]
            measurements.append(timed("ordinary_list_page", lambda: list_counts(base)))
            measurements.append(
                timed("time_filter_count_and_page", lambda: list_counts(time_criteria))
            )
            measurements.append(
                timed(
                    "quality_count_sort_and_page",
                    lambda: list_counts(quality_criteria),
                )
            )

            explain = db._q(
                "EXPLAIN QUERY PLAN SELECT id FROM entry WHERE mc_version=? ORDER BY id LIMIT 50",
                ("1.21.1",),
            )
            measurements.append(
                timed(
                    "batch_preview",
                    lambda: db.preview_batch_replace(base, "舊", "新"),
                )
            )
            plan = measurements[-1]["result"]
            measurements[-1]["result"] = {
                "root_ids": len(plan.root_ids),
                "updates": plan.update_count,
                "skipped": plan.skipped_count,
            }

            cancelled = Event()

            def request_cancel(stage: str, _progress: float) -> None:
                if stage == "查詢符合條目":
                    cancelled.set()

            def cancel_preview():
                try:
                    with cancel_scope(cancelled.is_set):
                        db.preview_batch_replace(
                            base,
                            "舊",
                            "新",
                            progress_callback=request_cancel,
                        )
                except TaskCancelled:
                    return "cancelled at first candidate checkpoint"
                raise AssertionError("preview cancellation was not observed")

            measurements.append(timed("batch_preview_cancel", cancel_preview))
            measurements.append(
                timed(
                    "batch_commit",
                    lambda: {
                        "updated": db.execute_batch_replace(plan).updated,
                    },
                )
            )
            measurements.append(
                timed(
                    "batch_revert",
                    lambda: {
                        "reverted": db.revert_batch_replace(
                            db._one(
                                "SELECT batch FROM history WHERE action='batch_replace' LIMIT 1"
                            )[0]
                        ).reverted,
                    },
                )
            )
            print(
                json.dumps(
                    {
                        "rows": count,
                        "database": "temporary; removed on exit",
                        "measurements": measurements,
                        "explain_query_plan": [list(row) for row in explain],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
