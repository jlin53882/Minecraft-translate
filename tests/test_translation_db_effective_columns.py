"""effective 衍生欄位（mc_version／diff／upd）：與原本定義一致、狀態篩選與排序逐頁相同。

各寫入流程（掃描、手動儲存、審核、批次取代、清理與還原、優先序重建、寫回、修復同步）
後的比對在 ``test_translation_db_stat_summary.py`` 的共用檢查中一併執行。
"""

from __future__ import annotations

import itertools
import sqlite3

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    ScanItem,
    TranslationDB,
    effective_columns,
)
from translation_tool.translation_db.schema import (
    DEFAULT_PRIORITY,
    SRC_AI,
    SRC_JAR_TW,
    SRC_MANUAL,
)

VERSIONS = ("1.20.1", "1.21.1", "1.22")
STATES = ("all", "ok", "diff", "manual", "none", "same", "changed")
SORTS = ("default", "effective_updated_newest", "manual_activity_newest")


def _seed(db: TranslationDB) -> None:
    """三個版本、同內容譯文有同有異，含人工、未翻譯、與原文相同與原文變動的條目。"""
    for index, version in enumerate(VERSIONS):
        items = []
        for i in range(30):
            text = f"譯{i}" if i % 4 else f"譯{i}-{index}"  # 每 4 筆有一筆跨版本不同
            items.append(ScanItem(KIND_LANG, f"mod{i % 3}", f"k{i}", f"Text {i}", text))
        items += [
            ScanItem(KIND_LANG, "mod9", f"blank{i}", f"Blank {i}") for i in range(4)
        ]
        items.append(ScanItem(KIND_LANG, "mod9", "same", "Same", "Same"))
        items.append(
            ScanItem(KIND_LANG, "mod9", "nosrc", "", "沒有原文", source=SRC_AI)
        )
        db.ingest(version, items)
    rows = db.list_entries("1.21.1", limit=500)[0]
    for row in rows[:6]:
        db.save_manual(row.id, f"人工{row.id}", actor="editor", propagate=False)
    db.review_manual(rows[0].id, expected_zh_tw=f"人工{rows[0].id}", actor="checker")
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "mod0", "k0", "Changed text", "改")])
    # 不同的更新時間，讓「最近更新」排序有先後也有同時間
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET updated_at='2030-01-0' || (id % 3 + 1) "
            "WHERE id % 5 = 0"
        )


def _ids(db, criteria, offset, limit, *, derived=True):
    saved = db._effective_columns_ready, db._activity_ready
    if not derived:
        db._effective_columns_ready = lambda: False
        db._activity_ready = lambda: False
    try:
        rows, total = db.list_entries(criteria=criteria, limit=limit, offset=offset)
    finally:
        db._effective_columns_ready, db._activity_ready = saved
    return [(row.id, row.diff) for row in rows], total


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "effective.db")
    yield d
    d.close()


def test_new_database_installs_ready_columns(db):
    assert effective_columns.is_installed(db._conn)
    assert db._effective_columns_ready()
    _seed(db)
    assert db.verify_effective_columns() == []
    assert db._one("SELECT COUNT(*) FROM effective WHERE diff=1")[0] > 0
    assert db._one("SELECT COUNT(*) FROM effective WHERE upd IS NULL")[0] == 0


@pytest.mark.parametrize(
    ("version", "state", "sort_by"), list(itertools.product(VERSIONS, STATES, SORTS))
)
def test_states_and_sorts_match_original_queries(db, version, state, sort_by):
    _seed(db)
    for extra in ({}, {"mod_id": "mod1"}, {"source": SRC_MANUAL}, {"query": "1"}):
        criteria = EntryFilter(version=version, state=state, sort_by=sort_by, **extra)
        total = _ids(db, criteria, 0, 1, derived=False)[1]
        for offset in sorted({0, 3, total // 2, max(0, total - 2), total + 1}):
            assert _ids(db, criteria, offset, 4) == _ids(
                db, criteria, offset, 4, derived=False
            ), (extra, offset)
        assert _ids(db, criteria, 0, 500) == _ids(
            db, criteria, 0, 500, derived=False
        ), extra


def test_list_queries_read_indexes_without_sorting(db):
    _seed(db)
    for state, extra in itertools.product(
        ("all", "ok", "diff", "manual", "none"),
        ({}, {"mod_id": "mod1"}, {"source": SRC_JAR_TW}),
    ):
        criteria = EntryFilter(version="1.21.1", state=state, **extra)
        cond, params = db._filter_sql(criteria)
        sqls = [db._count_sql(criteria, cond)]
        for sort_by in ("effective_updated_newest", "manual_activity_newest"):
            dated, _count, undated = db._two_phase_sort_sql(sort_by, cond)
            sqls += [dated, undated]
        for sql in sqls:
            values = (*params, 10, 0) if "LIMIT" in sql else params
            plan = " ".join(r[3] for r in db._q(f"EXPLAIN QUERY PLAN {sql}", values))
            assert "TEMP B-TREE" not in plan, (state, extra, sql, plan)
    criteria = EntryFilter(version="1.21.1", state="ok")
    cond, _params = db._filter_sql(criteria)
    assert "EXISTS" not in cond
    assert db._count_sql(criteria, cond).startswith("SELECT COUNT(*) FROM effective f")


def _diff_of(db, version, key, mod):
    return db._one(
        "SELECT f.diff FROM effective f JOIN entry e ON e.id=f.entry_id "
        "WHERE e.mc_version=? AND e.key=? AND e.mod_id=?",
        (version, key, mod),
    )[0]


def test_entry_delete_and_content_changes_recompute_peers(db):
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "m", "k", "Gold", "金")])
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "m", "k", "Gold", "黃金")])
    db.ingest("1.22", [ScanItem(KIND_LANG, "m", "k", "", "黃金")])
    assert _diff_of(db, "1.20.1", "k", "m") == 1
    assert db.verify_effective_columns() == []
    # 原文補上：加入同內容組
    with db._tx() as conn:
        conn.execute(
            "UPDATE entry SET en_us='Gold' WHERE mc_version='1.22' AND key='k'"
        )
    assert db.verify_effective_columns() == []
    assert _diff_of(db, "1.22", "k", "m") == 1
    # 原文改變：離開舊組、加入新組
    with db._tx() as conn:
        conn.execute(
            "UPDATE entry SET en_us='Golden' WHERE mc_version='1.20.1' AND key='k'"
        )
    assert db.verify_effective_columns() == []
    assert _diff_of(db, "1.20.1", "k", "m") == 0
    assert _diff_of(db, "1.21.1", "k", "m") == 0
    with db._tx() as conn:
        conn.execute(
            "UPDATE entry SET en_us='Gold' WHERE mc_version='1.20.1' AND key='k'"
        )
    assert _diff_of(db, "1.21.1", "k", "m") == 1
    # 條目刪除（串聯刪除生效列）：剩下的同內容條目一致了
    with db._tx() as conn:
        conn.execute("DELETE FROM entry WHERE mc_version='1.20.1' AND key='k'")
    assert db.verify_effective_columns() == []
    assert _diff_of(db, "1.21.1", "k", "m") == 0
    # 換模組、換版本
    with db._tx() as conn:
        conn.execute("UPDATE entry SET mc_version='2.0' WHERE mc_version='1.22'")
    assert db.verify_effective_columns() == []
    assert db._one("SELECT COUNT(*) FROM effective WHERE mc_version='2.0'")[0] == 1


def test_translation_time_changes_follow_effective_source(db):
    _seed(db)
    row = db.list_entries("1.20.1", limit=1)[0][0]
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET updated_at='2040-01-01' WHERE entry_id=?",
            (row.id,),
        )
    assert db._one("SELECT upd FROM effective WHERE entry_id=?", (row.id,)) == (
        "2040-01-01",
    )
    with db._tx() as conn:
        conn.execute("DELETE FROM translation WHERE entry_id=?", (row.id,))
    assert db.verify_effective_columns() == []
    assert db._one("SELECT upd FROM effective WHERE entry_id=?", (row.id,)) == (None,)


def _exercise_triggers(db) -> list[list[tuple]]:
    """逐步做會觸發每個觸發器的直接寫入，回傳每一步之後的驗證結果。"""

    def ids(key):
        return [
            db._one(
                "SELECT e.id FROM entry e JOIN effective f ON f.entry_id=e.id "
                "WHERE e.mc_version=? AND e.key=?",
                (version, key),
            )[0]
            for version in VERSIONS
        ]

    a, b, c = ids("k3")
    moved = ids("k5")[2]  # k5 各版本譯文相同：先改成不同、再移出同內容組
    steps = [
        ("UPDATE effective SET zh_tw='不同' WHERE entry_id=?", (a,)),
        ("UPDATE effective SET source=? WHERE entry_id=?", (SRC_MANUAL, b)),
        ("DELETE FROM effective WHERE entry_id=?", (a,)),
        (
            "INSERT INTO effective (entry_id, zh_tw, source) VALUES (?, '新', ?)",
            (a, SRC_JAR_TW),
        ),
        ("UPDATE entry SET en_us='Other' WHERE id=?", (c,)),
        ("UPDATE effective SET zh_tw='只有我不同' WHERE entry_id=?", (moved,)),
        ("UPDATE entry SET en_us='Moved away' WHERE id=?", (moved,)),
        ("DELETE FROM entry WHERE id=?", (a,)),
        (
            "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?, ?, '人')",
            (b, SRC_MANUAL),
        ),
        ("UPDATE translation SET updated_at='2050-01-01' WHERE entry_id=?", (c,)),
        ("DELETE FROM translation WHERE entry_id=?", (b,)),
    ]
    results = []
    for sql, params in steps:
        with db._tx() as conn:
            conn.execute(sql, params)
        results.append(db.verify_effective_columns())
    return results


def test_every_write_trigger_keeps_columns_exact(db):
    _seed(db)
    assert all(result == [] for result in _exercise_triggers(db))


@pytest.mark.parametrize("trigger", sorted(effective_columns._trigger_sql()))
def test_each_trigger_is_needed(db, trigger):
    _seed(db)
    with db._tx() as conn:
        conn.execute(f"DROP TRIGGER {trigger}")
    assert any(result != [] for result in _exercise_triggers(db)), trigger


def test_priority_rebuild_freezes_and_recomputes(db):
    _seed(db)
    statements: list[str] = []
    db._conn.set_trace_callback(statements.append)
    try:
        db.set_priority((SRC_JAR_TW, *[s for s in DEFAULT_PRIORITY if s != SRC_JAR_TW]))
    finally:
        db._conn.set_trace_callback(None)
    assert any("effective_columns_frozen" in sql for sql in statements)
    # 暫停期間觸發器本體不執行
    assert not any("TRIGGER effcol" in sql for sql in statements)
    assert db._one("SELECT 1 FROM meta WHERE key='effective_columns_frozen'") is None
    assert db.verify_effective_columns() == []
    # 重建結果與清空後全部重新插入相同
    before = db._q("SELECT * FROM effective ORDER BY entry_id")
    with db._tx() as conn:
        conn.execute("DELETE FROM effective")
        db._rebuild_effective(conn)
    assert db._q("SELECT * FROM effective ORDER BY entry_id") == before


def test_frozen_block_recomputes_and_rollback_clears_flag(db):
    _seed(db)
    with db._tx() as conn, effective_columns.frozen(conn):
        assert not db._effective_columns_ready()
        conn.execute("UPDATE effective SET zh_tw='全部相同'")
        # 暫停期間讀取端退回原本的查詢，結果仍正確
        assert db.list_entries("1.21.1", state="diff")[1] == 0
    assert db.verify_effective_columns() == []
    assert db._one("SELECT COUNT(*) FROM effective WHERE diff=1")[0] == 0
    with (
        pytest.raises(RuntimeError),
        db._tx() as conn,
        effective_columns.frozen(conn),
    ):
        conn.execute("DELETE FROM effective")
        raise RuntimeError("boom")
    assert db._one("SELECT 1 FROM meta WHERE key='effective_columns_frozen'") is None
    assert db.verify_effective_columns() == []


def _drop_columns(path):
    with sqlite3.connect(path) as conn:
        for name in effective_columns._trigger_sql():
            conn.execute(f"DROP TRIGGER {name}")
        for name in effective_columns._INDEXES:
            conn.execute(f"DROP INDEX {name}")
        for column in effective_columns.COLUMNS:
            conn.execute(f"ALTER TABLE effective DROP COLUMN {column}")
        conn.execute("DELETE FROM meta WHERE key LIKE 'effective_columns%'")


def _all_pages(db):
    return {
        (state, sort_by): _ids(
            db, EntryFilter(version="1.21.1", state=state, sort_by=sort_by), 0, 500
        )
        for state, sort_by in itertools.product(STATES, SORTS)
    }


def test_readonly_old_database_falls_back_and_writable_open_installs(tmp_path):
    path = tmp_path / "old.db"
    writer = TranslationDB(path)
    _seed(writer)
    expected = _all_pages(writer)
    effective_before = writer._q(
        "SELECT entry_id, zh_tw, source, checker, review_status FROM effective"
    )
    writer.close()
    _drop_columns(path)
    reader = TranslationDB(path, readonly=True)
    try:
        assert not reader._effective_columns_ready()
        assert _all_pages(reader) == expected
    finally:
        reader.close()
    migrated = TranslationDB(path)
    try:
        assert migrated._effective_columns_ready()
        assert migrated.verify_effective_columns() == []
        assert _all_pages(migrated) == expected
        assert (
            migrated._q(
                "SELECT entry_id, zh_tw, source, checker, review_status FROM effective"
            )
            == effective_before
        )
    finally:
        migrated.close()


def test_invalid_flag_falls_back_until_rebuilt(db):
    _seed(db)
    expected = _all_pages(db)
    with db._tx() as conn:
        conn.execute("UPDATE effective SET diff=1 - diff, upd=NULL")
        conn.execute("UPDATE meta SET value='0' WHERE key='effective_columns_valid'")
    assert not db._effective_columns_ready()
    assert _all_pages(db) == expected
    assert db.verify_effective_columns()
    db.rebuild_effective_columns()
    assert db._effective_columns_ready() and db.verify_effective_columns() == []
    assert _all_pages(db) == expected


def test_outdated_trigger_definition_is_reinstalled(tmp_path):
    path = tmp_path / "outdated.db"
    first = TranslationDB(path)
    _seed(first)
    with first._tx() as conn:
        conn.execute("DROP TRIGGER effcol_effective_text")
        conn.execute(
            "CREATE TRIGGER effcol_effective_text AFTER UPDATE ON effective "
            "BEGIN SELECT 1; END"
        )
    assert not effective_columns.is_installed(first._conn)
    first.close()
    reopened = TranslationDB(path)
    try:
        assert effective_columns.is_installed(reopened._conn)
        with reopened._tx() as conn:
            conn.execute("UPDATE effective SET zh_tw='改' WHERE entry_id % 2 = 0")
        assert reopened.verify_effective_columns() == []
    finally:
        reopened.close()
