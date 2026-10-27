"""掃描匯入管線：批次交易、行程池讀取、取消與退回。"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app.services_impl.moddb_service import DbSettings
from app.services_impl.pipelines import bundle_service
from app.views.moddb.lm_db_options import LmDbRunSnapshot
from app.views.pipeline.pipeline_actions import PipelineActions
from app.views.pipeline.pipeline_config import PipelineConfig
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


def test_scan_persists_content_issues_without_creating_fake_entries(db, tmp_path):
    jar = tmp_path / "invalid-book.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr(
            "assets/herbsandharvest/books/chapters/herbs.json",
            '{"pages": []}\n{"extra": "document"}',
        )
        archive.writestr(
            "assets/herbsandharvest/books/chapters/foods.json",
            json.dumps(
                {
                    "title": "herbsandharvest.book.title",
                    "pages": [
                        {
                            "type": "standard",
                            "summary": "The Pantry Update adds useful food items.",
                        }
                    ],
                }
            ),
        )

    report = list(
        scan_folder_generator(db, tmp_path, ScanOptions("1.21.1"), workers=1)
    )[-1]["report"]

    assert any(
        issue["reason"] == "invalid_book_json" and issue["status"] == "parse_failed"
        for issue in report.issues
    )
    assert db.content_issues("1.21.1", origin="scan")
    assert db.count_entries() == 1
    assert db._q("SELECT key FROM entry")[0][0].endswith("foods.json#pages[0].summary")


def test_scan_persists_bad_archive_as_issue_without_partial_entries(db, tmp_path):
    (tmp_path / "broken.jar").write_bytes(b"not a zip")

    report = list(
        scan_folder_generator(db, tmp_path, ScanOptions("1.21.1"), workers=1)
    )[-1]["report"]

    assert report.jars_failed == ["broken.jar"]
    issue = db.content_issues("1.21.1", origin="scan")[0]
    assert issue["reason"] == "archive_read_failed"
    assert issue["status"] == "parse_failed"
    assert db.count_entries() == 0


def test_scan_content_issues_replace_previous_snapshot(db, tmp_path):
    context = str(tmp_path / "mods")
    first = [
        {
            "source_archive": "broken.jar",
            "source_member": "",
            "kind": "archive",
            "status": "parse_failed",
            "reason": "archive_read_failed",
        }
    ]
    db.record_scan("1.21.1", context, {"issue_count": 1}, issues=first)
    assert db.content_issues("1.21.1", origin="scan", context=context)

    db.record_scan("1.21.1", context, {"issue_count": 0}, issues=[])
    assert db.content_issues("1.21.1", origin="scan", context=context) == []


def test_pipeline_content_issues_replace_previous_manifest_snapshot(db, tmp_path):
    context = str(tmp_path / "mods")
    first = [
        {
            "source": str(tmp_path / "pending.json"),
            "relative_path": "assets/demo/lang/en_us.json",
            "status": "pending",
            "reason": "translation_pending",
        }
    ]
    assert db.record_content_issues("1.21.1", context, first) == 1
    assert db.content_issues("1.21.1", origin="pipeline")[0]["status"] == "pending"

    assert db.record_content_issues("1.21.1", context, []) == 0
    assert db.content_issues("1.21.1", origin="pipeline") == []


def test_pipeline_manifest_is_written_to_the_same_isolated_db_issue_surface(tmp_path):
    db_path = tmp_path / "pipeline.db"
    TranslationDB(db_path).close()
    manifest = tmp_path / "translation_output_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source": "translated.json",
                        "relative_path": "assets/demo/lang/zh_tw.json",
                        "status": "emitted",
                        "reason": "resource_pack_asset",
                    },
                    {
                        "source": "pending.json",
                        "relative_path": "assets/demo/lang/en_us.json",
                        "status": "pending",
                        "reason": "translation_pending",
                    },
                    {
                        "source": "broken.json",
                        "relative_path": "assets/demo/books/chapter.json",
                        "status": "parse_failed",
                        "reason": "json_parse_failed_before_translation",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    cfg = PipelineConfig(str(tmp_path / "mods"), str(tmp_path / "output"))
    settings = DbSettings(path=str(db_path), version="1.21.1")
    snapshot = LmDbRunSnapshot(True, "1.21.1", "test", "", settings)

    class Session:
        def __init__(self):
            self.logs = []

        def add_log(self, text, level="info"):
            self.logs.append((text, level))

    session = Session()
    PipelineActions._record_pipeline_content_issues(
        {"lm_db_snapshot": snapshot},
        cfg,
        {"manifest_path": str(manifest)},
        session,
    )

    db = TranslationDB(db_path)
    try:
        issues = db.content_issues(
            "1.21.1", origin="pipeline", context=str(cfg.input_dir)
        )
        assert {(row["status"], row["reason"]) for row in issues} == {
            ("pending", "translation_pending"),
            ("parse_failed", "json_parse_failed_before_translation"),
        }
        assert any("已同步 2 筆" in text for text, _level in session.logs)
    finally:
        db.close()


def test_pipeline_manifest_sync_keeps_manifest_when_db_write_is_locked(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "translation_output_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source": "pending.json",
                        "output": "",
                        "status": "pending",
                        "reason": "translation_pending",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class LockedDB:
        def record_content_issues(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        def close(self):
            pass

    monkeypatch.setattr(bundle_service, "open_db", lambda *_args, **_kwargs: LockedDB())

    result = bundle_service.sync_pipeline_content_manifest(
        settings=DbSettings(path=str(tmp_path / "locked.db"), version="1.21.1"),
        version="1.21.1",
        context="mods",
        manifest_path=str(manifest),
    )

    assert result.count == 0
    assert result.warning and "database is locked" in result.warning


def test_replay_logs_keeps_level(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        scan_pool.replay_logs(
            [(logging.WARNING, "warn-msg"), (logging.INFO, "info-msg")]
        )
    levels = {r.getMessage(): r.levelno for r in caplog.records}
    assert levels.get("warn-msg") == logging.WARNING
    assert levels.get("info-msg") == logging.INFO
