"""Mod 資料庫第二輪後端效能修正：同原文旗標、清理候選快取、時間範圍查詢計畫。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    ScanItem,
    TranslationDB,
    WriteBackItem,
    quality_flags,
)
from translation_tool.translation_db import cleanup as cleanup_module
from translation_tool.translation_db.models import TimeFilter
from translation_tool.translation_db.schema import SRC_AI


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "t.db")
    yield d
    d.close()


def _seed_same_as_source(db: TranslationDB) -> None:
    db.ingest(
        "1.20.1",
        [
            ScanItem(KIND_LANG, "foo", f"k{i}", f"Text number {i}", "", "")
            for i in range(5)
        ],
    )
    db.write_back(
        "1.20.1",
        [
            WriteBackItem(KIND_LANG, "foo", "k0", "Text number 0", "Text number 0"),
            WriteBackItem(KIND_LANG, "foo", "k1", "Text number 1", "文字 1"),
            WriteBackItem(KIND_LANG, "foo", "k2", "Text number 2", "Text number 2"),
        ],
        source=SRC_AI,
    )


def _without_flags(db: TranslationDB, monkeypatch) -> None:
    monkeypatch.setattr(db, "_quality_flags_ready", lambda conn=None: False)


def test_same_as_source_flag_matches_sql_condition(db, monkeypatch):
    _seed_same_as_source(db)
    assert db.verify_quality_flags() == []
    flagged = db._q(
        f"SELECT key FROM entry WHERE qflags & {quality_flags.SAME_AS_SOURCE} ORDER BY key"
    )
    assert flagged == [("k0",), ("k2",)]

    with_flags = [e.key for e in db.same_as_source_ai_entries("1.20.1")]
    count_with = db.count_same_as_source_ai("1.20.1")
    page_with = [
        r.key
        for r in db.list_entries(criteria=EntryFilter(version="1.20.1", state="same"))[
            0
        ]
    ]
    _without_flags(db, monkeypatch)
    assert [e.key for e in db.same_as_source_ai_entries("1.20.1")] == with_flags
    assert db.count_same_as_source_ai("1.20.1") == count_with == 2
    page_without = [
        r.key
        for r in db.list_entries(criteria=EntryFilter(version="1.20.1", state="same"))[
            0
        ]
    ]
    assert page_without == page_with


def test_same_as_source_flag_follows_writes(db):
    _seed_same_as_source(db)
    entry_id = db._one("SELECT id FROM entry WHERE key='k0'")[0]
    db.save_manual(entry_id, "文字 0")
    assert db.verify_quality_flags() == []
    assert db.count_same_as_source_ai("1.20.1") == 1


def test_same_as_source_query_uses_partial_index(db):
    _seed_same_as_source(db)
    seen: list[str] = []
    conn = db._conn
    conn.set_trace_callback(seen.append)
    try:
        db.same_as_source_ai_entries("1.20.1")
    finally:
        conn.set_trace_callback(None)
    select = next(s for s in seen if "FROM (SELECT e.id FROM entry e" in s)
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + select))
    assert "idx_qflags_same_as_source" in plan


def test_cleanup_candidates_are_cached_until_data_changes(db, monkeypatch):
    db.ingest(
        "1.20.1",
        [
            ScanItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "", ""),
            ScanItem(KIND_LANG, "foo", "item.foo.b", "Infused Alloy", "", ""),
        ],
    )
    calls = []
    real = cleanup_module.entry_cleanup_category

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(cleanup_module, "entry_cleanup_category", counting)
    first = db.cleanup_candidates("1.20.1")
    scanned = len(calls)
    assert scanned == 2
    assert db.cleanup_candidates("1.20.1") == first
    assert len(calls) == scanned  # 資料沒變：不重掃
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "foo", "item.foo.c", "Another", "", "")])
    db.cleanup_candidates("1.20.1")
    assert len(calls) > scanned  # 資料變了：重算


def test_activity_time_range_starts_from_history(db):
    _seed_same_as_source(db)
    entry_id = db._one("SELECT id FROM entry WHERE key='k1'")[0]
    db.save_manual(entry_id, "文字 一")
    criteria = EntryFilter(
        version="1.20.1",
        time=TimeFilter(kind="manual_activity", start_utc="2000-01-01 00:00:00"),
    )
    low, high = db.time_data_range(criteria)
    expected = db._one(
        "SELECT MIN(at), MAX(at) FROM history WHERE entry_id=? AND action='manual'",
        (entry_id,),
    )
    assert (low, high) == expected

    seen: list[str] = []
    db._conn.set_trace_callback(seen.append)
    try:
        db.time_data_range(
            EntryFilter(
                version="1.20.1",
                mod_id="foo",
                time=TimeFilter(
                    kind="manual_activity", start_utc="2000-01-01 00:00:00"
                ),
            )
        )
    finally:
        db._conn.set_trace_callback(None)
    select = next(s for s in seen if "MIN(h.at)" in s)
    plan = " ".join(r[3] for r in db._conn.execute("EXPLAIN QUERY PLAN " + select))
    assert plan.index("h ") < plan.index("e ")  # 歷史在前、條目在後
