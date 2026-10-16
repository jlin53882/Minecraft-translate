"""條目清單排序索引的安裝、唯讀界線與查詢結果契約。"""

from __future__ import annotations

import sqlite3

import pytest

from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.schema import SCHEMA_VERSION, init_schema

INDEX = "idx_entry_list_order"
COLUMNS = ["mc_version", "mod_id", "kind", "key", "id"]
VERSIONS = ("1.20.1", "1.21.1")


@pytest.fixture
def populated_db(tmp_path):
    db = TranslationDB(tmp_path / "entries.db")
    for version in VERSIONS:
        db.ingest(
            version,
            [
                ScanItem(
                    kind,
                    mod,
                    f"key.{key}",
                    f"{mod} {kind} {key}",
                    "測試譯文" if key % 2 == 0 else "",
                )
                for mod in ("zeta", "middle", "alpha")
                for kind in ("patchouli", "lang", "guide", "advancement")
                for key in reversed(range(5))
            ],
        )
    yield db
    db.close()


def _columns(conn):
    return [row[2] for row in conn.execute(f"PRAGMA index_info({INDEX})")]


def _data_snapshot(conn):
    tables = (
        "meta",
        "entry",
        "translation",
        "effective",
        "history",
        "src_change",
        "scan_run",
        "stat_cache",
        "cleanup_batch",
    )
    return {
        table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        for table in tables
    }


def test_new_database_installs_entry_list_order_index(tmp_path):
    db = TranslationDB(tmp_path / "new.db")
    try:
        assert _columns(db._conn) == COLUMNS
        assert db._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone() == (str(SCHEMA_VERSION),)
    finally:
        db.close()


def test_existing_database_gets_index_on_writable_open_without_data_changes(
    populated_db,
):
    db = populated_db
    db._conn.execute(f"DROP INDEX IF EXISTS {INDEX}")
    db._conn.commit()
    before = _data_snapshot(db._conn)
    # A separate writable repository must install the index even when the
    # stored schema version already equals the current version.
    path = db._conn.execute("PRAGMA database_list").fetchone()[2]
    writer = TranslationDB(path)
    try:
        assert _columns(writer._conn) == COLUMNS
        assert _data_snapshot(writer._conn) == before
        rootpage = writer._conn.execute(
            "SELECT rootpage FROM sqlite_master WHERE name=?", (INDEX,)
        ).fetchone()
        init_schema(writer._conn)
        assert (
            writer._conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name=?", (INDEX,)
            ).fetchone()
            == rootpage
        )
        assert _data_snapshot(writer._conn) == before
        assert writer._conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
    finally:
        writer.close()


def test_readonly_open_does_not_install_missing_index(populated_db):
    db = populated_db
    db._conn.execute(f"DROP INDEX IF EXISTS {INDEX}")
    db._conn.commit()
    before = _data_snapshot(db._conn)
    path = db._conn.execute("PRAGMA database_list").fetchone()[2]
    reader = TranslationDB(path, readonly=True)
    try:
        assert _columns(reader._conn) == []
        rows, total = reader.list_entries(VERSIONS[0], limit=50, offset=50)
        assert (len(rows), total) == (10, 60)
        assert _data_snapshot(reader._conn) == before
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader._conn.execute("CREATE TABLE unexpected_write (id INTEGER)")
    finally:
        reader.close()


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"mod_id": "middle"},
        {"kind": "guide"},
        {"state": "none"},
        {"query": "alpha"},
        {"source": 1},
        {"review_status": "unreviewed"},
    ],
)
def test_index_preserves_filtered_page_rows_order_and_total(
    populated_db, version, filters
):
    db = populated_db
    db._conn.execute(f"DROP INDEX IF EXISTS {INDEX}")
    db._conn.commit()
    offsets = (0, 25, 50, 60)
    before = [
        db.list_entries(version, limit=50, offset=offset, **filters)
        for offset in offsets
    ]
    init_schema(db._conn)
    assert _columns(db._conn) == COLUMNS
    after = [
        db.list_entries(version, limit=50, offset=offset, **filters)
        for offset in offsets
    ]
    assert after == before
    if not filters:
        assert [(len(rows), total) for rows, total in after] == [
            (50, 60),
            (35, 60),
            (10, 60),
            (0, 60),
        ]
        expected = sorted(before[0][0], key=lambda r: (r.mod_id, r.kind, r.key, r.id))
        assert after[0][0] == expected


@pytest.mark.parametrize("version", VERSIONS)
def test_public_default_page_uses_index_without_temporary_ordering(
    populated_db, version
):
    db = populated_db
    statements = []
    db._conn.set_trace_callback(statements.append)
    try:
        rows, total = db.list_entries(version, limit=50, offset=50)
    finally:
        db._conn.set_trace_callback(None)
    assert (len(rows), total) == (10, 60)
    page_sql = next(
        sql
        for sql in statements
        if "ORDER BY e.mod_id, e.kind, e.key, e.id LIMIT" in sql
    )
    plan = db._conn.execute("EXPLAIN QUERY PLAN " + page_sql).fetchall()
    details = [row[3] for row in plan]
    assert any(INDEX in detail for detail in details), details
    assert not any("TEMP B-TREE" in detail.upper() for detail in details), details
