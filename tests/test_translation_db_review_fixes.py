"""寫入失敗回復、外部 SQL 寫入、歷史改刪與大型候選清單的快取行為。"""

from __future__ import annotations

import sqlite3

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    QualityFilter,
    ScanItem,
    TranslationDB,
    change_tracker,
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
_MOVE_SQL = (
    "UPDATE entry SET mc_version='1.21.1' WHERE id="
    "(SELECT MIN(id) FROM entry WHERE mc_version='1.20.1')"
)
_SRC_CHANGE_SQL = (
    "INSERT INTO src_change (kind, mc_version, mod_id, key, old_en, new_en, "
    "detected_at) VALUES ('lang', '1.20.1', 'alpha', 'k', 'a', 'b', '2026-01-01')"
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


# --- 外部 SQL 的其他寫法：總數不變的搬移、src_change ---------------------------


def test_external_version_move_with_equal_totals_changes_generation(db_path):
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "beta", f"other{i}", f"Other {i}") for i in range(2)],
    )
    before_stats = [(s.mc_version, s.total) for s in db.version_stats()]
    before_gen = db._cache_generation()
    db.close()

    # 兩個版本各有條目；把 1.20.1 的一筆搬到 1.21.1，全庫筆數與各項總和都不變
    _external(db_path, (_MOVE_SQL, ()))
    reopened = TranslationDB(db_path, readonly=True)
    assert reopened._cache_generation() != before_gen
    after_stats = [(s.mc_version, s.total) for s in reopened.version_stats()]
    assert after_stats != before_stats
    reopened.close()


def test_external_src_change_insert_changes_generation_and_overview(db_path):
    db = TranslationDB(db_path)
    first = db.overview()["src_changed"]
    before_gen = db._cache_generation()
    db.close()
    _external(db_path, (_SRC_CHANGE_SQL, ()))
    reopened = TranslationDB(db_path, readonly=True)
    assert reopened._cache_generation() != before_gen
    assert reopened.overview()["src_changed"] == first + 1
    reopened.close()


def test_own_writes_do_not_bump_external_sequence(db_path):
    db = TranslationDB(db_path)
    seq = db._one("SELECT value FROM meta WHERE key='ext_change_seq'")[0]
    db.ingest(VERSION, _items(20))
    db.save_manual(_first_id(db), "自己的寫入", actor="test")
    with db._tx() as conn:
        conn.execute("UPDATE entry SET en_us=en_us || '!' WHERE id=1")
    assert db._one("SELECT value FROM meta WHERE key='ext_change_seq'")[0] == seq
    assert db._one("SELECT 1 FROM meta WHERE key='tx_active'") is None
    db.close()


def test_failed_transaction_leaves_no_tx_marker(db_path):
    db = TranslationDB(db_path)
    with pytest.raises(RuntimeError), db._tx():
        raise RuntimeError("fail")
    assert db._one("SELECT 1 FROM meta WHERE key='tx_active'") is None
    db.close()


def test_change_tracker_triggers_are_installed_and_idempotent(db_path):
    conn = sqlite3.connect(db_path)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger' AND name GLOB 'chg_*'"
        )
    }
    assert {"chg_entry_insert", "chg_history_delete", "chg_src_change_update"} <= names
    assert change_tracker.is_installed(conn)
    conn.close()


# --- 大型結果：不白做 JSON 序列化（含巢狀結果與超長文字）-------------------------


def _spy_dumps(monkeypatch):
    calls = []
    real_dumps = repository.json.dumps

    def spy(value, *args, **kwargs):
        calls.append(1)
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(repository.json, "dumps", spy)
    return calls


def test_oversized_nested_tuple_result_skips_serialization(db_path, monkeypatch):
    db = TranslationDB(db_path)
    calls = _spy_dumps(monkeypatch)
    monkeypatch.setattr(repository.READ_CACHE, "_max_bytes", 50_000)
    # 與 mismatched_translation_entries 相同的 (筆數, 候選清單) 形狀
    big = (3000, [[i, "x" * 30, "譯" * 10] for i in range(3000)])
    assert db._cached("nested", lambda: big, persist=False) is big
    assert calls == []
    db.close()


def test_few_rows_with_huge_text_skip_serialization(db_path, monkeypatch):
    db = TranslationDB(db_path)
    calls = _spy_dumps(monkeypatch)
    monkeypatch.setattr(repository.READ_CACHE, "_max_bytes", 50_000)
    long_text = [[1, "譯" * 40_000]]  # 一列、但內容超過上限
    assert db._cached("long-text", lambda: long_text, persist=False) is long_text
    assert calls == []
    db.close()


def test_small_results_are_still_cached_once(db_path, monkeypatch):
    db = TranslationDB(db_path)
    calls = _spy_dumps(monkeypatch)
    runs = []

    def compute():
        runs.append(1)
        return (2, [[1, "a"], [2, "b"]])

    db._cached("small", compute, persist=False)
    db._cached("small", compute, persist=False)
    assert runs == [1]
    assert calls == [1]
    db.close()


def test_exceeds_capacity_is_cheaper_than_and_consistent_with_serialization():
    cache = repository.READ_CACHE.__class__(max_entries=4, max_bytes=10_000)
    rows = [[i, "text " * 5] for i in range(500)]
    assert cache.exceeds_capacity(rows) is True  # 序列化後確實超過
    assert len(repository.json.dumps(rows)) > 10_000
    small = [[i, "t"] for i in range(10)]
    assert cache.exceeds_capacity(small) is False


@pytest.mark.parametrize("marker", ["tx_active", "keep_review_on_text_change"])
def test_marker_init_failure_rolls_back_and_allows_later_writes(db_path, marker):
    db = TranslationDB(db_path)
    entry_id = _first_id(db)
    db._conn.execute(
        "CREATE TEMP TRIGGER refuse_marker BEFORE INSERT ON meta "
        f"WHEN NEW.key = '{marker}' BEGIN SELECT RAISE(ABORT, 'marker refused'); END"
    )

    with (
        pytest.raises(sqlite3.DatabaseError, match="marker refused"),
        db._tx(keep_review=(marker != "tx_active")),
    ):
        pass

    assert not db._conn.in_transaction
    assert db._write_transaction is False

    db._conn.execute("DROP TRIGGER refuse_marker")
    db.save_manual(
        entry_id, "故障排除後寫入成功", actor="test"
    )  # 不會 "within a transaction"
    assert db.list_entries(VERSION, limit=1)[0][0].zh_tw == "故障排除後寫入成功"
    assert (
        db._conn.execute(
            "SELECT COUNT(*) FROM meta WHERE key IN ('tx_active','keep_review_on_text_change')"
        ).fetchone()[0]
        == 0
    )
    db.close()


# --- 衍生索引不見時，讀取端不走快路徑 ------------------------------------------


@pytest.mark.parametrize(
    ("index", "ready"),
    [
        ("idx_act_ver_at", "_activity_ready"),
        ("idx_eff_ver_upd", "_effective_columns_ready"),
        ("idx_qflags_mismatch", "_quality_flags_ready"),
    ],
)
def test_missing_derived_index_disables_fast_path_and_results_match(
    db_path, index, ready
):
    db = TranslationDB(db_path)
    criteria = [
        EntryFilter(version=VERSION, sort_by="manual_activity_newest"),
        EntryFilter(version=VERSION, sort_by="effective_updated_newest"),
        EntryFilter(version=VERSION, quality=QualityFilter(status="mismatch")),
    ]
    with_index = [db.list_entries(criteria=c, limit=50)[0] for c in criteria]
    assert getattr(db, ready)()

    db._conn.execute(f"DROP INDEX {index}")
    assert not getattr(db, ready)()
    without_index = [db.list_entries(criteria=c, limit=50)[0] for c in criteria]
    assert without_index == with_index  # 退回原本的查詢，結果相同
    db.close()


def test_entry_activity_rebuild_replaces_same_name_index_with_wrong_definition(db_path):

    db = TranslationDB(db_path)
    db._conn.execute("DROP INDEX idx_act_ver_at")
    db._conn.execute("CREATE INDEX idx_act_ver_at ON entry_activity (entry_id)")
    assert not entry_activity.is_installed(db._conn)
    entry_activity.install(db._conn)
    assert entry_activity.is_installed(db._conn)
    assert entry_activity.is_ready(db._conn)
    db.close()


# --- 外部 SQL 改變生效列／譯文列的身分：衍生資料不可靜默失準 ------------------

_MOVE_EFFECTIVE_SQL = (
    "UPDATE effective SET entry_id = (SELECT id FROM entry WHERE mc_version = '1.21.1' "
    "ORDER BY id LIMIT 1) WHERE entry_id = (SELECT entry_id FROM effective "
    "ORDER BY entry_id LIMIT 1)"
)
_RETARGET_TRANSLATION_SQL = (
    "UPDATE translation SET source = 3 WHERE id = (SELECT MIN(id) FROM translation)"
)


@pytest.fixture
def two_version_path(tmp_path):
    path = tmp_path / "moved.db"
    db = TranslationDB(path)
    db.ingest(VERSION, _items())
    db.ingest("1.21.1", _items(6)[:3])  # 另一個版本：部分條目沒有譯文
    db.close()
    return path


def _external_sql(path, sql: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute(sql)
    connection.commit()
    connection.close()


def test_external_move_of_effective_row_disables_derived_reads_until_rebuilt(
    two_version_path,
):
    db = TranslationDB(two_version_path)
    generation = db._cache_generation()
    db.close()
    _external_sql(two_version_path, _MOVE_EFFECTIVE_SQL)

    reader = TranslationDB(two_version_path, readonly=True)
    assert reader._cache_generation() != generation
    assert not reader._stat_summary_ready()
    assert not reader._effective_columns_ready()
    # 退回原本的查詢，結果與全庫重算相同
    assert reader.overview() == reader._overview_full()
    reader.close()

    reopened = TranslationDB(two_version_path)  # 可寫開啟時重建
    assert reopened._stat_summary_ready()
    assert reopened._effective_columns_ready()
    assert reopened.verify_stat_summary() == []
    assert reopened.verify_effective_columns() == []
    reopened.close()


def test_external_translation_source_change_disables_effective_columns(
    two_version_path,
):
    _external_sql(two_version_path, _RETARGET_TRANSLATION_SQL)
    reader = TranslationDB(two_version_path, readonly=True)
    assert not reader._effective_columns_ready()
    reader.close()
    reopened = TranslationDB(two_version_path)
    assert reopened._effective_columns_ready()
    assert reopened.verify_effective_columns() == []
    reopened.close()


def test_own_writes_never_disable_derived_data(two_version_path):
    db = TranslationDB(two_version_path)
    db.save_manual(_first_id(db), "自己的寫入", actor="test")
    db.ingest(VERSION, _items(14))
    assert db._stat_summary_ready()
    assert db._effective_columns_ready()
    db.close()


def test_cache_capacity_estimate_counts_json_escapes():
    import json

    from translation_tool.translation_db.read_cache import ReadCache

    cache = ReadCache(max_bytes=16_000_000)
    for payload in ("\x00" * 3_000_000, '"' * 9_000_000, "x" * 3_000_000):
        actual = len(json.dumps([payload], ensure_ascii=False).encode("utf-8"))
        assert cache.exceeds_capacity([payload]) == (actual > 16_000_000), len(payload)


def test_cache_capacity_estimate_counts_json_separators():
    import json

    from translation_tool.translation_db.read_cache import ReadCache

    for value in (["a"] * 21, {f"k{i}": "v" for i in range(10)}):
        actual = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        assert ReadCache(max_bytes=actual - 1).exceeds_capacity(value)


def test_old_derived_data_definition_is_not_ready_before_a_writable_open(
    two_version_path,
):
    """舊版安裝的衍生資料（沒有新觸發器）在唯讀開啟時不能被當成可用。"""
    db = TranslationDB(two_version_path)
    db._conn.execute(
        "UPDATE meta SET value = '1' WHERE key IN "
        "('stats_schema_version', 'effective_columns_schema_version')"
    )
    db._conn.commit()
    db.close()
    reader = TranslationDB(two_version_path, readonly=True)
    assert not reader._stat_summary_ready()
    assert not reader._effective_columns_ready()
    reader.close()
    reopened = TranslationDB(two_version_path)
    assert reopened._stat_summary_ready()
    assert reopened._effective_columns_ready()
    assert reopened.verify_stat_summary() == []
    reopened.close()


def test_cache_capacity_estimate_counts_big_integers():
    import json

    from translation_tool.translation_db.read_cache import ReadCache

    value = 10**20
    actual = len(json.dumps(value).encode("utf-8"))
    assert ReadCache(max_bytes=actual - 1).exceeds_capacity(value)


def test_activity_installed_with_old_version_is_not_ready_read_only(two_version_path):
    db = TranslationDB(two_version_path)
    db._conn.execute("UPDATE meta SET value='1' WHERE key='activity_schema_version'")
    db._conn.commit()
    db.close()
    reader = TranslationDB(two_version_path, readonly=True)
    assert not reader._activity_ready()
    reader.close()
    reopened = TranslationDB(two_version_path)
    assert reopened._activity_ready()
    assert reopened.verify_entry_activity() == []
    reopened.close()
