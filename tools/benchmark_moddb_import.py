"""以隔離資料夾量測 ModDB 匯入／清理流程。

這個工具不會開啟正式資料庫，也不會修改桌面上的 jar／zip。預設使用暫存目錄
建立合成語言檔，量測掃描、抽取、SQLite ingest、清理檢查檔、preview、apply
與 revert；``--real-root`` 只會以唯讀方式逐一掃描實際 archive。

範例：

``uv run python -m tools.benchmark_moddb_import --sizes 1000 10000 70000``
``uv run python -m tools.benchmark_moddb_import --real-root <mods 資料夾>``
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from statistics import median

from translation_tool.core.translatable_extractor import extract_translatables
from translation_tool.translation_db import KIND_LANG, ScanItem, TranslationDB
from translation_tool.translation_db.scanner import ScanOptions, find_jars, scan_jar

VERSION = "1.20.1"


def _percentile(samples: list[float], ratio: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * ratio + 0.999999))
    return round(ordered[index], 4)


def _samples(operation, repeats: int = 3) -> dict[str, object]:
    values: list[float] = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        values.append(time.perf_counter() - started)
    return {
        "repeats": repeats,
        "seconds": [round(value, 4) for value in values],
        "median_seconds": round(median(values), 4),
        "p95_seconds": _percentile(values, 0.95),
        "result": result,
    }


def _make_items(count: int) -> list[ScanItem]:
    """建立正常文字與少量結構值，讓清理 pipeline 也有實際候選可驗證。"""
    items: list[ScanItem] = []
    for index in range(count):
        if index % 100 == 0:
            items.append(
                ScanItem(
                    KIND_LANG,
                    "benchmark",
                    f"item.benchmark.internal_{index:06d}",
                    "minecraft:stone",
                )
            )
        else:
            items.append(
                ScanItem(
                    KIND_LANG,
                    "benchmark",
                    f"item.benchmark.entry_{index:06d}",
                    f"Example source sentence number {index}.",
                )
            )
    return items


def _write_archive(path: Path, count: int) -> None:
    values = {f"b.e{index:06d}": "Text entry" for index in range(count)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "assets/benchmark/lang/en_us.json",
            json.dumps(values, ensure_ascii=False, separators=(",", ":")),
        )


def benchmark_synthetic(count: int) -> dict[str, object]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"moddb-import-{count}-") as folder_name:
        folder = Path(folder_name)
        archive = folder / "benchmark.jar"
        _write_archive(archive, count)
        with zipfile.ZipFile(archive) as archive_file:
            raw = json.loads(archive_file.read("assets/benchmark/lang/en_us.json"))

        scan_options = ScanOptions(version=VERSION, clean_english=True)

        def scan() -> dict[str, int]:
            result = scan_jar(archive, scan_options, dir_names=())
            if result.error:
                raise RuntimeError(result.error)
            return {"items": len(result.items), "failed": 0}

        def extract() -> int:
            return len(
                extract_translatables(raw, Path("assets/benchmark/lang/en_us.json"))
            )

        scan_first = scan()
        scan_warm = _samples(scan)
        extract_warm = _samples(extract)
        items = _make_items(count)
        ingest_samples = []
        for repeat in range(3):
            db = TranslationDB(folder / f"ingest-{repeat}.db")
            try:
                ingest_samples.append(
                    _samples(
                        lambda db=db: db.ingest(VERSION, items).new_entries,
                        repeats=1,
                    )
                )
            finally:
                db.close()

        db = TranslationDB(folder / "pipeline.db")
        try:
            db.ingest(VERSION, items)
            report_path = folder / "cleanup.json"
            cleanup_candidates = len(db.cleanup_candidates(VERSION)[0])
            report = _samples(
                lambda: db.export_cleanup_report(VERSION, report_path).default_selected,
            )
            preview = _samples(lambda: db.preview_cleanup(report_path).deletable)
            plan = db.preview_cleanup(report_path)
            apply_result = _samples(
                lambda: db.apply_cleanup(report_path, plan.token).deletable,
                repeats=1,
            )
            batch = db.last_cleanup_batch()
            if batch is None:
                raise RuntimeError("清理 benchmark 沒有建立 cleanup_batch")
            revert = _samples(
                lambda: db.revert_cleanup(batch.batch_id).restored,
                repeats=1,
            )
        finally:
            db.close()

        return {
            "rows": count,
            "archive_bytes": archive.stat().st_size,
            "scan_first": scan_first,
            "scan_warm": scan_warm,
            "extract_warm": extract_warm,
            "ingest": ingest_samples,
            "cleanup_candidates": cleanup_candidates,
            "report_export": report,
            "preview": preview,
            "apply": apply_result,
            "revert": revert,
            "total_seconds": round(time.perf_counter() - started, 4),
            "database": "temporary; removed on exit",
            "sqlite_version": sqlite3.sqlite_version,
        }


def benchmark_real(root: Path, version: str) -> dict[str, object]:
    """掃描實際 archive；此函式不建立 TranslationDB，也不寫入資料庫。"""
    archives = find_jars(root, translated=False)
    started = time.perf_counter()
    item_count = 0
    nested_count = 0
    failures: list[str] = []
    jars_with_items = 0
    for archive in archives:
        result = scan_jar(archive, ScanOptions(version=version), dir_names=())
        if result.error:
            failures.append(str(archive))
        item_count += len(result.items)
        nested_count += result.nested_jars
        jars_with_items += int(bool(result.items))
    return {
        "root": str(root),
        "version": version,
        "archives": len(archives),
        "archives_with_items": jars_with_items,
        "items": item_count,
        "nested_jars": nested_count,
        "failed": len(failures),
        "failure_samples": failures[:5],
        "seconds": round(time.perf_counter() - started, 4),
        "database": "not opened; read-only archive scan",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=[1_000, 10_000, 70_000],
        help="合成資料列數；預設 1000、10000、70000",
    )
    parser.add_argument("--real-root", type=Path, help="只讀掃描實際 mods 資料夾")
    parser.add_argument("--version", default=VERSION)
    args = parser.parse_args()
    if any(size < 1 for size in args.sizes):
        parser.error("sizes 必須是正整數")

    payload: dict[str, object] = {
        "mode": "isolated synthetic database plus read-only archive scan",
        "synthetic": [benchmark_synthetic(size) for size in args.sizes],
    }
    if args.real_root:
        payload["real"] = benchmark_real(args.real_root, args.version)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
