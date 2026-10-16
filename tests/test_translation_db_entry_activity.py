"""條目最近人工活動（entry_activity）：與歷史重算一致、兩段式排序與原本排序逐頁相同。"""

from __future__ import annotations

import sqlite3

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    ScanItem,
    TranslationDB,
    entry_activity,
)
from translation_tool.translation_db.schema import SRC_JAR_TW, SRC_MANUAL

SORT = "manual_activity_newest"


def _seed(db: TranslationDB) -> None:
    for version in ("1.20.1", "1.21.1"):
        db.ingest(
            version,
            [
                ScanItem(KIND_LANG, f"mod{i % 3}", f"k{i}", f"Text {i}", f"譯{i}")
                for i in range(40)
            ]
            + [
                ScanItem(KIND_LANG, "mod9", f"blank{i}", f"Blank {i}") for i in range(5)
            ],
        )
    rows = db.list_entries("1.20.1", limit=500)[0]
    for row in rows[:12]:
        db.save_manual(row.id, f"人工{row.id}", actor="editor", propagate=False)
    for row in rows[5:9]:
        db.review_manual(row.id, expected_zh_tw=f"人工{row.id}", actor="checker")
    # 不同時間、相同時間與不列入排序的動作都要涵蓋
    with db._tx() as conn:
        for index, row in enumerate(rows[15:30]):
            conn.execute(
                "INSERT INTO history (entry_id, batch, action, at) VALUES (?,?,?,?)",
                (row.id, "seed", "revert", f"2020-01-0{1 + index % 3} 00:00:00"),
            )
        conn.execute(
            "INSERT INTO history (entry_id, batch, action, at) VALUES (?,?,?,?)",
            (rows[30].id, "seed", "quality_repair", "2099-01-01 00:00:00"),
        )


def _page_ids(db, criteria, offset, limit=7, *, indexed=True):
    original = db._activity_ready
    if not indexed:
        db._activity_ready = lambda: False
    try:
        rows, total = db.list_entries(criteria=criteria, limit=limit, offset=offset)
    finally:
        db._activity_ready = original
    return [row.id for row in rows], total


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "activity.db")
    yield d
    d.close()


def test_new_database_installs_ready_table(db):
    assert entry_activity.is_installed(db._conn)
    assert db._activity_ready()
    _seed(db)
    assert db.verify_entry_activity() == []
    assert db._one("SELECT COUNT(*) FROM entry_activity")[0] > 0


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"state": "none"},
        {"state": "ok"},
        {"source": SRC_MANUAL},
        {"source": SRC_JAR_TW},
        {"review_status": "reviewed"},
        {"mod_id": "mod1"},
        {"query": "Text 1"},
    ],
)
@pytest.mark.parametrize("version", ["1.20.1", "1.21.1"])
def test_two_phase_sort_matches_history_aggregate(db, version, extra):
    _seed(db)
    criteria = EntryFilter(version=version, sort_by=SORT, **extra)
    cond, params = db._filter_sql(criteria)
    dated = db._one(db._two_phase_sort_sql(SORT, cond)[1], params)[0]
    total = db.list_entries(criteria=criteria, limit=1)[1]
    offsets = {0, 3, max(0, dated - 2), dated, dated + 1, total - 1, total + 5}
    for offset in sorted(o for o in offsets if o >= 0):
        assert _page_ids(db, criteria, offset) == _page_ids(
            db, criteria, offset, indexed=False
        ), offset
    assert _page_ids(db, criteria, 0, limit=500) == _page_ids(
        db, criteria, 0, limit=500, indexed=False
    )


def test_page_queries_read_indexes_without_sorting(db):
    _seed(db)
    for extra in ({}, {"mod_id": "mod1"}, {"state": "diff"}, {"source": SRC_MANUAL}):
        criteria = EntryFilter(version="1.20.1", sort_by=SORT, **extra)
        cond, params = db._filter_sql(criteria)
        dated, _count, undated = db._two_phase_sort_sql(SORT, cond)
        for sql in (dated, undated):
            plan = " ".join(
                row[3] for row in db._q(f"EXPLAIN QUERY PLAN {sql}", (*params, 10, 0))
            )
            assert "TEMP B-TREE" not in plan, (extra, sql, plan)


def test_history_insert_keeps_latest_and_entry_changes_follow(db):
    _seed(db)
    row = db.list_entries("1.21.1", limit=1)[0][0]
    with db._tx() as conn:
        for at in ("2030-01-01 00:00:00", "2025-01-01 00:00:00"):
            conn.execute(
                "INSERT INTO history (entry_id, batch, action, at) VALUES (?,?,?,?)",
                (row.id, "t", "manual", at),
            )
    assert db._one(
        "SELECT last_at FROM entry_activity WHERE entry_id=?", (row.id,)
    ) == ("2030-01-01 00:00:00",)
    with db._tx() as conn:
        conn.execute("UPDATE entry SET mc_version='9.9' WHERE id=?", (row.id,))
    assert db.verify_entry_activity() == []
    with db._tx() as conn:
        conn.execute("DELETE FROM entry WHERE id=?", (row.id,))
    assert db._one("SELECT 1 FROM entry_activity WHERE entry_id=?", (row.id,)) is None
    assert db.verify_entry_activity() == []


def _drop_activity(path):
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER activity_history_insert")
        conn.execute("DROP TRIGGER activity_entry_version")
        conn.execute("DROP TABLE entry_activity")
        conn.execute("DELETE FROM meta WHERE key LIKE 'activity%'")


def test_readonly_old_database_falls_back_to_history_aggregate(tmp_path):
    path = tmp_path / "old.db"
    writer = TranslationDB(path)
    _seed(writer)
    criteria = EntryFilter(version="1.20.1", sort_by=SORT)
    expected = _page_ids(writer, criteria, 0, limit=500)
    writer.close()
    _drop_activity(path)
    reader = TranslationDB(path, readonly=True)
    try:
        assert not reader._activity_ready()
        assert _page_ids(reader, criteria, 0, limit=500) == expected
    finally:
        reader.close()
    # 可寫開啟時安裝並由歷史填入
    migrated = TranslationDB(path)
    try:
        assert migrated._activity_ready()
        assert migrated.verify_entry_activity() == []
        assert _page_ids(migrated, criteria, 0, limit=500) == expected
    finally:
        migrated.close()


def test_invalid_flag_falls_back_until_rebuilt(db):
    _seed(db)
    criteria = EntryFilter(version="1.20.1", sort_by=SORT)
    expected = _page_ids(db, criteria, 0, limit=500, indexed=False)
    with db._tx() as conn:
        conn.execute("DELETE FROM entry_activity")
        conn.execute("UPDATE meta SET value='0' WHERE key='activity_valid'")
    assert not db._activity_ready()
    assert _page_ids(db, criteria, 0, limit=500) == expected
    assert db.verify_entry_activity()
    db.rebuild_entry_activity()
    assert db._activity_ready() and db.verify_entry_activity() == []
    assert _page_ids(db, criteria, 0, limit=500) == expected
