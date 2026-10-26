"""Mod DB 大型資料庫的寫入路徑基準（在資料庫複本上執行）。

會修改傳入的資料庫，請先複製（例如用 SQLite backup 或 ``cp``，WAL 已 checkpoint 時）::

    cp moddb.db work.db
    uv run python -m tools.benchmark_moddb_700k_write --db work.db \
        --jar-folder path/to/mods --output .artifacts/moddb-700k/write.json

每個案例只量一次（寫入會改變資料，重複沒有意義），連續手動儲存另外量 20 筆的總和。
批次類操作會在同一案例後還原。數字是診斷證據，不是 CI 門檻。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from translation_tool.translation_db import (
    EntryFilter,
    TranslationDB,
    WriteBackItem,
)
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator

Op = Callable[[], Any]


def _timed(results: list[dict], label: str, op: Op) -> Any:
    started = time.perf_counter()
    value = op()
    elapsed = (time.perf_counter() - started) * 1000
    results.append({"op": label, "ms": round(elapsed, 1)})
    print(f"{label:48s} {elapsed:10.1f} ms", flush=True)
    return value


def _pick_entries(path: Path, version: str, count: int, mod: str) -> list[int]:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT e.id FROM entry e JOIN effective f ON f.entry_id=e.id "
                "WHERE e.mc_version=? AND e.mod_id=? AND f.source=0 "
                "AND e.en_us<>'' ORDER BY e.key LIMIT ?",
                (version, mod, count),
            )
        ]
    finally:
        conn.close()


def _writeback_items(path: Path, version: str, count: int) -> list[WriteBackItem]:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [
            WriteBackItem(kind, mod, key, en, f"測試{key}")
            for kind, mod, key, en in conn.execute(
                "SELECT kind, mod_id, key, en_us FROM entry WHERE mc_version=? "
                "AND kind='lang' AND en_us<>'' ORDER BY mod_id, key LIMIT ?",
                (version, count),
            )
        ]
    finally:
        conn.close()


def _manual_ops(db: TranslationDB, ids: list[int], results: list[dict]) -> None:
    first = ids[0]
    _timed(results, "save_manual single", lambda: db.save_manual(first, "測試A"))
    _timed(
        results, "save_manual single (again)", lambda: db.save_manual(first, "測試B")
    )
    _timed(
        results,
        "save_manual x20 (sum)",
        lambda: [db.save_manual(i, f"連續{i}") for i in ids[1:21]],
    )
    entry = db.get_entry(first)
    _timed(
        results,
        "review_manual single",
        lambda: db.review_manual(
            first,
            expected_zh_tw=entry.zh_tw,
            expected_source=entry.source,
            expected_review_status=entry.review_status,
            expected_checker=entry.checker,
        ),
    )
    history = db._one("SELECT MAX(id) FROM history")[0]
    _timed(results, "revert (whole batch)", lambda: db.revert(history))


def _batch_ops(db: TranslationDB, version: str, mod: str, results: list[dict]) -> None:
    for label, criteria in (
        ("small(mod)", EntryFilter(version=version, mod_id=mod)),
        ("large(version)", EntryFilter(version=version, source=2)),
    ):
        plan = _timed(
            results,
            f"preview_batch_replace {label}",
            lambda c=criteria: db.preview_batch_replace(c, "的", "之"),
        )
        results[-1]["changes"] = len(plan.changes)
        res = _timed(
            results,
            f"execute_batch_replace {label}",
            lambda p=plan: db.execute_batch_replace(p),
        )
        _timed(
            results,
            f"revert_batch_replace {label}",
            lambda r=res: db.revert_batch_replace(r.batch_id),
        )


def _bulk_review_ops(
    db: TranslationDB, version: str, mod: str, results: list[dict]
) -> None:
    plan = _timed(
        results,
        "preview_bulk_review (mod)",
        lambda: db.preview_bulk_review(version, 0, mod_id=mod),
    )
    results[-1]["candidates"] = len(plan.candidates)
    res = _timed(
        results, "execute_bulk_review (mod)", lambda: db.execute_bulk_review(plan)
    )
    row = db._one("SELECT MAX(id) FROM history WHERE batch=?", (res.batch,))
    if row and row[0]:
        _timed(results, "revert bulk review (mod)", lambda: db.revert(row[0]))


def _maintenance_ops(db: TranslationDB, results: list[dict]) -> None:
    for name in (
        "rebuild_effective_columns",
        "rebuild_quality_flags",
        "rebuild_entry_activity",
        "rebuild_stat_summary",
    ):
        _timed(results, name, getattr(db, name))
    for name in (
        "verify_effective_columns",
        "verify_quality_flags",
        "verify_entry_activity",
        "verify_stat_summary",
    ):
        problems = _timed(results, name, getattr(db, name))
        results[-1]["problems"] = len(problems)


def _ingest_ops(
    db: TranslationDB, jar_folder: Path, jars: int, results: list[dict]
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for jar in sorted(jar_folder.glob("*.jar"))[:jars]:
            (Path(tmp) / jar.name).symlink_to(jar)
        for label, version in (("existing 1.20.1", "1.20.1"), ("new 9.9.9", "9.9.9")):
            options = ScanOptions(version=version)

            def scan(opts: ScanOptions = options) -> int:
                done = 0
                for update in scan_folder_generator(db, tmp, opts, workers=4):
                    done += 1 if update.get("progress") is not None else 0
                return done

            _timed(results, f"ingest {jars} jars ({label})", scan)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--jar-folder", type=Path, default=None)
    parser.add_argument("--jars", type=int, default=200)
    parser.add_argument("--version", default="1.20.1")
    parser.add_argument("--mod", default="create")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    results: list[dict] = []
    db = TranslationDB(args.db)
    ids = _pick_entries(args.db, args.version, 30, args.mod)
    _manual_ops(db, ids, results)
    _batch_ops(db, args.version, args.mod, results)
    _bulk_review_ops(db, args.version, args.mod, results)
    for count in (1000, 20000):
        items = _writeback_items(args.db, args.version, count)
        _timed(
            results,
            f"write_back {count} (new version 9.9.{count // 1000})",
            lambda i=items, c=count: db.write_back(f"9.9.{c // 1000}", i),
        )
    if args.jar_folder is not None:
        _ingest_ops(db, args.jar_folder, args.jars, results)
    _maintenance_ops(db, results)
    db.close()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(results, ensure_ascii=False, indent=1), "utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
