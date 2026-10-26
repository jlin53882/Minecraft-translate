"""掃描匯入管線：批次交易、行程池讀取、取消與退回。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    ScanItem,
    TranslationDB,
    scan_pool,
)
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "t.db")
    yield d
    d.close()


def _make_jars(folder: Path, count: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        with zipfile.ZipFile(folder / f"mod{index:03d}.jar", "w") as zf:
            zf.writestr(
                f"assets/mod{index:03d}/lang/en_us.json",
                json.dumps({"item.a": f"Steel Casing {index}", "item.b": "Alloy"}),
            )
            zf.writestr(
                f"assets/mod{index:03d}/lang/zh_cn.json",
                json.dumps({"item.b": "合金"}),
            )


def _verify_all(db: TranslationDB) -> None:
    assert db.verify_stat_summary() == []
    assert db.verify_effective_columns() == []
    assert db.verify_entry_activity() == []
    assert db.verify_quality_flags() == []


def _snapshot(db: TranslationDB) -> list[tuple]:
    return db._q(
        "SELECT mc_version, mod_id, kind, key, en_us FROM entry ORDER BY 1,2,3,4"
    ) + db._q("SELECT source, zh_tw FROM translation ORDER BY entry_id, source")


def test_ingest_many_matches_sequential_ingest(tmp_path):
    batches = [
        [
            ScanItem(KIND_LANG, "foo", "a", "Alpha", "甲", ""),
            ScanItem(KIND_LANG, "foo", "b", "Beta", "", "乙"),
        ],
        [
            ScanItem(KIND_LANG, "foo", "a", "Alpha", "", ""),
            ScanItem(KIND_LANG, "bar", "c", "Gamma", "", ""),
        ],
    ]
    convert = lambda text: text + "!"
    one = TranslationDB(tmp_path / "one.db")
    many = TranslationDB(tmp_path / "many.db")
    sequential = [one.ingest("1.20.1", items, convert) for items in batches]
    batched = many.ingest_many("1.20.1", batches, convert)
    assert batched == sequential
    assert _snapshot(many) == _snapshot(one)
    _verify_all(many)
    one.close()
    many.close()


def test_ingest_many_commits_once_and_rolls_back_together(db):
    before = db.count_entries()
    bad = [ScanItem(KIND_LANG, "foo", "a", "Alpha", "甲", ""), None]
    with pytest.raises(AttributeError):
        db.ingest_many(
            "1.20.1", [[ScanItem(KIND_LANG, "foo", "z", "Zed", "", "")], bad]
        )
    assert db.count_entries() == before
    _verify_all(db)


def test_scan_writes_in_batches(db, tmp_path, monkeypatch):
    _make_jars(tmp_path / "m", 70)
    calls: list[int] = []
    real = db.ingest_many

    def spy(version, batches, convert=None):
        calls.append(len(batches))
        return real(version, batches, convert)

    monkeypatch.setattr(db, "ingest_many", spy)
    updates = list(
        scan_folder_generator(db, tmp_path / "m", ScanOptions("1.20.1"), workers=1)
    )
    assert sum(calls) == 70 and len(calls) < 70
    assert max(calls) <= 32
    progress = [u["progress"] for u in updates if u.get("progress") is not None]
    assert progress[-1] == 1.0
    assert updates[-1]["report"].stats.new_entries == 140
    _verify_all(db)


def test_process_pool_scan_matches_thread_scan(tmp_path, monkeypatch):
    _make_jars(tmp_path / "m", scan_pool.PROCESS_MIN_JARS + 2)
    results = {}
    for label, workers in (("threads", 1), ("processes", 2)):
        target = TranslationDB(tmp_path / f"{label}.db")
        updates = list(
            scan_folder_generator(
                target, tmp_path / "m", ScanOptions("1.20.1"), workers=workers
            )
        )
        results[label] = (_snapshot(target), updates[-1]["report"].as_dict())
        _verify_all(target)
        target.close()
    assert results["processes"] == results["threads"]


def test_process_pool_failure_falls_back_to_threads(db, tmp_path, monkeypatch):
    _make_jars(tmp_path / "m", scan_pool.PROCESS_MIN_JARS)

    def broken(*_args, **_kwargs):
        raise OSError("spawn unavailable")

    monkeypatch.setattr(scan_pool, "_start_process_pool", broken)
    updates = list(
        scan_folder_generator(db, tmp_path / "m", ScanOptions("1.20.1"), workers=3)
    )
    assert updates[-1]["report"].stats.new_entries == scan_pool.PROCESS_MIN_JARS * 2


def test_process_pool_scan_can_be_cancelled(db, tmp_path):
    _make_jars(tmp_path / "m", scan_pool.PROCESS_MIN_JARS * 2)
    seen = {"progress": 0}

    def cancelled() -> bool:
        return seen["progress"] >= 3

    updates = []
    for update in scan_folder_generator(
        db, tmp_path / "m", ScanOptions("1.20.1"), workers=2, should_cancel=cancelled
    ):
        updates.append(update)
        if update.get("progress"):
            seen["progress"] += 1
    report = updates[-1]["report"]
    assert report.cancelled
    # 已讀取的 jar 在取消時仍會寫入，統計與資料庫一致
    assert db.count_entries() == report.stats.new_entries
    _verify_all(db)


def test_replay_logs_keeps_level(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        scan_pool.replay_logs(
            [(logging.WARNING, "warn-msg"), (logging.INFO, "info-msg")]
        )
    levels = {r.getMessage(): r.levelno for r in caplog.records}
    assert levels.get("warn-msg") == logging.WARNING
    assert levels.get("info-msg") == logging.INFO
