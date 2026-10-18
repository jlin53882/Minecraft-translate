"""Correctness contracts for narrow paging and cross-connection read caching."""

import sqlite3
from dataclasses import replace

import pytest

from translation_tool.translation_db import (
    EntryFilter,
    ScanItem,
    TranslationDB,
    repository,
)
from translation_tool.translation_db.models import TimeFilter
from translation_tool.translation_db.read_cache import ReadCache


def seed(path):
    db = TranslationDB(path)
    for version in ("1.20.1", "1.21.1"):
        db.ingest(
            version,
            [
                ScanItem("lang", "m", f"key{i:03}", f"Text {i}", "譯文")
                for i in range(55)
            ],
        )
    db.save_manual(1, "人工", propagate=False)
    return db


def test_readonly_statistics_reused_across_connections_and_invalidated(tmp_path):
    path = tmp_path / "db.sqlite"
    writer = seed(path)
    try:
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM stat_cache")
        first = TranslationDB(path, readonly=True)
        initial = first._cached(
            "test:payload", lambda: {"entries": first.count_entries()}
        )
        first.close()
        second = TranslationDB(path, readonly=True)
        try:

            def unexpected():
                pytest.fail(
                    "A fresh read-only worker recalculated an unchanged snapshot"
                )

            result = second._cached("test:payload", unexpected)
            assert result == initial
            result["entries"] = -1
            assert second._cached("test:payload", unexpected) == initial
            writer.ingest("1.21.1", [ScanItem("lang", "m", "new", "New")])
            assert second._cached(
                "test:payload", lambda: {"entries": second.count_entries()}
            ) == {"entries": 111}
        finally:
            second.close()
    finally:
        writer.close()


def test_cached_values_are_not_published_from_uncommitted_writes(tmp_path):
    db = seed(tmp_path / "db.sqlite")
    try:
        assert db._cached("test:transaction", lambda: 1) == 1
        with pytest.raises(ValueError), db._tx():
            assert db._cached("test:transaction", lambda: 2) == 2
            raise ValueError("rollback")
        assert db._cached("test:transaction", lambda: 1) == 1
    finally:
        db.close()


def test_read_cache_detects_external_writes_without_generation_bump(tmp_path):
    path = tmp_path / "db.sqlite"
    db = seed(path)
    reader = TranslationDB(path, readonly=True)
    try:
        assert reader._cached("test:external", lambda: reader.count_entries()) == 110
        with sqlite3.connect(path) as external:
            external.execute(
                "INSERT INTO entry(kind, mc_version, mod_id, key, en_us) "
                "VALUES ('lang','new','m','external','External')"
            )
        assert reader._cached("test:external", lambda: reader.count_entries()) == 111
    finally:
        reader.close()
        db.close()


@pytest.mark.parametrize(
    "sort",
    [
        "default",
        "default_desc",
        "entry_newest",
        "entry_oldest",
        "effective_updated_newest",
        "effective_updated_oldest",
        "manual_activity_newest",
        "manual_activity_oldest",
    ],
)
@pytest.mark.parametrize(
    "state", ["all", "none", "diff", "changed", "manual", "ok", "same"]
)
def test_paged_public_rows_equal_unbounded_reference(tmp_path, sort, state):
    db = seed(tmp_path / "db.sqlite")
    try:
        criteria = EntryFilter(version="1.20.1", sort_by=sort, state=state)
        expected = db._query_entry_rows(criteria)
        reader = TranslationDB(db.path, readonly=True)
        try:
            for target in (db, reader):
                for offset in (0, 10, 50, 60):
                    rows, total = target.list_entries(
                        criteria=criteria, limit=10, offset=offset
                    )
                    assert total == len(expected)
                    assert rows == [
                        db._row(row) for row in expected[offset : offset + 10]
                    ]
        finally:
            reader.close()
        timed = replace(
            criteria,
            time=TimeFilter(kind="translation_created", unknown_policy="exclude"),
        )
        expected = db._query_entry_rows(timed)
        rows, total = db.list_entries(criteria=timed, limit=10)
        assert total == len(expected)
        assert rows == [db._row(row) for row in expected[:10]]
    finally:
        db.close()


def test_writable_reopen_does_not_rebuild_unchanged_review_trigger(tmp_path):
    path = tmp_path / "db.sqlite"
    db = seed(path)
    before = db._conn.execute("PRAGMA schema_version").fetchone()[0]
    db.close()
    reopened = TranslationDB(path)
    try:
        assert reopened._conn.execute("PRAGMA schema_version").fetchone()[0] == before
    finally:
        reopened.close()


def test_shared_cache_is_bounded_by_utf8_bytes_and_lru_entries():
    cache = ReadCache(max_entries=2, max_bytes=9)
    cache.put((1,), "中文")  # Six bytes, not two.
    cache.put((2,), "abc")
    assert cache.get((1,)) == "中文"  # Promote first entry.
    cache.put((3,), "def")
    assert cache.get((2,)) is None
    assert cache.get((1,)) == "中文"
    cache.put((4,), "oversized value")
    assert cache.get((4,)) is None
    cache.put((1,), "a")  # Replacement releases the old byte budget.
    cache.put((5,), "b")
    assert cache.get((3,)) is None
    assert cache.get((1,)) == "a"
    assert cache.get((5,)) == "b"


def test_file_replacement_with_same_generation_does_not_reuse_results(tmp_path):
    path = tmp_path / "db.sqlite"
    replacement = tmp_path / "replacement.sqlite"
    first = seed(path)
    other = seed(replacement)
    try:
        with other._tx():
            other._conn.execute("DELETE FROM entry WHERE id=1")
            other._conn.execute(
                "UPDATE meta SET value=? WHERE key='data_gen'",
                (str(int(first._data_gen()) - 1),),
            )
        assert first._data_gen() == other._data_gen()
        assert first._cached("test:replace", first.count_entries) == 110
    finally:
        first.close()
        other.close()
    replacement.replace(path)
    reopened = TranslationDB(path, readonly=True)
    try:
        assert reopened._cached("test:replace", reopened.count_entries) == 109
    finally:
        reopened.close()


def test_streaming_quality_page_reused_and_invalidated(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "_QUALITY_PAGE_BUFFER_LIMIT", 3)
    db = seed(tmp_path / "db.sqlite")
    reader = TranslationDB(db.path, readonly=True)
    # 逐列掃描的翻頁快取：品質旗標不可用（舊資料庫、有待重算條目）時走這條路
    monkeypatch.setattr(reader, "_quality_flags_ready", lambda conn=None: False)
    criteria = EntryFilter(
        version="1.20.1", quality=repository.QualityFilter(status="consistent")
    )
    try:
        expected, total = reader.list_entries(criteria=criteria, limit=5, offset=10)
        assert total == 55
        original_scan = reader._scan_quality_candidates
        calls = []

        def counted(*args):
            calls.append(True)
            return original_scan(*args)

        monkeypatch.setattr(reader, "_scan_quality_candidates", counted)
        assert reader.list_entries(criteria=criteria, limit=5, offset=10) == (
            expected,
            total,
        )
        assert calls == []
        db.ingest("1.20.1", [ScanItem("lang", "m", "new", "New", "新")])
        _, total = reader.list_entries(criteria=criteria, limit=5, offset=10)
        assert total == 56
        assert calls == [True]
    finally:
        reader.close()
        db.close()


def test_candidate_lists_stay_memory_only_and_refresh_after_write(tmp_path):
    db = TranslationDB(tmp_path / "db.sqlite")
    try:
        db.ingest("v", [ScanItem("lang", "m", "empty", "Missing")])
        expected = db.untranslated_entries("v", exclude_reusable=True)
        assert isinstance(expected[0], tuple)
        expected.clear()
        assert len(db.untranslated_entries("v", exclude_reusable=True)) == 1
        assert db._one("SELECT COUNT(*) FROM stat_cache")[0] == 0
        db.save_manual(1, "已補齊", propagate=False)
        assert db.untranslated_entries("v", exclude_reusable=True) == []
    finally:
        db.close()
