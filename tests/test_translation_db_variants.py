"""其他原文（變體）：鍵值相同但原文不同時，譯文能存、能查，且不影響主原文與其他功能。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    ScanItem,
    TranslationDB,
    TranslationResolver,
    WriteBackItem,
)
from translation_tool.translation_db.schema import (
    KIND_LANG,
    SRC_AI,
    SRC_JAR_TW,
    SRC_MANUAL,
)

VERSION = "1.21.1"


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "t.db")
    yield d
    d.close()


def _wb(en, tw, key="item.foo.a", mod="foo"):
    return WriteBackItem(KIND_LANG, mod, key, en, tw)


def _seed(db, en="Show Background", tw="顯示背景"):
    db.ingest(VERSION, [ScanItem(KIND_LANG, "foo", "item.foo.a", en, tw, "")])


def test_variant_translation_is_written_and_main_entry_is_untouched(db):
    _seed(db)
    stats = db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    assert stats.variant_written == 1 and stats.written == 0 and stats.skipped == 0
    assert db._q("SELECT en_us FROM entry") == [("Show Background",)]
    assert db._q(
        "SELECT v.en_us, t.zh_tw, t.source FROM entry_variant v "
        "JOIN variant_translation t ON t.variant_id = v.id"
    ) == [("Background Style", "背景樣式", SRC_AI)]
    # 原文變動的歷史紀錄照舊保留
    assert [(c["old_en"], c["new_en"]) for c in map(_row, db.src_changes())] == [
        ("Show Background", "Background Style")
    ]
    # 變體不會出現在條目清單與生效譯文
    assert db.count_entries() == 1
    assert db._q("SELECT zh_tw FROM effective") == [("顯示背景",)]


def _row(row):
    return row if isinstance(row, dict) else dict(row._asdict())


def test_variant_write_back_is_insert_only(db):
    _seed(db)
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    again = db.write_back(VERSION, [_wb("Background Style", "別的翻法")])
    assert again.variant_written == 0 and again.skipped == 1
    assert db._q("SELECT zh_tw FROM variant_translation") == [("背景樣式",)]
    assert db._one("SELECT COUNT(*) FROM entry_variant")[0] == 1


def test_variant_is_not_propagated_to_other_versions(db):
    _seed(db)
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "foo", "item.foo.a", "Background Style")])
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    # 1.20.1 的原文與變體相同，但變體不做跨版本補空白（見 ADR 0007）
    assert db._q(
        "SELECT COUNT(*) FROM effective f JOIN entry e ON e.id = f.entry_id "
        "WHERE e.mc_version='1.20.1'"
    ) == [(0,)]


def test_resolver_hits_main_and_variant_but_not_unknown_text(db):
    _seed(db)
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    resolver = TranslationResolver(db, VERSION)
    main = resolver.lookup(KIND_LANG, "foo", "item.foo.a", "Show Background")
    var = resolver.lookup(KIND_LANG, "foo", "item.foo.a", "Background Style")
    other = resolver.lookup(KIND_LANG, "foo", "item.foo.a", "Third Wording")
    assert main is not None and main.zh_tw == "顯示背景"
    assert var is not None and var.zh_tw == "背景樣式" and not var.cross
    assert other is None
    assert resolver.stats.hit_target == 2 and resolver.stats.en_mismatch == 1


def test_resolver_with_priority_snapshot_also_reads_variants(db):
    _seed(db)
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    resolver = TranslationResolver(
        db, VERSION, source_priority=(SRC_MANUAL, SRC_JAR_TW, SRC_AI)
    )
    hit = resolver.lookup(KIND_LANG, "foo", "item.foo.a", "Background Style")
    assert hit is not None and hit.zh_tw == "背景樣式"


def test_variant_uses_source_priority_to_pick_translation(db):
    _seed(db)
    db.write_back(VERSION, [_wb("Background Style", "AI 譯")])
    vid = db._one("SELECT id FROM entry_variant")[0]
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO variant_translation (variant_id, source, zh_tw) VALUES (?,?,?)",
            (vid, SRC_JAR_TW, "模組自帶"),
        )
    default = TranslationResolver(db, VERSION).lookup(
        KIND_LANG, "foo", "item.foo.a", "Background Style"
    )
    ai_first = TranslationResolver(
        db, VERSION, source_priority=(SRC_AI, SRC_JAR_TW)
    ).lookup(KIND_LANG, "foo", "item.foo.a", "Background Style")
    assert default.zh_tw == "模組自帶"
    assert ai_first.zh_tw == "AI 譯"


def test_variant_rows_are_removed_with_their_entry(db):
    _seed(db)
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    with db._tx() as conn:
        conn.execute("DELETE FROM entry")
    assert db._one("SELECT COUNT(*) FROM entry_variant")[0] == 0
    assert db._one("SELECT COUNT(*) FROM variant_translation")[0] == 0


def test_variant_survives_reopening_the_database(tmp_path):
    path = tmp_path / "t.db"
    first = TranslationDB(path)
    _seed(first)
    first.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    first.close()
    second = TranslationDB(path)
    try:
        hit = TranslationResolver(second, VERSION).lookup(
            KIND_LANG, "foo", "item.foo.a", "Background Style"
        )
        assert hit is not None and hit.zh_tw == "背景樣式"
    finally:
        second.close()


def test_variant_write_back_bumps_cache_generation(db):
    """變體寫入走 ``_tx``，快取世代照常遞增（統計與清單快取不會讀到舊資料）。"""
    _seed(db)
    before = db._one("SELECT value FROM meta WHERE key='data_gen'")[0]
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    after = db._one("SELECT value FROM meta WHERE key='data_gen'")[0]
    assert after != before


def test_variant_query_has_no_temp_sort_and_old_schema_reads_main_rows(tmp_path):
    """查詢計畫不得臨時排序；舊 schema（沒有變體表）唯讀開啟仍讀得到主原文譯文。"""
    import sqlite3

    path = tmp_path / "old.db"
    first = TranslationDB(path)
    _seed(first)
    first.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    group, rank = first._priority_parts()
    plan = " ".join(
        str(r[-1])
        for r in first._q(
            "EXPLAIN QUERY PLAN SELECT v.id, "
            f"{group}, {rank} FROM entry e "
            "CROSS JOIN entry_variant v ON v.entry_id = e.id "
            "CROSS JOIN variant_translation t ON t.variant_id = v.id "
            "WHERE e.mod_id = ? AND t.zh_tw <> ''",
            ("foo",),
        )
    )
    assert "TEMP B-TREE" not in plan
    first.close()

    raw = sqlite3.connect(path)
    raw.executescript(
        "DROP TABLE variant_translation; DROP TABLE entry_variant; "
        "UPDATE meta SET value='17' WHERE key='schema_version';"
    )
    raw.commit()
    raw.close()
    readonly = TranslationDB(path, readonly=True)
    try:
        rows = readonly.load_mod_for_priority("foo", (SRC_MANUAL, SRC_JAR_TW, SRC_AI))
        assert [(r[2], r[4]) for r in rows] == [("Show Background", "顯示背景")]
        assert [r[2] for r in readonly.load_mod("foo")] == ["Show Background"]
    finally:
        readonly.close()


def test_variant_uses_database_priority_not_instance_priority(tmp_path):
    """沒有指定優先序時，變體與主原文一樣用資料庫 meta 的共用優先序。"""
    path = tmp_path / "p.db"
    first = TranslationDB(path)
    _seed(first)
    first.write_back(VERSION, [_wb("Background Style", "AI 譯")])
    vid = first._one("SELECT id FROM entry_variant")[0]
    with first._tx() as conn:
        conn.execute(
            "INSERT INTO variant_translation (variant_id, source, zh_tw) VALUES (?,?,?)",
            (vid, SRC_JAR_TW, "模組自帶"),
        )
        conn.execute(
            "UPDATE meta SET value=? WHERE key='priority'",
            (f"{SRC_MANUAL},{SRC_JAR_TW},{SRC_AI}",),
        )
    first.close()
    # 以相反的優先序開啟、且不同步：主原文列看 effective（meta 的優先序），變體必須一致
    second = TranslationDB(
        path, priority=(SRC_AI, SRC_JAR_TW, SRC_MANUAL), sync_priority=False
    )
    try:
        rows = {r[2]: r[4] for r in second.load_mod("foo")}
        assert rows["Background Style"] == "模組自帶"
    finally:
        second.close()


def test_variant_is_found_by_cross_version_lookup_but_never_filled(db):
    """寫回不補其他版本；流水線開啟跨版本沿用時，原文相同的其他版本查得到。"""
    _seed(db)
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    cross = TranslationResolver(db, "1.20.1", cross_version=True)
    hit = cross.lookup(KIND_LANG, "foo", "item.foo.a", "Background Style")
    assert hit is not None and hit.cross and hit.zh_tw == "背景樣式"
    off = TranslationResolver(db, "1.20.1", cross_version=False)
    assert off.lookup(KIND_LANG, "foo", "item.foo.a", "Background Style") is None


def test_load_mod_statements_for_variants_have_no_temp_sort(db):
    """實際執行的變體載入語句（不是抄一份）的查詢計畫不得臨時排序。"""
    _seed(db)
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])
    captured: list[str] = []
    with db._lock:
        db._conn.set_trace_callback(captured.append)
    try:
        db.load_mod("foo")
        db.load_mod_for_priority("foo", (SRC_MANUAL, SRC_JAR_TW, SRC_AI))
    finally:
        with db._lock:
            db._conn.set_trace_callback(None)
    variant_sql = [
        q for q in captured if "FROM entry_variant" in q or "entry_variant v" in q
    ]
    assert variant_sql, "沒有抓到變體載入語句"
    for sql in variant_sql:
        plan = " ".join(str(r[-1]) for r in db._q("EXPLAIN QUERY PLAN " + sql))
        assert "TEMP B-TREE" not in plan, sql


def test_external_sql_write_to_variant_tables_bumps_change_sequence(db):
    """外部 SQL 直接改變體表也會使統計快取失效（change_tracker 有追蹤）。"""
    _seed(db)
    db.write_back(VERSION, [_wb("Background Style", "背景樣式")])

    def seq() -> int:
        row = db._one("SELECT value FROM meta WHERE key='ext_change_seq'")
        return int(row[0]) if row else 0

    before = seq()
    with db._lock:
        db._conn.execute("UPDATE variant_translation SET zh_tw='外部修改'")
        db._conn.commit()
    after_translation = seq()
    with db._lock:
        db._conn.execute("UPDATE entry_variant SET en_us=en_us || ' '")
        db._conn.commit()
    assert after_translation > before and seq() > after_translation


def test_write_back_same_batch_variants_and_blank_main_original(db):
    """同批次：同原文第二筆算略過、不同新原文各建變體；主原文只有空白視為未知。"""
    _seed(db)
    stats = db.write_back(
        VERSION,
        [
            _wb("Second Wording", "第二種"),
            _wb("Second Wording", "重複"),
            _wb("Third Wording", "第三種"),
        ],
    )
    assert stats.variant_written == 2 and stats.skipped == 1
    assert db._one("SELECT COUNT(*) FROM entry_variant")[0] == 2

    db.ingest(VERSION, [ScanItem(KIND_LANG, "foo", "blank", "   ", "", "")])
    blank = db.write_back(VERSION, [_wb("Real Text", "真實", key="blank")])
    assert blank.variant_written == 0 and blank.written == 1
    assert db._one("SELECT en_us FROM entry WHERE key='blank'")[0] == "Real Text"


def test_v17_database_with_data_upgrades_to_v18_and_keeps_translations(tmp_path):
    """舊資料庫（有資料、沒有變體表）升到 v18 後譯文仍在，並能開始寫變體。"""
    import sqlite3

    path = tmp_path / "v17.db"
    first = TranslationDB(path)
    _seed(first)
    first.close()
    raw = sqlite3.connect(path)
    raw.executescript(
        "DROP TABLE variant_translation; DROP TABLE entry_variant; "
        "UPDATE meta SET value='17' WHERE key='schema_version';"
    )
    raw.commit()
    raw.close()
    upgraded = TranslationDB(path)
    try:
        assert (
            upgraded._one("SELECT value FROM meta WHERE key='schema_version'")[0]
            == "18"
        )
        assert upgraded._q("SELECT zh_tw FROM effective") == [("顯示背景",)]
        assert list(tmp_path.glob("v17.db.pre-schema-v18-*.bak"))
        stats = upgraded.write_back(VERSION, [_wb("Background Style", "背景樣式")])
        assert stats.variant_written == 1
    finally:
        upgraded.close()
