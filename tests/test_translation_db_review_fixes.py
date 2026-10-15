"""寫入失敗回復、外部 SQL 寫入、歷史改刪與大型候選清單的快取行為。"""

from __future__ import annotations

import sqlite3

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    ScanItem,
    TranslationDB,
    entry_activity,
    quality_flags,
    repository,
)

VERSION = "1.20.1"


def _items(count: int = 12) -> list[ScanItem]:
    return [
        ScanItem(KIND_LANG, "alpha", f"key{i}", f"Text {i}", f"譯{i}" if i % 2 else "")
        for i in range(count)
    ]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "fixes.db"
    db = TranslationDB(path)
    db.ingest(VERSION, _items())
    db.close()
    return path


def _first_id(db: TranslationDB) -> int:
    return db.list_entries(VERSION, limit=1)[0][0].id


# --- 提交前的品質旗標重算失敗：交易必須回復 ---------------------------------


def test_settle_failure_rolls_back_and_allows_later_writes(db_path, monkeypatch):
    db = TranslationDB(db_path)
    entry_id = _first_id(db)
    before = db.list_entries(VERSION, limit=50)[0]

    def boom(conn):
        raise sqlite3.OperationalError("settle failed")

    monkeypatch.setattr(quality_flags, "settle", boom)
    with pytest.raises(sqlite3.OperationalError, match="settle failed"):
        db.save_manual(entry_id, "不該留下的譯文", actor="test")

    assert not db._conn.in_transaction
    assert db._write_transaction is False
    assert db.list_entries(VERSION, limit=50)[0] == before  # 未提交的資料沒有留下

    monkeypatch.undo()
    db.save_manual(
        entry_id, "之後的寫入成功", actor="test"
    )  # 不會 "within a transaction"
    assert db.list_entries(VERSION, limit=1)[0][0].zh_tw == "之後的寫入成功"
    assert db.verify_stat_summary() == []
    db.close()


def test_failure_inside_transaction_body_still_rolls_back(db_path):
    db = TranslationDB(db_path)
    with pytest.raises(RuntimeError), db._tx() as conn:
        conn.execute("DELETE FROM history")
        raise RuntimeError("body failed")
    assert not db._conn.in_transaction
    db.close()


# --- 外部 SQL 寫入沒有遞增 data_gen：持久化統計不可沿用 -------------------------


def _external(path, *statements):
    conn = sqlite3.connect(path)
    try:
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


_ENTRY_SQL = (
    "INSERT INTO entry (kind, mc_version, mod_id, key, en_us, created_at) "
    "VALUES (?,?,?,?,?,?)"
)
_EXTERNAL_ENTRY = (
    _ENTRY_SQL,
    (KIND_LANG, VERSION, "alpha", "external.key", "From outside", "2026-01-01"),
)
_HISTORY_SQL = (
    "INSERT INTO history (entry_id, action, at, batch) "
    "SELECT id, 'manual', '2026-01-01T00:00:00Z', 'ext' FROM entry LIMIT 1"
)


def test_external_insert_invalidates_persisted_stat_cache(db_path):
    db = TranslationDB(db_path)
    before = db.count_untranslated(VERSION)
    gen_before = db._data_gen()
    assert db._one("SELECT COUNT(*) FROM stat_cache")[0] > 0
    db.close()

    _external(db_path, _EXTERNAL_ENTRY)

    reopened = TranslationDB(db_path, readonly=True)
    assert reopened._data_gen() == gen_before  # 外部寫入沒有遞增世代
    assert reopened.count_untranslated(VERSION) == before + 1
    reopened.close()


def test_cache_generation_changes_for_each_external_signal(db_path):
    db = TranslationDB(db_path)
    seen = {db._cache_generation()}
    db.close()
    for statement in (
        _EXTERNAL_ENTRY,
        ("UPDATE translation SET zh_tw='外部修訂' WHERE rowid=1", ()),
        (_HISTORY_SQL, ()),
    ):
        _external(db_path, statement)
        probe = TranslationDB(db_path, readonly=True)
        generation = probe._cache_generation()
        probe.close()
        assert generation not in seen, statement[0]
        seen.add(generation)


def test_unchanged_database_keeps_the_same_generation(db_path):
    first = TranslationDB(db_path, readonly=True)
    second = TranslationDB(db_path, readonly=True)
    assert first._cache_generation() == second._cache_generation()
    first.close()
    second.close()


# --- history 單筆修改或刪除：最近人工活動必須跟著重算 ---------------------------


def _activity_db(db_path):
    db = TranslationDB(db_path)
    ids = [row.id for row in db.list_entries(VERSION, limit=5)[0]]
    for index, entry_id in enumerate(ids):
        db.save_manual(entry_id, f"人工{index}", actor="editor", propagate=False)
    return db, ids


def _last_at(db, entry_id):
    row = db._one("SELECT last_at FROM entry_activity WHERE entry_id=?", (entry_id,))
    return row[0] if row else None


def test_history_delete_recomputes_last_activity(db_path):
    db, ids = _activity_db(db_path)
    entry_id = ids[0]
    with db._tx() as conn:
        conn.execute(
            "UPDATE history SET at='2020-01-01T00:00:00Z' WHERE entry_id=?", (entry_id,)
        )
        conn.execute(
            "INSERT INTO history (entry_id, action, at, batch) "
            "VALUES (?, 'manual', '2026-06-01T00:00:00Z', 'fix')",
            (entry_id,),
        )
    assert _last_at(db, entry_id) == "2026-06-01T00:00:00Z"

    with db._tx() as conn:
        conn.execute(
            "DELETE FROM history WHERE entry_id=? AND at='2026-06-01T00:00:00Z'",
            (entry_id,),
        )
    assert _last_at(db, entry_id) == "2020-01-01T00:00:00Z"

    with db._tx() as conn:
        conn.execute("DELETE FROM history WHERE entry_id=?", (entry_id,))
    assert _last_at(db, entry_id) is None  # 沒有任何人工紀錄：整列移除
    assert entry_activity.verify(db._conn) == []
    db.close()


def test_history_update_recomputes_last_activity(db_path):
    db, ids = _activity_db(db_path)
    first, second = ids[0], ids[1]
    with db._tx() as conn:
        conn.execute(
            "UPDATE history SET at='2030-01-01T00:00:00Z' WHERE entry_id=?", (first,)
        )
    assert _last_at(db, first) == "2030-01-01T00:00:00Z"

    with db._tx() as conn:  # 改成不列入人工活動的動作
        conn.execute("UPDATE history SET action='scan' WHERE entry_id=?", (first,))
    assert _last_at(db, first) is None

    with db._tx() as conn:  # 搬到另一個條目
        conn.execute(
            "UPDATE history SET action='manual', entry_id=? WHERE entry_id=? "
            "AND action='scan'",
            (second, first),
        )
        conn.execute(
            "UPDATE history SET at='2031-01-01T00:00:00Z' WHERE entry_id=?", (second,)
        )
    assert _last_at(db, second) == "2031-01-01T00:00:00Z"
    assert _last_at(db, first) is None
    assert entry_activity.verify(db._conn) == []
    db.close()


def test_history_delete_by_entry_cascade_leaves_no_orphan_rows(db_path):
    db, ids = _activity_db(db_path)
    with db._tx() as conn:
        conn.execute("DELETE FROM entry WHERE id=?", (ids[0],))
    assert _last_at(db, ids[0]) is None
    assert entry_activity.verify(db._conn) == []
    db.close()


def test_history_edit_keeps_sort_equal_to_old_query(db_path):
    db, ids = _activity_db(db_path)
    with db._tx() as conn:
        conn.execute("DELETE FROM history WHERE entry_id=?", (ids[2],))
    criteria = EntryFilter(version=VERSION, sort_by="manual_activity_newest")
    fast = [row.id for row in db.list_entries(criteria=criteria, limit=50)[0]]
    with db._tx() as conn:
        conn.execute("UPDATE meta SET value='0' WHERE key='activity_valid'")
    slow = [row.id for row in db.list_entries(criteria=criteria, limit=50)[0]]
    db.close()
    assert fast == slow


def test_activity_history_triggers_are_installed(db_path):
    conn = sqlite3.connect(db_path)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='trigger' AND name LIKE 'activity%'"
        )
    }
    conn.close()
    assert {"activity_history_delete", "activity_history_update"} <= names


# --- 大型候選清單：不白做 JSON 序列化 ----------------------------------------


def test_oversized_list_result_skips_serialization(db_path, monkeypatch):
    db = TranslationDB(db_path)
    calls = []
    real_dumps = repository.json.dumps

    def spy(value, *args, **kwargs):
        calls.append(len(value) if isinstance(value, list) else None)
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(repository.json, "dumps", spy)
    big = [[i] for i in range(repository._MAX_CACHED_ROWS + 1)]
    assert db._cached("big-list", lambda: big, persist=False) is big
    assert calls == []  # 超過上限：沒有序列化

    small = [[i] for i in range(10)]
    assert db._cached("small-list", lambda: small, persist=False) == small
    assert calls == [10]

    runs = []

    def compute():
        runs.append(1)
        return small

    db._cached("small-again", compute, persist=False)
    db._cached("small-again", compute, persist=False)
    assert runs == [1]  # 小結果仍可被快取重用
    db.close()
