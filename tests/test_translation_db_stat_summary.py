"""全庫統計摘要：每條寫入路徑後都與全庫重算一致，以及安裝、暫停重算與舊資料庫退回。"""

from __future__ import annotations

import sqlite3

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    ScanItem,
    TranslationDB,
    WriteBackItem,
    stat_summary,
)
from translation_tool.translation_db.schema import (
    DEFAULT_PRIORITY,
    SRC_AI,
    SRC_JAR_CN,
    SRC_JAR_TW,
    SRC_MANUAL,
)


def _item(key, en, tw="", mod="foo", cn="", source=None):
    return ScanItem(KIND_LANG, mod, key, en, tw, cn, source)


def _entry(db, version, key, mod="foo"):
    rows = db.list_entries(version, limit=500)[0]
    return next(r for r in rows if r.key == key and r.mod_id == mod)


def _identity(entry):
    return {
        "expected_version": entry.mc_version,
        "expected_kind": entry.kind,
        "expected_mod_id": entry.mod_id,
        "expected_key": entry.key,
        "expected_en_us": entry.en_us,
    }


def assert_summary_matches(db):
    """摘要可用、與全庫重算逐筆一致，且三個讀取端的結果和重算完全相同。"""
    assert db.verify_stat_summary() == []
    assert db._stat_summary_ready()
    full_sources = db._effective_source_stats_by_version_full()
    assert db._effective_source_stats_by_version_rows() == full_sources
    summary_versions = db._version_stats_rows()
    # 全庫重算只依總數排序，同數量版本的先後未定義：比對內容，並與來源統計推導的排序一致
    assert sorted(summary_versions) == sorted(db._version_stats_full())
    assert summary_versions == db._version_stats_from_source_stats(
        [db_models_stat(row) for row in full_sources]
    )
    assert db._overview_uncached() == db._overview_full()
    # 其他觸發器維護的衍生資料也在每條寫入路徑後與重算比對
    assert db.verify_entry_activity() == []
    assert db.verify_effective_columns() == []
    assert db.verify_quality_flags() == []
    assert db._quality_flags_ready()


def db_models_stat(row):
    from translation_tool.translation_db import EffectiveSourceStat

    return EffectiveSourceStat(*row)


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "stats.db")
    yield d
    d.close()


def _seed(db):
    db.ingest(
        "1.20.1",
        [
            _item("same", "Iron", "鐵"),
            _item("diff", "Gold", "金"),
            _item("cn", "Copper", cn="铜"),
            _item("blank", "Stone"),
            _item("nosrc", "", "未知原文"),
            _item("other", "Wood", "木", mod="bar"),
        ],
        convert=lambda text: text.replace("铜", "銅"),
    )
    assert_summary_matches(db)
    db.ingest(
        "1.21.1",
        [
            _item("same", "Iron", "鐵"),
            _item("diff", "Gold", "黃金"),
            _item("cn", "Copper", cn="铜"),
            _item("blank", "Stone"),
            _item("nosrc", "", "未知原文"),
            _item("new", "Diamond", "鑽石", mod="baz"),
        ],
        convert=lambda text: text.replace("铜", "銅"),
    )
    assert_summary_matches(db)
    overview = db.overview()
    assert overview["diff"] == 1 and overview["no_source"] == 2
    assert overview["mods"] == 3


def test_new_database_installs_ready_summary(db):
    meta = dict(db._q("SELECT key, value FROM meta WHERE key LIKE 'stats%'"))
    assert meta == {"stats_schema_version": "1", "stats_valid": "1"}
    assert stat_summary.is_installed(db._conn)
    assert_summary_matches(db)


def test_ingest_scan_and_source_text_adoption(db):
    _seed(db)
    # 原文原本未知的條目在之後的掃描補上原文（entry.en_us 改變）
    db.ingest("1.21.1", [_item("nosrc", "Unknown", "未知原文")])
    assert _entry(db, "1.21.1", "nosrc").en_us == "Unknown"
    assert_summary_matches(db)
    # 原文變動只記錄、不改條目
    db.ingest("1.21.1", [_item("same", "Iron Ingot", "鐵錠")])
    assert db.overview()["src_changed"] == 1
    assert_summary_matches(db)
    # 已有條目補上新來源的譯文
    db.ingest("1.21.1", [_item("blank", "Stone", "石頭", source=SRC_MANUAL)])
    assert_summary_matches(db)


def test_manual_save_review_and_revert(db):
    _seed(db)
    target = _entry(db, "1.21.1", "diff")
    db.save_manual(target.id, "金", actor="editor", propagate=False)
    assert_summary_matches(db)
    db.save_manual(target.id, "金子", actor="editor", propagate=True)
    assert_summary_matches(db)
    blank = _entry(db, "1.20.1", "blank")
    db.save_manual(blank.id, "石", actor="editor")
    assert_summary_matches(db)
    same = _entry(db, "1.21.1", "same")
    db.review_manual(same.id, expected_zh_tw="鐵", actor="checker", propagate=True)
    assert_summary_matches(db)
    for history in db.entry_detail(target.id).history:
        db.revert(history.id, whole_batch=True)
        assert_summary_matches(db)
    db.revert(db.entry_detail(same.id).history[0].id, whole_batch=True)
    assert_summary_matches(db)


def test_bulk_review_and_revert(db):
    _seed(db)
    plan = db.preview_bulk_review(
        "1.21.1", SRC_JAR_TW, quality_pass_only=False, propagate=True
    )
    assert plan.candidates
    result = db.execute_bulk_review(plan, actor="checker")
    assert result.reviewed
    assert_summary_matches(db)
    assert db.revert_review_batch(result.batch)
    assert_summary_matches(db)


def test_batch_replace_and_revert(db):
    _seed(db)
    plan = db.preview_batch_replace(EntryFilter(version="1.21.1"), "鐵", "鋼")
    result = db.execute_batch_replace(plan, actor="tester")
    assert result.updated
    assert_summary_matches(db)
    db.revert_batch_replace(result.batch_id)
    assert_summary_matches(db)


def test_cleanup_apply_and_restore(db, tmp_path):
    db.ingest(
        "1.21.1",
        [
            ScanItem("guide", "m", "g/a.md#0", "[TODO: photo goes here]"),
            ScanItem("guide", "m", "g/a.md#1", "<Tag src='x'></Tag>"),
            ScanItem("guide", "m", "g/a.md#2", "Real text for the guide."),
            ScanItem("guide", "solo", "g/b.md#0", "[TODO: photo goes here]"),
        ],
    )
    report = tmp_path / "cleanup.json"
    db.export_cleanup_report("1.21.1", report)
    plan = db.apply_cleanup(report)
    assert plan.deletable
    assert_summary_matches(db)
    restored, _skipped = db.revert_cleanup(db.last_cleanup_batch().batch_id)
    assert restored == plan.deletable
    assert_summary_matches(db)


def test_priority_change_rebuilds_effective_with_frozen_triggers(db):
    _seed(db)
    blank = _entry(db, "1.21.1", "blank")
    db.save_manual(blank.id, "石塊", actor="editor", propagate=False)
    db.ingest("1.21.1", [_item("blank", "Stone", "石頭")])
    statements: list[str] = []
    db._conn.set_trace_callback(statements.append)
    try:
        db.set_priority(
            (SRC_JAR_TW, SRC_JAR_CN, *[s for s in DEFAULT_PRIORITY if s not in (1, 2)])
        )
    finally:
        db._conn.set_trace_callback(None)
    assert any("stats_frozen" in sql for sql in statements)
    assert _entry(db, "1.21.1", "blank").zh_tw == "石頭"
    assert db._one("SELECT 1 FROM meta WHERE key='stats_frozen'") is None
    assert_summary_matches(db)


def test_entry_delete_cascade_and_key_updates(db):
    _seed(db)
    target = _entry(db, "1.21.1", "diff")
    with db._tx() as conn:
        conn.execute("DELETE FROM entry WHERE id=?", (target.id,))
    assert db._one("SELECT 1 FROM effective WHERE entry_id=?", (target.id,)) is None
    assert_summary_matches(db)
    same = _entry(db, "1.21.1", "same")
    with db._tx() as conn:
        conn.execute("UPDATE entry SET en_us='Iron!' WHERE id=?", (same.id,))
    assert_summary_matches(db)
    with db._tx() as conn:
        conn.execute("UPDATE entry SET en_us='Iron' WHERE id=?", (same.id,))
        conn.execute(
            "UPDATE entry SET mod_id='moved', mc_version='9.9' WHERE id=?",
            (_entry(db, "1.20.1", "cn").id,),
        )
    assert_summary_matches(db)
    with db._tx() as conn:
        conn.execute("DELETE FROM entry WHERE mod_id='bar'")
        conn.execute("DELETE FROM effective WHERE entry_id=?", (same.id,))
        conn.execute(
            "UPDATE effective SET source=?, review_status=NULL WHERE entry_id=?",
            (SRC_MANUAL, _entry(db, "1.20.1", "same").id),
        )
    assert_summary_matches(db)


def test_write_back_and_reuse_from_other_versions(db):
    _seed(db)
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "foo", "blank", "Stone", "石頭"),
            WriteBackItem(KIND_LANG, "foo", "fresh", "Fresh", "新鮮"),
        ],
    )
    assert_summary_matches(db)
    db.ingest("1.22", [_item("same", "Iron"), _item("diff", "Gold")])
    assert_summary_matches(db)
    assert db.reuse_from_other_versions("1.22") >= 1
    assert_summary_matches(db)


def test_ai_repair_and_repair_source_sync(db):
    db.ingest("1.20.1", [_item("use", "Use %s", "使用", source=SRC_AI)])
    db.ingest("1.21.1", [_item("use", "Use %s", "使用", source=SRC_AI)])
    db.ingest("1.21.1", [_item("name", "Minecraft", "Minecraft", source=SRC_AI)])
    db.ingest("1.20.1", [_item("name", "Minecraft", "Minecraft", source=SRC_AI)])
    assert_summary_matches(db)
    name = _entry(db, "1.21.1", "name")
    result = db.replace_ai_translation(
        name.id, "Minecraft", "我的世界", **_identity(name)
    )
    assert result.status == "updated"
    assert_summary_matches(db)
    use = _entry(db, "1.21.1", "use")
    revision = db._one(
        "SELECT revision FROM translation WHERE entry_id=? AND source=?",
        (use.id, SRC_AI),
    )[0]
    repaired = db.replace_translation_quality_mismatch(
        use.id,
        SRC_AI,
        "使用",
        "使用 %s",
        **_identity(use),
        expected_revision=revision,
        sync_other_versions=True,
    )
    assert repaired.status == "updated" and repaired.synced_other_versions
    assert_summary_matches(db)
    history = db.entry_detail(use.id).history
    db.revert(history[0].id, whole_batch=True)
    assert_summary_matches(db)


def test_frozen_block_reseeds_and_rollback_clears_flag(db):
    _seed(db)
    with db._tx() as conn, stat_summary.frozen(conn):
        assert not db._stat_summary_ready()
        conn.execute("DELETE FROM effective")
        conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES ('lang', '2.0', 'new', 'k', 'K')"
        )
        # 暫停期間讀取端退回全庫重算，結果仍正確
        assert db._overview_uncached() == db._overview_full()
    assert db._one("SELECT 1 FROM meta WHERE key='stats_frozen'") is None
    assert_summary_matches(db)
    with pytest.raises(RuntimeError), db._tx() as conn, stat_summary.frozen(conn):
        conn.execute("DELETE FROM entry")
        raise RuntimeError("boom")
    assert db._one("SELECT 1 FROM meta WHERE key='stats_frozen'") is None
    assert_summary_matches(db)


def _drop_summary(path):
    with sqlite3.connect(path) as conn:
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'stat_%'"
        ).fetchall():
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("DROP TABLE stat_src")
        conn.execute("DROP TABLE stat_counter")
        conn.execute(
            "DELETE FROM meta WHERE key IN ('stats_schema_version', 'stats_valid')"
        )


def _snapshot(conn):
    tables = ("entry", "translation", "effective", "history", "src_change")
    snap = {
        t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables
    }
    snap["meta"] = conn.execute(
        "SELECT * FROM meta WHERE key NOT LIKE 'stats%' ORDER BY key"
    ).fetchall()
    return snap


def test_readonly_old_database_without_summary_falls_back(tmp_path):
    path = tmp_path / "old.db"
    writer = TranslationDB(path)
    _seed(writer)
    expected = (
        writer._effective_source_stats_by_version_full(),
        sorted(writer._version_stats_full()),
        writer._overview_full(),
    )
    writer.close()
    _drop_summary(path)
    reader = TranslationDB(path, readonly=True)
    try:
        assert not reader._stat_summary_ready()
        assert [
            [s.mc_version, s.source, s.review_status, s.count]
            for s in reader.effective_source_stats_by_version()
        ] == expected[0]
        assert sorted(list(v.__dict__.values()) for v in reader.version_stats()) == [
            list(row) for row in expected[1]
        ]
        assert reader.overview() == expected[2]
    finally:
        reader.close()


def test_invalid_flag_falls_back_until_rebuilt(db):
    _seed(db)
    with db._tx() as conn:
        conn.execute("UPDATE stat_counter SET v = v + 5 WHERE name='diff'")
        conn.execute("UPDATE meta SET value='0' WHERE key='stats_valid'")
    assert not db._stat_summary_ready()
    assert db._overview_uncached() == db._overview_full()
    assert db.verify_stat_summary()
    generation = db._data_gen()
    db.rebuild_stat_summary()
    # 重建會遞增資料世代，依失準摘要算出的統計快取一併失效
    assert db._data_gen() != generation
    assert_summary_matches(db)


def test_outdated_trigger_definition_is_reinstalled(tmp_path):
    path = tmp_path / "outdated.db"
    first = TranslationDB(path)
    _seed(first)
    with first._tx() as conn:
        conn.execute("DROP TRIGGER stat_effective_update")
        conn.execute(
            "CREATE TRIGGER stat_effective_update AFTER UPDATE ON effective "
            "BEGIN SELECT 1; END"
        )
    assert not stat_summary.is_installed(first._conn)
    first.close()
    reopened = TranslationDB(path)
    try:
        assert stat_summary.is_installed(reopened._conn)
        with reopened._tx() as conn:
            conn.execute(
                "UPDATE effective SET source=? WHERE entry_id=?",
                (SRC_MANUAL, _entry(reopened, "1.21.1", "same").id),
            )
        assert_summary_matches(reopened)
    finally:
        reopened.close()


def test_migration_installs_summary_on_existing_database(tmp_path):
    path = tmp_path / "existing.db"
    first = TranslationDB(path)
    _seed(first)
    first.close()
    _drop_summary(path)
    with sqlite3.connect(path) as conn:
        before = _snapshot(conn)
    migrated = TranslationDB(path)
    try:
        assert stat_summary.is_installed(migrated._conn)
        assert _snapshot(migrated._conn) == before
        assert_summary_matches(migrated)
    finally:
        migrated.close()
    with sqlite3.connect(path) as conn:
        full_meta = conn.execute("SELECT * FROM meta ORDER BY key").fetchall()
    # 已安裝且有效時重新開啟不做任何寫入
    again = TranslationDB(path)
    try:
        assert again._q("SELECT * FROM meta ORDER BY key") == full_meta
    finally:
        again.close()


def test_repository_never_uses_insert_or_replace_on_effective():
    from pathlib import Path

    package = Path(stat_summary.__file__).parent
    for source in package.glob("*.py"):
        text = " ".join(source.read_text(encoding="utf-8").upper().split())
        assert "REPLACE INTO EFFECTIVE" not in text, source
