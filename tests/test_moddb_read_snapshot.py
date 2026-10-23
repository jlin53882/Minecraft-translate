"""Mod DB 面板的唯讀快照：交易與連線由 service 的 context manager 擁有。"""

from __future__ import annotations

import sqlite3

import pytest

from app.services_impl.moddb_service import (
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
