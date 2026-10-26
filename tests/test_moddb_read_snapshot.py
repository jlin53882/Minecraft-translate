"""Mod DB 面板的唯讀快照：交易與連線由 service 的 context manager 擁有。"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.services_impl.moddb_service import (
    QueryCancelled,
    QueryInterrupt,
    database_data_generation,
    moddb_read_snapshot,
)
from app.views.moddb.panel_refresh import load_panel_snapshot
from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.settings import DbSettings


def _settings(tmp_path) -> DbSettings:
    path = tmp_path / "db.sqlite"
    db = TranslationDB(path)
    db.ingest("1.21.1", [ScanItem("lang", "m", "item.m.a", "Alpha")])
    db.close()
    return DbSettings(path=str(path))


def test_missing_database_yields_none_and_no_generation(tmp_path):
    settings = DbSettings(path=str(tmp_path / "absent.sqlite"))
    with moddb_read_snapshot(settings) as db:
        assert db is None
        assert database_data_generation(db) is None


def test_snapshot_rolls_back_and_closes_the_connection(tmp_path):
    settings = _settings(tmp_path)
    with moddb_read_snapshot(settings) as db:
        assert db._conn.in_transaction
        assert database_data_generation(db)
        conn = db._conn
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_snapshot_closes_even_when_the_reader_raises(tmp_path):
    settings = _settings(tmp_path)
    holder = {}
    with pytest.raises(RuntimeError), moddb_read_snapshot(settings) as db:
        holder["conn"] = db._conn
        raise RuntimeError("read failed")
    with pytest.raises(sqlite3.ProgrammingError):
        holder["conn"].execute("SELECT 1")


def test_snapshot_does_not_see_commits_made_after_it_started(tmp_path):
    settings = _settings(tmp_path)
    with moddb_read_snapshot(settings) as db:
        before = db.versions()
        generation = database_data_generation(db)
        writer = TranslationDB(settings.resolved_path())
        writer.ingest("1.20.1", [ScanItem("lang", "m", "item.m.b", "Beta")])
        writer.close()
        assert db.versions() == before
        assert database_data_generation(db) == generation


def test_load_panel_snapshot_scan_tab_reads_versions_and_leaves_no_transaction(
    tmp_path,
):
    settings = _settings(tmp_path)
    snapshot = load_panel_snapshot("scan", {}, settings)
    assert "1.21.1" in snapshot["database_versions"]
    assert snapshot["db_generation"]
    with pytest.raises(ValueError):
        load_panel_snapshot("unknown", {}, settings)


_ENDLESS_QUERY = (
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 100000000) "
    "SELECT count(*) FROM n"
)


def test_cancel_interrupts_a_running_query_in_the_snapshot(tmp_path):
    settings = _settings(tmp_path)
    interrupt = QueryInterrupt()
    started = threading.Event()

    def cancel_when_running():
        started.wait(5)
        threading.Event().wait(0.3)  # 讓查詢先開始執行
        interrupt.cancel()

    threading.Thread(target=cancel_when_running, daemon=True).start()
    with (
        pytest.raises(QueryCancelled),
        moddb_read_snapshot(settings, interrupt=interrupt) as db,
    ):
        started.set()
        db._conn.execute(_ENDLESS_QUERY).fetchall()
    assert interrupt.cancelled


def test_cancel_before_the_connection_is_used_skips_the_read(tmp_path):
    settings = _settings(tmp_path)
    interrupt = QueryInterrupt()
    interrupt.cancel()
    with (
        pytest.raises(QueryCancelled),
        moddb_read_snapshot(settings, interrupt=interrupt),
    ):
        raise AssertionError("已取消的請求不該執行查詢")


def test_other_database_errors_are_not_reported_as_cancelled(tmp_path):
    settings = _settings(tmp_path)
    interrupt = QueryInterrupt()
    with (
        pytest.raises(sqlite3.OperationalError),
        moddb_read_snapshot(settings, interrupt=interrupt) as db,
    ):
        db._conn.execute("SELECT * FROM no_such_table")
