"""Mod 翻譯資料庫核心：身分、寫入規則、掃描、手動同步、寫回與查詢。"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from translation_tool.translation_db import (
    KIND_ADVANCEMENT,
    KIND_LANG,
    KIND_PATCHOULI,
    ScanItem,
    TranslationDB,
    TranslationResolver,
    WriteBackBuffer,
    WriteBackItem,
    split_items_by_db,
)
from translation_tool.translation_db.identity import (
    classify_file,
    classify_member,
    get_by_path,
)
from translation_tool.translation_db.models import (
    EntryFilter,
    QualityFilter,
    TimeFilter,
)
from translation_tool.translation_db.quality import (
    format_tokens,
    repair_input_issues,
    repair_output_issues,
    token_issues,
)
from translation_tool.translation_db.resolver import version_number
from translation_tool.translation_db.scanner import (
    ScanOptions,
    scan_folder_generator,
    scan_jar,
)
from translation_tool.translation_db.schema import (
    SRC_AI,
    SRC_AI_REPAIR,
    SRC_JAR_CN,
    SRC_JAR_TW,
    SRC_MANUAL,
    SRC_SUBTITLE,
)


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "t.db")
    yield d
    d.close()


def item(key="item.foo.a", en="Steel Casing", tw="", cn="", mod="foo", kind=KIND_LANG):
    return ScanItem(kind, mod, key, en, tw, cn)


def test_history_retention_keeps_only_latest_five_rows_per_entry(db):
    db.ingest("1.21.1", [item()])
    entry_id = db._one("SELECT id FROM entry LIMIT 1")[0]

    with db._tx() as conn:
        for number in range(7):
            conn.execute(
                "INSERT INTO history "
                "(entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
                "VALUES (?, ?, 'manual', '', ?, ?)",
                (entry_id, f"batch-{number}", f"譯文-{number}", str(number)),
            )

    rows = db._q("SELECT note FROM history WHERE entry_id=? ORDER BY id", (entry_id,))
    assert rows == [("2",), ("3",), ("4",), ("5",), ("6",)]
    assert len(db.entry_detail(entry_id).history) == 5


def test_history_retention_preserves_pending_smart_review_marker(db):
    """JSON 尚未對帳時，套用 marker 不會被五筆一般歷史清掉。"""
    db.ingest("1.21.1", [item()])
    entry_id = db._one("SELECT id FROM entry LIMIT 1")[0]

    with db._tx() as conn:
        conn.execute(
            "INSERT INTO history "
            "(entry_id, batch, action, old_zh_tw, new_zh_tw, note, projection_state) "
            "VALUES (?, ?, 'smart_review_apply', '', ?, ?, 'pending')",
            (entry_id, "smart-run", "AI 譯文", "run=run-1; ordinal=1"),
        )
    for number in range(5):
        with db._tx() as conn:
            conn.execute(
                "INSERT INTO history "
                "(entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
                "VALUES (?, ?, 'manual', '', ?, ?)",
                (entry_id, f"manual-{number}", f"人工-{number}", str(number)),
            )

    assert db._one(
        "SELECT projection_state FROM history "
        "WHERE entry_id=? AND action='smart_review_apply'",
        (entry_id,),
    ) == ("pending",)

    with db._tx() as conn:
        conn.execute(
            "UPDATE history SET projection_state='projected' "
            "WHERE entry_id=? AND action='smart_review_apply'",
            (entry_id,),
        )
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO history "
            "(entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
            "VALUES (?, 'manual-final', 'manual', '', '最後', 'final')",
            (entry_id,),
        )
    assert db._one(
        "SELECT COUNT(*) FROM history WHERE entry_id=? AND action='smart_review_apply'",
        (entry_id,),
    ) == (0,)


def test_pending_smart_review_marker_survives_five_later_writes_and_reopen(tmp_path):
    """JSON 回寫失敗後重開資料庫，仍能找回 SQLite 的套用證據。"""
    path = tmp_path / "pending-marker.db"
    db = TranslationDB(path)
    db.ingest("1.21.1", [item()])
    entry_id = db._one("SELECT id FROM entry LIMIT 1")[0]
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO history "
            "(entry_id, batch, action, old_zh_tw, new_zh_tw, note, projection_state) "
            "VALUES (?, ?, 'smart_review_apply', '', ?, ?, 'pending')",
            (entry_id, "smart-run", "AI 譯文", "run=run-1; ordinal=1"),
        )
    for number in range(5):
        with db._tx() as conn:
            conn.execute(
                "INSERT INTO history "
                "(entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
                "VALUES (?, ?, 'manual', '', ?, ?)",
                (entry_id, f"manual-{number}", f"人工-{number}", str(number)),
            )
    db.close()

    reopened = TranslationDB(path)
    try:
        marker = reopened._one(
            "SELECT action, projection_state, note FROM history "
            "WHERE entry_id=? AND action='smart_review_apply'",
            (entry_id,),
        )
        assert marker == (
            "smart_review_apply",
            "pending",
            "run=run-1; ordinal=1",
        )
    finally:
        reopened.close()


def test_history_retention_migration_prunes_existing_database_and_keeps_backup(
    tmp_path,
):
    path = tmp_path / "history-v8.db"
    db = TranslationDB(path)
    db.ingest("1.21.1", [item()])
    entry_id = db._one("SELECT id FROM entry LIMIT 1")[0]
    db.close()

    # 重建保留過量記錄的 v9 前資料庫狀態，讓 migration 路徑能和一般寫入
    # 交易的清理路徑分開驗證。
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE history DROP COLUMN projection_state")
    conn.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
    for number in range(7):
        conn.execute(
            "INSERT INTO history "
            "(entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
            "VALUES (?, ?, 'manual', '', ?, ?)",
            (entry_id, f"legacy-{number}", f"舊譯文-{number}", str(number)),
        )
    conn.commit()
    conn.close()

    reopened = TranslationDB(path)
    try:
        assert reopened._one("SELECT value FROM meta WHERE key='schema_version'") == (
            "10",
        )
        assert reopened._one(
            "SELECT COUNT(*) FROM history WHERE entry_id=?", (entry_id,)
        ) == (5,)
        assert "projection_state" in {
            row[1] for row in reopened._q("PRAGMA table_info(history)")
        }
        assert list(tmp_path.glob("history-v8.db.pre-schema-v9-*.bak"))
        assert list(tmp_path.glob("history-v8.db.pre-schema-v10-*.bak"))
    finally:
        reopened.close()


def test_history_retention_migration_preserves_legacy_smart_review_marker(
    tmp_path,
):
    """v8 migration 無法判定舊 marker 是否已對帳時，先保留以便後續恢復。"""
    path = tmp_path / "history-v8-pending.db"
    db = TranslationDB(path)
    db.ingest("1.21.1", [item()])
    entry_id = db._one("SELECT id FROM entry LIMIT 1")[0]
    db.close()

    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE history DROP COLUMN projection_state")
    conn.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
    conn.execute(
        "INSERT INTO history "
        "(entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
        "VALUES (?, 'legacy-smart-run', 'smart_review_apply', '', ?, ?)",
        (entry_id, "AI 譯文", "run=legacy-run; ordinal=1"),
    )
    for number in range(7):
        conn.execute(
            "INSERT INTO history "
            "(entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
            "VALUES (?, ?, 'manual', '', ?, ?)",
            (entry_id, f"legacy-manual-{number}", f"人工-{number}", str(number)),
        )
    conn.commit()
    conn.close()

    reopened = TranslationDB(path)
    try:
        marker = reopened._one(
            "SELECT action, projection_state, note FROM history "
            "WHERE entry_id=? AND action='smart_review_apply'",
            (entry_id,),
        )
        assert marker == (
            "smart_review_apply",
            "pending",
            "run=legacy-run; ordinal=1",
        )
        assert reopened._one(
            "SELECT COUNT(*) FROM history WHERE entry_id=? AND action='manual'",
            (entry_id,),
        ) == (5,)
    finally:
        reopened.close()


# ---------------------------------------------------------------- 身分
def test_classify_lang_and_patchouli_members():
    lang = classify_member("assets/foo/lang/en_us.json")
    assert (lang.kind, lang.mod_id, lang.lang) == (KIND_LANG, "foo", "en_us")
    assert classify_member("assets/foo/lang/ZH_TW.json").lang == "zh_tw"
    legacy = classify_member("assets/foo/lang/en_US.lang")
    assert (legacy.kind, legacy.lang) == (KIND_LANG, "en_us")
    assert classify_member("data/foo/lang/en_us.json").kind == KIND_LANG
    assert classify_member("assets/foo/textures/a.json") is None

    book = classify_member("assets/foo/patchouli_books/guide/en_us/entries/a/b.json")
    assert (book.kind, book.file_key) == (
        KIND_PATCHOULI,
        "patchouli_books/guide/entries/a/b.json",
    )
    # data 與 assets、不同語言資料夾 → 同一個身分
    other = classify_member("data/foo/patchouli_books/guide/zh_tw/entries/a/b.json")
    assert other.file_key == book.file_key and other.lang == "zh_tw"
    assert classify_member("assets/foo/patchouli_books/guide/entries/a.json") is None

    advancement = classify_member("data/foo/advancements/story/start.json")
    assert (advancement.kind, advancement.file_key, advancement.lang) == (
        KIND_ADVANCEMENT,
        "story/start.json",
        "en_us",
    )
    assert advancement.item_key("display.title.fallback") == (
        "story/start.json#display.title.fallback"
    )


def test_classify_file_relative_to_root(tmp_path):
    f = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    ident = classify_file(f, tmp_path)
    assert ident.mod_id == "foo" and ident.item_key("item.x") == "item.x"
    bf = tmp_path / "assets" / "foo" / "book" / "en_us" / "e.json"
    assert (
        classify_file(bf, tmp_path).item_key("pages[0].text")
        == "book/e.json#pages[0].text"
    )


def test_get_by_path_handles_dotted_keys_and_lists():
    data = {"item.foo.bar": "A", "pages": [{"text": "B"}]}
    assert get_by_path(data, "item.foo.bar") == "A"
    assert get_by_path(data, "pages[0].text") == "B"
    assert get_by_path(data, "pages[3].text") is None
    assert get_by_path(data, "missing") is None


def test_version_number_orders_labels():
    assert (
        version_number("1.21.1") > version_number("1.20.1") > version_number("1.16.5")
    )
    assert version_number("1.21 (24w18a)~1.21.1") == version_number("1.21.1")
    assert version_number("unknown") == 0


# ---------------------------------------------------------- 掃描寫入規則
def test_ingest_inserts_and_skips_existing(db):
    stats = db.ingest("1.21.1", [item(tw="鋼製外殼")])
    assert (stats.new_entries, stats.existing) == (1, 0)
    again = db.ingest("1.21.1", [item(tw="完全不同的譯文")])
    assert (again.new_entries, again.existing) == (0, 1)
    rows, total = db.list_entries("1.21.1")
    assert total == 1 and rows[0].zh_tw == "鋼製外殼"  # 沒有被覆蓋


def test_ingest_adds_missing_source_to_existing_entry(db):
    db.ingest("1.21.1", [item()])
    stats = db.ingest("1.21.1", [item(tw="鋼製外殼")])
    assert stats.added_translations == 1
    assert db.list_entries("1.21.1")[0][0].zh_tw == "鋼製外殼"


def test_ingest_records_en_change_without_touching_entry(db):
    db.ingest("1.21.1", [item(en="Steel Casing", tw="鋼製外殼")])
    stats = db.ingest("1.21.1", [item(en="Steel Casing Block", tw="鋼製外殼方塊")])
    assert stats.en_changed == 1
    row = db.list_entries("1.21.1")[0][0]
    assert (row.en_us, row.zh_tw) == ("Steel Casing", "鋼製外殼")
    assert len(db.src_changes("1.21.1")) == 1
    db.ingest("1.21.1", [item(en="Steel Casing Block")])
    assert len(db.src_changes("1.21.1")) == 1  # 同一筆變動不重複記錄


def test_ingest_converts_cn_only_when_no_tw(db):
    conv = lambda s: s.replace("钢", "鋼")
    db.ingest("1.21.1", [item(cn="钢外壳"), item(key="k2", cn="钢", tw="繁中")], conv)
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert (rows["item.foo.a"].zh_tw, rows["item.foo.a"].source) == (
        "鋼外壳",
        SRC_JAR_CN,
    )
    assert (rows["k2"].zh_tw, rows["k2"].source) == ("繁中", SRC_JAR_TW)


def test_versions_are_independent(db):
    db.ingest("1.21.1", [item(tw="新版")])
    db.ingest("1.20.1", [item(tw="舊版")])
    assert sorted(db.versions()) == ["1.20.1", "1.21.1"]
    assert db.list_entries("1.20.1")[0][0].zh_tw == "舊版"
    assert db.list_entries("1.21.1")[0][0].diff is True  # 其他版本相同內容、譯文不同


def test_source_priority_picks_best_and_rebuilds(tmp_path):
    path = tmp_path / "p.db"
    d = TranslationDB(path)
    d.ingest("1.21.1", [item(tw="自帶", cn="簡")], None)
    eid = d.list_entries("1.21.1")[0][0].id
    with d._tx() as conn:  # 補一筆町宮來源
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?,?,?)",
            (eid, SRC_SUBTITLE, "町宮"),
        )
        d._refresh(conn, [eid])
    assert d.get_entry(eid).zh_tw == "町宮"
    d.close()
    d2 = TranslationDB(path, priority=(SRC_JAR_TW, SRC_SUBTITLE))  # 改優先序 → 重建
    assert d2.get_entry(eid).zh_tw == "自帶"
    d2.close()


def test_load_mod_for_priority_filters_first_and_uses_mod_index(db):
    priority = (SRC_AI, SRC_JAR_TW)
    db.ingest(
        "1.21.1",
        [
            item(key="priority", tw="模組自帶", mod="target"),
            item(key="reviewed", tw="已審核的模組譯文", mod="target"),
            item(key="empty", mod="target"),
            item(key="other", tw="其他模組", mod="other"),
            *[
                item(key=f"unrelated.{i}", tw=f"其他譯文 {i}", mod="unrelated")
                for i in range(500)
            ],
        ],
    )
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "target",
                "priority",
                "Steel Casing",
                "AI 優先譯文",
                source=SRC_AI,
            ),
            ScanItem(
                KIND_LANG,
                "target",
                "reviewed",
                "Steel Casing",
                "AI 審核候選",
                source=SRC_AI,
            ),
        ],
    )

    reviewed_id = db._one(
        "SELECT id FROM entry WHERE mod_id='target' AND key='reviewed'"
    )[0]
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET checker='reviewed' WHERE entry_id=? AND source=?",
            (reviewed_id, SRC_JAR_TW),
        )

    rows = db.load_mod_for_priority("target", priority)
    assert rows == [
        (KIND_LANG, "priority", "Steel Casing", "1.21.1", "AI 優先譯文", SRC_AI),
        (
            KIND_LANG,
            "reviewed",
            "Steel Casing",
            "1.21.1",
            "已審核的模組譯文",
            SRC_JAR_TW,
        ),
    ]

    db._conn.execute("ANALYZE")
    plan = [
        row[3]
        for row in db._conn.execute(
            "EXPLAIN QUERY PLAN " + db._load_mod_for_priority_sql(priority),
            ("target",),
        )
    ]
    assert any(
        "SEARCH e USING INDEX idx_entry_mod_id (mod_id=?)" in step for step in plan
    )
    assert any(
        "SEARCH t USING INDEX sqlite_autoindex_translation_1 (entry_id=?)" in step
        for step in plan
    )


# ---------------------------------------------------------- 手動更新
def test_manual_save_propagates_to_same_content_only(db):
    db.ingest("1.21.1", [item(tw="能量")])
    db.ingest("1.20.1", [item(tw="能源")])
    db.ingest("1.16.5", [item(en="Steel Casing Old", tw="舊")])  # 原文不同
    cur = db.list_entries("1.21.1")[0][0]
    preview = db.preview_manual(cur.id, "電能")
    assert {i.mc_version for i in preview} == {"1.21.1", "1.20.1"}
    done = db.save_manual(cur.id, "電能", actor="jlin")
    assert {i.mc_version for i in done} == {"1.21.1", "1.20.1"}
    assert db.list_entries("1.20.1")[0][0].zh_tw == "電能"
    assert db.list_entries("1.20.1")[0][0].source == SRC_MANUAL
    assert db.list_entries("1.16.5")[0][0].zh_tw == "舊"
    assert db.list_entries("1.21.1")[0][0].diff is False


def test_manual_save_without_propagation_and_original_kept(db):
    db.ingest("1.21.1", [item(tw="A")])
    db.ingest("1.20.1", [item(tw="B")])
    cur = db.list_entries("1.21.1")[0][0]
    db.save_manual(cur.id, "C", propagate=False)
    assert db.list_entries("1.20.1")[0][0].zh_tw == "B"
    detail = db.entry_detail(cur.id)
    assert {t.source for t in detail.translations} == {
        SRC_JAR_TW,
        SRC_MANUAL,
    }  # 原譯文保留
    assert detail.history[0].action == "manual"


def test_manual_save_overrides_ai_repair_source_and_keeps_repair_history(db):
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", "item.ai-repair", "Use", "Use", source=SRC_AI)],
    )
    entry = db.list_entries("1.21.1")[0][0]
    identity = {
        "expected_version": entry.mc_version,
        "expected_kind": entry.kind,
        "expected_mod_id": entry.mod_id,
        "expected_key": entry.key,
        "expected_en_us": entry.en_us,
    }

    assert (
        db.replace_ai_translation(entry.id, "Use", "AI repaired", **identity).status
        == "updated"
    )
    assert db.get_entry(entry.id).source == SRC_AI_REPAIR

    db.save_manual(entry.id, "Manual override", propagate=False)

    current = db.get_entry(entry.id)
    assert (current.zh_tw, current.source, current.review_status) == (
        "Manual override",
        SRC_MANUAL,
        "unreviewed",
    )
    translations = {
        row.source: row.zh_tw for row in db.entry_detail(entry.id).translations
    }
    assert translations[SRC_AI_REPAIR] == "AI repaired"
    assert db.entry_detail(entry.id).history[0].action == "manual"


def test_open_rebuilds_stale_effective_projection_after_priority_rule_change(tmp_path):
    path = tmp_path / "stale-effective.db"
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG, "foo", "item.stale-effective", "Use", "Use", source=SRC_AI
            )
        ],
    )
    entry = db.list_entries("1.21.1")[0][0]
    identity = {
        "expected_version": entry.mc_version,
        "expected_kind": entry.kind,
        "expected_mod_id": entry.mod_id,
        "expected_key": entry.key,
        "expected_en_us": entry.en_us,
    }
    db.replace_ai_translation(entry.id, "Use", "AI repaired", **identity)
    db.save_manual(entry.id, "Manual override", propagate=False)
    with db._tx() as conn:
        conn.execute(
            "UPDATE effective SET zh_tw='AI repaired', source=? WHERE entry_id=?",
            (SRC_AI_REPAIR, entry.id),
        )
        conn.execute("DELETE FROM meta WHERE key='effective_priority_version'")
    db.close()

    reopened = TranslationDB(path)
    current = reopened.get_entry(entry.id)
    assert (current.zh_tw, current.source) == ("Manual override", SRC_MANUAL)
    reopened.close()


def test_revert_restores_previous_state_for_whole_batch(db):
    db.ingest("1.21.1", [item(tw="A")])
    db.ingest("1.20.1", [item(tw="B")])
    cur = db.list_entries("1.21.1")[0][0]
    db.save_manual(cur.id, "C")
    hid = db.entry_detail(cur.id).history[0].id
    assert db.revert(hid) == 2
    assert db.list_entries("1.21.1")[0][0].zh_tw == "A"
    assert db.list_entries("1.20.1")[0][0].zh_tw == "B"


def test_manual_save_rejects_empty(db):
    db.ingest("1.21.1", [item(tw="A")])
    with pytest.raises(ValueError):
        db.save_manual(db.list_entries("1.21.1")[0][0].id, "  ")


def test_manual_save_review_transition_and_idempotence(db):
    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]

    db.save_manual(entry.id, "人工修改", actor="editor", propagate=False)
    detail = db.entry_detail(entry.id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    assert manual.review_status == "unreviewed" and manual.checker == ""
    assert detail.entry.review_status == "unreviewed"

    reviewed = db.review_manual(
        entry.id, expected_zh_tw="人工修改", actor="reviewer", propagate=False
    )
    assert [row.entry_id for row in reviewed] == [entry.id]
    detail = db.entry_detail(entry.id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    assert manual.review_status == "reviewed" and manual.checker == "reviewer"
    assert detail.history[0].action == "review"

    review_gen = db._data_gen()
    assert not db.review_manual(
        entry.id, expected_zh_tw="人工修改", actor="reviewer", propagate=False
    )
    assert len(db.entry_detail(entry.id).history) == 2
    assert db._data_gen() == review_gen


def test_clear_manual_review_keeps_translation_and_can_be_reverted(db):
    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "人工修改", propagate=False)
    db.review_manual(
        entry.id, expected_zh_tw="人工修改", actor="reviewer", propagate=False
    )

    assert db.clear_manual_review(
        entry.id,
        expected_zh_tw="人工修改",
        expected_source=SRC_MANUAL,
        expected_review_status="reviewed",
        expected_checker="reviewer",
        actor="解除者",
    )
    detail = db.entry_detail(entry.id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    assert (manual.zh_tw, manual.checker, manual.review_status) == (
        "人工修改",
        "",
        "unreviewed",
    )
    assert (detail.entry.zh_tw, detail.entry.source, detail.entry.review_status) == (
        "人工修改",
        SRC_MANUAL,
        "unreviewed",
    )
    clear_history = detail.history[0]
    assert clear_history.action == "review_unmark"
    assert clear_history.actor == "解除者"

    assert db.revert(clear_history.id, whole_batch=False) == 1
    restored = db.entry_detail(entry.id)
    manual = next(row for row in restored.translations if row.source == SRC_MANUAL)
    assert (manual.zh_tw, manual.checker, manual.review_status) == (
        "人工修改",
        "reviewer",
        "reviewed",
    )


def test_clear_manual_review_rejects_stale_snapshot_without_mutation(db):
    """驗證解除審核的 CAS 失敗時不會修改狀態或新增 history。"""
    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "人工修改", propagate=False)
    db.review_manual(
        entry.id, expected_zh_tw="人工修改", actor="reviewer", propagate=False
    )
    before = db.entry_detail(entry.id)
    with pytest.raises(ValueError, match="已變動"):
        db.clear_manual_review(
            entry.id,
            expected_zh_tw="過期譯文",
            expected_source=SRC_MANUAL,
            expected_review_status="reviewed",
            expected_checker="reviewer",
        )

    after = db.entry_detail(entry.id)
    assert after.entry.review_status == "reviewed"
    assert after.entry.zh_tw == "人工修改"
    assert len(after.history) == len(before.history)
    assert after.history[0].action == "review"


def test_clear_legacy_unknown_manual_review_restores_unknown_state(db):
    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "人工修改", propagate=False)
    with db._tx(keep_review=True) as conn:
        conn.execute(
            "UPDATE translation SET review_status=NULL WHERE entry_id=? AND source=?",
            (entry.id, SRC_MANUAL),
        )
        db._refresh(conn, [entry.id])

    legacy = db.entry_detail(entry.id)
    assert legacy.entry.review_status == "legacy_unknown"
    assert db.clear_manual_review(
        entry.id,
        expected_zh_tw="人工修改",
        expected_source=SRC_MANUAL,
        expected_review_status="legacy_unknown",
        expected_checker="",
    )
    cleared = db.entry_detail(entry.id)
    assert cleared.entry.review_status == "unreviewed"
    clear_history = cleared.history[0]
    assert db.revert(clear_history.id, whole_batch=False) == 1
    restored = db.entry_detail(entry.id)
    assert restored.entry.review_status == "legacy_unknown"


def test_manual_edit_demotes_review_and_same_text_save_keeps_review(db):
    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "人工修改", propagate=False)
    db.review_manual(
        entry.id, expected_zh_tw="人工修改", actor="reviewer", propagate=False
    )

    assert db.save_manual(entry.id, "人工修改", propagate=False) == []
    assert db.entry_detail(entry.id).entry.review_status == "reviewed"
    db.save_manual(entry.id, "再次修改", propagate=False)
    detail = db.entry_detail(entry.id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    assert manual.review_status == "unreviewed" and manual.checker == ""
    edit_history = detail.history[0]
    assert edit_history.action == "manual"
    assert db.revert(edit_history.id, whole_batch=False) == 1
    restored = db.entry_detail(entry.id)
    manual = next(row for row in restored.translations if row.source == SRC_MANUAL)
    assert manual.review_status == "reviewed" and manual.checker == "reviewer"
    assert manual.zh_tw == "人工修改"


def test_review_manual_rejects_stale_text_and_revert_restores_review_state(db):
    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "人工修改", propagate=False)
    with pytest.raises(ValueError, match="已變動"):
        db.review_manual(entry.id, expected_zh_tw="過期譯文", propagate=False)

    db.review_manual(
        entry.id, expected_zh_tw="人工修改", actor="reviewer", propagate=False
    )
    review_history = db.entry_detail(entry.id).history[0]
    assert review_history.action == "review"
    assert db.revert(review_history.id, whole_batch=False) == 1
    detail = db.entry_detail(entry.id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    assert manual.review_status == "unreviewed" and manual.checker == ""


def test_review_manual_compare_and_set_rejects_source_changed_with_same_text(db):
    db.ingest("1.21.1", [item(tw="相同譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", entry.key, entry.en_us, "相同譯文")],
        source=SRC_AI,
        fill_other_versions=False,
    )
    db.set_priority((SRC_AI, SRC_JAR_TW, SRC_MANUAL))
    assert db.get_entry(entry.id).source == SRC_AI

    with pytest.raises(ValueError, match="已變動"):
        db.review_manual(
            entry.id,
            expected_zh_tw="相同譯文",
            expected_source=SRC_JAR_TW,
        )


def test_review_keeps_effective_source_and_marks_it_reviewed(db):
    db.ingest("1.21.1", [item(tw="同一譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", entry.key, entry.en_us, "同一譯文")],
        source=SRC_AI,
        fill_other_versions=False,
    )
    db.set_priority((SRC_AI, SRC_JAR_TW, SRC_MANUAL))

    db.save_manual(entry.id, "同一譯文", propagate=False)
    assert db.get_entry(entry.id).source == SRC_AI
    assert db.get_entry(entry.id).review_status == "unreviewed"

    db.review_manual(
        entry.id,
        expected_zh_tw="同一譯文",
        expected_source=SRC_AI,
        propagate=False,
    )
    # 審核不改變來源：仍是 AI，只多了「已審核」；人工列維持未審核
    assert db.get_entry(entry.id).source == SRC_AI
    assert db.get_entry(entry.id).review_status == "reviewed"
    rows = {row.source: row for row in db.entry_detail(entry.id).translations}
    assert rows[SRC_AI].review_status == "reviewed"
    assert rows[SRC_MANUAL].review_status == "unreviewed"


def test_clear_manual_review_keeps_non_manual_effective_source(db):
    db.ingest("1.21.1", [item(tw="同一譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", entry.key, entry.en_us, "同一譯文")],
        source=SRC_AI,
        fill_other_versions=False,
    )
    db.set_priority((SRC_AI, SRC_JAR_TW, SRC_MANUAL))
    db.save_manual(entry.id, "同一譯文", propagate=False)
    db.review_manual(
        entry.id,
        expected_zh_tw="同一譯文",
        expected_source=SRC_AI,
        expected_review_status="unreviewed",
        expected_checker="",
        propagate=False,
    )

    db.clear_manual_review(
        entry.id,
        expected_zh_tw="同一譯文",
        expected_source=SRC_AI,
        expected_review_status="reviewed",
        expected_checker="",
    )
    cleared = db.get_entry(entry.id)
    assert (cleared.source, cleared.review_status, cleared.zh_tw) == (
        SRC_AI,
        "unreviewed",
        "同一譯文",
    )


def test_reviewing_ai_text_leaves_non_effective_manual_alone_and_is_revertible(db):
    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", entry.key, entry.en_us, "AI 新譯文")],
        source=SRC_AI,
        fill_other_versions=False,
    )
    db.save_manual(entry.id, "舊人工譯文", actor="old-editor", propagate=False)
    db.set_priority((SRC_AI, SRC_JAR_TW, SRC_MANUAL))

    preview = db.preview_manual_review(
        entry.id,
        expected_zh_tw="AI 新譯文",
        expected_source=SRC_AI,
        expected_review_status="unreviewed",
        expected_checker="",
        propagate=False,
    )
    assert preview[0].included
    reviewed = db.review_manual(
        entry.id,
        expected_zh_tw="AI 新譯文",
        expected_source=SRC_AI,
        expected_checker="",
        expected_preview=preview,
        actor="reviewer",
        propagate=False,
    )
    assert [row.entry_id for row in reviewed] == [entry.id]
    detail = db.entry_detail(entry.id)
    rows = {row.source: row for row in detail.translations}
    assert (
        rows[SRC_AI].zh_tw == "AI 新譯文" and rows[SRC_AI].review_status == "reviewed"
    )
    assert rows[SRC_MANUAL].zh_tw == "舊人工譯文"  # 沒被動到
    assert db.get_entry(entry.id).source == SRC_AI
    history = detail.history[0]
    assert history.action == "review" and history.source_id == SRC_AI

    db.revert(history.id)
    assert db.get_entry(entry.id).review_status == "unreviewed"
    assert db.get_entry(entry.id).source == SRC_AI


def test_review_preview_shows_sync_and_skipped_sibling_reasons(db):
    db.ingest("1.21.1", [item(tw="相同譯文")])
    db.ingest("1.20.1", [item(tw="相同譯文")])
    db.ingest("1.19.2", [item(tw="另一譯文")])
    selected = db.list_entries("1.21.1")[0][0]

    preview = db.preview_manual_review(
        selected.id,
        expected_zh_tw="相同譯文",
        expected_source=SRC_JAR_TW,
        expected_review_status=None,
        propagate=True,
    )
    assert {row.mc_version for row in preview if row.included} == {
        "1.21.1",
        "1.20.1",
        "1.19.2",
    }
    different = next(row for row in preview if row.mc_version == "1.19.2")
    assert different.included and different.reason == "原文相同但目前生效譯文不同"

    local_only = db.preview_manual_review(
        selected.id,
        expected_zh_tw="相同譯文",
        expected_source=SRC_JAR_TW,
        expected_review_status=None,
        propagate=False,
    )
    sibling = next(row for row in local_only if row.mc_version == "1.20.1")
    assert not sibling.included and sibling.reason == "已選擇僅審核目前版本"


def test_review_rejects_stale_sibling_review_scope(db):
    db.ingest("1.21.1", [item(tw="相同譯文")])
    db.ingest("1.20.1", [item(tw="相同譯文")])
    selected = db.list_entries("1.21.1")[0][0]
    preview = db.preview_manual_review(
        selected.id,
        expected_zh_tw="相同譯文",
        expected_source=SRC_JAR_TW,
        expected_review_status=None,
        propagate=True,
    )
    sibling = next(
        row for row in db.list_entries("1.20.1")[0] if row.key == selected.key
    )
    db.save_manual(sibling.id, "後來修改", propagate=False)

    with pytest.raises(ValueError, match="影響範圍已變動"):
        db.review_manual(
            selected.id,
            expected_zh_tw="相同譯文",
            expected_source=SRC_JAR_TW,
            expected_preview=preview,
            propagate=True,
        )


def test_revert_rejects_aba_revision(db):
    db.ingest("1.21.1", [item(tw="原文譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "A", propagate=False)
    first = db.entry_detail(entry.id).history[0]
    db.save_manual(entry.id, "B", propagate=False)
    db.save_manual(entry.id, "A", propagate=False)

    assert db.revert(first.id, whole_batch=False) == 0
    assert (
        next(
            row
            for row in db.entry_detail(entry.id).translations
            if row.source == SRC_MANUAL
        ).zh_tw
        == "A"
    )


def test_legacy_revert_does_not_fabricate_missing_checker(db):
    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "舊文", propagate=False)
    history = db.entry_detail(entry.id).history[0]
    db._conn.execute(
        "UPDATE history SET new_revision=NULL, prev_checker=NULL, "
        "prev_review_status=NULL, new_review_status=NULL WHERE id=?",
        (history.id,),
    )
    db._conn.commit()
    db.save_manual(entry.id, "目前文", propagate=False)
    later = db.entry_detail(entry.id).history[0]
    db._conn.execute("UPDATE history SET new_revision=NULL WHERE id=?", (later.id,))
    db._conn.commit()

    assert db.revert(history.id, whole_batch=False) == 0
    manual = next(
        row
        for row in db.entry_detail(entry.id).translations
        if row.source == SRC_MANUAL
    )
    assert manual.zh_tw == "目前文" and manual.checker == ""


def test_quality_repair_invalidates_legacy_manual_revert_without_revision(db):
    db.ingest("1.21.1", [item(tw="Source")])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "Before", propagate=False)
    db.save_manual(entry.id, "Legacy manual value", propagate=False)
    history = db.entry_detail(entry.id).history[0]
    db._conn.execute(
        "UPDATE history SET prev_revision=NULL, new_revision=NULL WHERE id=?",
        (history.id,),
    )
    db._conn.execute(
        "INSERT INTO history (entry_id, batch, action, source_id) "
        "VALUES (?, 'quality-repair', 'quality_repair', ?)",
        (entry.id, SRC_MANUAL),
    )
    db._conn.commit()

    assert db.revert(history.id, whole_batch=False) == 0
    manual = next(
        row
        for row in db.entry_detail(entry.id).translations
        if row.source == SRC_MANUAL
    )
    assert manual.zh_tw == "Legacy manual value"


def test_ingest_manual_source_is_explicitly_unreviewed(db):
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "foo",
                "item.zip.manual",
                "Manual",
                "匯入人工",
                source=SRC_MANUAL,
            )
        ],
    )
    entry = next(
        row for row in db.list_entries("1.21.1")[0] if row.key == "item.zip.manual"
    )
    assert entry.review_status == "unreviewed"


def test_priority_tokens_roundtrip_builtin_custom_collision():
    from translation_tool.translation_db.settings import parse_priority
    from translation_tool.translation_db.source_catalog import SourceCatalog

    catalog = SourceCatalog.from_registry({"釘宮翻譯組": 100})
    priority = (SRC_SUBTITLE, 100)
    names = [catalog.token_for(code) for code in priority]
    assert names == ["builtin:subtitle", "custom:100"]
    assert parse_priority(names, {"釘宮翻譯組": 100})[:2] == priority


def test_batch_replace_literal_all_pages_and_manual_unreviewed(db):
    db.ingest(
        "1.21.1",
        [item(key=f"item.batch.{i:03d}", tw=f"A%_B {i}") for i in range(55)],
    )
    criteria = EntryFilter(version="1.21.1")
    first_page, total = db.list_entries(criteria=criteria, limit=50, offset=0)
    last_page, _ = db.list_entries(criteria=criteria, limit=50, offset=50)
    assert total == 55 and len(first_page) == 50 and len(last_page) == 5

    plan = db.preview_batch_replace(criteria, "%_", " literal ")
    assert plan.update_count == 55 and not plan.propagate
    result = db.execute_batch_replace(plan, actor="tester")
    assert result.updated == 55 and result.total == 55
    for entry in db.list_entries("1.21.1", limit=100)[0]:
        assert "A literal B" in entry.zh_tw
        assert entry.review_status == "unreviewed"
    assert (
        db._one(
            "SELECT COUNT(*) FROM history WHERE batch=? AND action='batch_replace'",
            (result.batch_id,),
        )[0]
        == 55
    )


def test_batch_replace_loads_skip_details_only_when_requested(db):
    from dataclasses import replace

    db.ingest(
        "1.21.1",
        [
            item(key="item.lazy.match", tw="譯文舊字"),
            item(key="item.lazy.miss", tw="沒有符合文字"),
            item(key="item.lazy.empty", tw=""),
            item(key="item.lazy.blank", tw="舊字"),
            item(key="item.lazy.unchanged", tw="完全相同"),
        ],
    )
    criteria = EntryFilter(version="1.21.1")

    plan = db.preview_batch_replace(
        criteria, "舊字", "　", confirmed_quality_worsening=True
    )

    assert plan.update_count == 1
    assert plan.skipped_count == 4
    assert plan.skipped == ()
    assert plan.skipped_details_loaded is False

    skipped = (
        *db.load_batch_replace_skipped_page(plan, offset=0, limit=2),
        *db.load_batch_replace_skipped_page(plan, offset=2, limit=2),
    )

    assert {item.key: item.reason for item in skipped} == {
        "item.lazy.empty": "沒有目前生效譯文",
        "item.lazy.miss": "找不到符合的原文片段",
        "item.lazy.blank": "替換後譯文為空白",
        "item.lazy.unchanged": "找不到符合的原文片段",
    }
    loaded_plan = replace(
        plan,
        skipped=skipped,
        skipped_count_value=None,
        skipped_details_loaded=True,
    )
    result = db.execute_batch_replace(loaded_plan)
    assert result.updated == 1 and result.skipped == 4

    unchanged_plan = db.preview_batch_replace(criteria, "相同", "相同")
    unchanged_page = db.load_batch_replace_skipped_page(unchanged_plan, limit=50)
    assert {item.key: item.reason for item in unchanged_page}[
        "item.lazy.unchanged"
    ] == ("替換後內容未改變")


def test_batch_replace_propagates_only_exact_same_effective_state(db):
    db.ingest("1.21.1", [item(key="item.same", tw="before")])
    db.ingest("1.20.1", [item(key="item.same", tw="before")])
    db.ingest("1.19.2", [item(key="item.same", tw="different")])
    selected = next(
        row for row in db.list_entries("1.21.1")[0] if row.key == "item.same"
    )
    plan = db.preview_batch_replace(
        EntryFilter(version="1.21.1"), "before", "after", propagate=True
    )
    assert {c.mc_version for c in plan.changes} == {"1.21.1", "1.20.1"}
    skipped = next(s for s in plan.skipped if s.mc_version == "1.19.2")
    assert skipped.is_extra_version and "不同" in skipped.reason
    result = db.execute_batch_replace(plan)
    assert result.updated == 2 and result.skipped == 1
    assert selected.id in {c.entry_id for c in plan.changes}
    assert (
        next(
            row for row in db.list_entries("1.19.2")[0] if row.key == "item.same"
        ).zh_tw
        == "different"
    )


def test_batch_replace_compare_and_set_and_partial_batch_revert(db):
    db.ingest(
        "1.21.1",
        [
            item(key="item.cas.one", tw="before one"),
            item(key="item.cas.two", tw="before two"),
        ],
    )
    plan = db.preview_batch_replace(EntryFilter(version="1.21.1"), "before", "after")
    first = plan.changes[0]
    db.save_manual(first.entry_id, "concurrent before edit", propagate=False)
    before = {row.id: row.zh_tw for row in db.list_entries("1.21.1", limit=100)[0]}
    with pytest.raises(ValueError, match="預覽後條目已變動"):
        db.execute_batch_replace(plan)
    assert {
        row.id: row.zh_tw for row in db.list_entries("1.21.1", limit=100)[0]
    } == before

    fresh = db.preview_batch_replace(EntryFilter(version="1.21.1"), "before", "after")
    result = db.execute_batch_replace(fresh)
    second = fresh.changes[-1]
    db.save_manual(second.entry_id, "later edit", propagate=False)
    reverted = db.revert_batch_replace(result.batch_id)
    assert reverted.reverted == 1 and reverted.skipped == 1
    assert (
        next(
            row
            for row in db.list_entries("1.21.1", limit=100)[0]
            if row.id == second.entry_id
        ).zh_tw
        == "later edit"
    )


def test_batch_replace_requires_explicit_quality_worsening_confirmation(db):
    db.ingest("1.21.1", [item(key="item.quality", en="Token", tw="原本")])
    criteria = EntryFilter(version="1.21.1", quality=QualityFilter())
    plan = db.preview_batch_replace(criteria, "原本", "%s")
    assert plan.changes[0].quality_worsened
    with pytest.raises(ValueError, match="明確確認"):
        db.execute_batch_replace(plan)
    confirmed = db.preview_batch_replace(
        criteria, "原本", "%s", confirmed_quality_worsening=True
    )
    assert db.execute_batch_replace(confirmed).updated == 1


def test_batch_replace_preview_cancellation_stops_before_write(db):
    from threading import Event

    from translation_tool.utils.cancellation import TaskCancelled, cancel_scope

    db.ingest(
        "1.21.1",
        [
            item(key=f"item.cancel.{index}", en="Source", tw="譯文舊")
            for index in range(3)
        ],
    )
    cancelled = Event()

    def report(stage: str, _progress: float) -> None:
        if stage == "查詢符合條目":
            cancelled.set()

    with cancel_scope(cancelled.is_set), pytest.raises(TaskCancelled):
        db.preview_batch_replace(
            EntryFilter(version="1.21.1"),
            "舊",
            "新",
            progress_callback=report,
        )
    assert [row.zh_tw for row in db.list_entries("1.21.1", limit=10)[0]] == [
        "譯文舊",
        "譯文舊",
        "譯文舊",
    ]


def test_batch_replace_quality_change_uses_structural_token_deltas(db):
    # 既有較小的不一致即使讓診斷句子改變，也代表改善（舊的字串比較邏輯會將此判為惡化）。
    db.ingest(
        "1.21.1",
        [item(key="item.quality.improve", en="Token", tw="錯誤%s%s")],
    )
    improved = db.preview_batch_replace(
        EntryFilter(version="1.21.1"), "%s", "", propagate=False
    )
    assert improved.changes[0].quality_worsened is False
    assert improved.changes[0].quality_improved is True

    # 再增加一個額外的佔位符代表惡化，且仍需明確指出。
    db.ingest(
        "1.21.1",
        [item(key="item.quality.worsen", en="Token", tw="錯誤%s")],
    )
    worsened = db.preview_batch_replace(
        EntryFilter(version="1.21.1"), "錯誤", "錯誤%s", propagate=False
    )
    assert worsened.changes[0].quality_worsened is True
    assert worsened.changes[0].quality_change_kind == "worsened"


def test_batch_replace_quality_mixed_and_whitespace_changes_are_structural(db):
    db.ingest(
        "1.21.1",
        [item(key="item.quality.mixed", en="Token %s", tw="錯誤\n")],
    )
    mixed = db.preview_batch_replace(
        EntryFilter(version="1.21.1"), "錯誤", "%s §a", propagate=False
    )
    change = mixed.changes[0]
    assert change.quality_worsened is True
    assert change.quality_improved is True
    assert change.quality_change_kind == "mixed"

    db.ingest(
        "1.21.1",
        [item(key="item.quality.space", en="Token", tw="原文")],
    )
    whitespace = db.preview_batch_replace(
        EntryFilter(version="1.21.1"), "原文", " 原文", propagate=False
    ).changes[0]
    assert whitespace.quality_worsened is True
    assert whitespace.quality_change_kind == "worsened"


def test_custom_date_bounds_reject_invalid_and_out_of_range_dates():
    from translation_tool.translation_db.time_filters import custom_date_bounds

    for start, end in (
        ("2026-02-30", "2026-03-01"),
        ("2026-W01-1", "2026-01-08"),
        ("0001-01-01", "0001-01-01"),
        ("9999-12-31", "9999-12-31"),
    ):
        with pytest.raises(ValueError):
            custom_date_bounds(start, end)


def test_time_bounds_are_taipei_calendar_days_and_half_open():
    from translation_tool.translation_db.time_filters import (
        format_taipei_time,
        local_date_bounds_to_utc,
        quick_date_bounds,
    )

    assert local_date_bounds_to_utc(date(2026, 10, 9), date(2026, 10, 9)) == (
        "2026-10-08 16:00:00",
        "2026-10-09 16:00:00",
    )
    assert quick_date_bounds("yesterday", now=datetime(2026, 10, 9, 1, tzinfo=UTC)) == (
        "2026-10-07 16:00:00",
        "2026-10-08 16:00:00",
    )
    assert format_taipei_time("2026-10-08 16:00:00") == "2026-10-09 00:00"


def test_time_filter_half_open_unknown_values_and_history_exists(db):
    path = db.path
    db.ingest(
        "1.21.1",
        [item(key=f"item.time.{n}", tw=f"譯文 {n}", mod="time") for n in range(3)],
    )
    db.close()
    _make_v1_db(path)
    db = TranslationDB(path)
    rows = db.list_entries("1.21.1", mod_id="time", limit=10)[0]
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET created_at=NULL WHERE entry_id=? AND source=?",
            (rows[2].id, SRC_JAR_TW),
        )
        conn.execute(
            "UPDATE translation SET created_at=? WHERE entry_id=? AND source=?",
            ("2026-10-08 16:00:00", rows[0].id, SRC_JAR_TW),
        )
        conn.execute(
            "UPDATE translation SET created_at=? WHERE entry_id=? AND source=?",
            ("2026-10-09 16:00:00", rows[1].id, SRC_JAR_TW),
        )
    range_filter = TimeFilter(
        kind="translation_created",
        start_utc="2026-10-08 16:00:00",
        end_utc="2026-10-09 16:00:00",
        unknown_policy="exclude",
    )
    selected, total = db.list_entries(
        criteria=EntryFilter(version="1.21.1", mod_id="time", time=range_filter)
    )
    assert total == 1 and [row.id for row in selected] == [rows[0].id]
    unknown, unknown_total = db.list_entries(
        criteria=EntryFilter(
            version="1.21.1",
            mod_id="time",
            time=TimeFilter(kind="translation_created", unknown_policy="only"),
        )
    )
    assert unknown_total == 1 and [row.id for row in unknown] == [rows[2].id]

    db.save_manual(rows[0].id, "人工 A", propagate=False)
    db.save_manual(rows[0].id, "人工 B", propagate=False)
    db.save_manual(rows[1].id, "人工 C", propagate=False)
    activity, activity_total = db.list_entries(
        criteria=EntryFilter(
            version="1.21.1",
            mod_id="time",
            time=TimeFilter(kind="manual_activity", action="edit"),
        )
    )
    assert activity_total == len(activity) == 2
    assert {row.id for row in activity} == {rows[0].id, rows[1].id}
    db.close()


def test_time_data_range_reports_where_the_data_actually_is(db):
    db.ingest(
        "1.21.1",
        [item(key=f"item.range.{n}", tw=f"譯文 {n}", mod="range") for n in range(2)],
    )
    rows = db.list_entries("1.21.1", mod_id="range", limit=10)[0]
    with db._tx() as conn:
        for row, stamp in zip(
            rows, ("2026-10-08 16:00:00", "2026-10-16 03:00:00"), strict=True
        ):
            conn.execute(
                "UPDATE entry SET created_at=? WHERE id=?",
                (stamp, row.id),
            )
    after_data = TimeFilter(
        kind="entry_created",
        start_utc="2026-10-16 16:00:00",
        end_utc="2026-10-19 16:00:00",
        unknown_policy="exclude",
    )
    criteria = EntryFilter(version="1.21.1", mod_id="range", time=after_data)
    assert db.list_entries(criteria=criteria)[1] == 0
    assert db.time_data_range(criteria) == (
        "2026-10-08 16:00:00",
        "2026-10-16 03:00:00",
    )
    assert db.time_data_range(EntryFilter(version="1.21.1", mod_id="range")) == (
        None,
        None,
    )
    manual = EntryFilter(
        version="1.21.1",
        mod_id="range",
        time=TimeFilter(kind="manual_activity", action="edit"),
    )
    assert db.time_data_range(manual) == (None, None)


def test_quality_filter_evaluates_before_pagination_and_uses_core_tokens(db):
    assert (
        token_issues(
            "$(t:Use f(x))a/$ %1$s {0} §a\\n\n",
            "$(t:翻譯 f(x))甲/$ %1$s {0} §a\\n\n",
        )
        == []
    )
    assert format_tokens("\\n") != format_tokens("\n")
    db.ingest(
        "1.21.1",
        [
            item(key="item.quality.good", en="Use %s", tw="使用 %s"),
            item(key="item.quality.missing", en="Use %s", tw="使用"),
            item(key="item.quality.extra", en="Use", tw="使用 %s"),
            item(key="item.quality.unknown", en="", tw="使用"),
            item(key="item.quality.empty", en="Use %s", tw=""),
        ],
    )
    criteria = EntryFilter(version="1.21.1", quality=QualityFilter(status="mismatch"))
    page, total = db.list_entries(criteria=criteria, limit=1, offset=0)
    other_page, _ = db.list_entries(criteria=criteria, limit=1, offset=1)
    assert total == 2 and len(page) == len(other_page) == 1
    assert {page[0].key, other_page[0].key} == {
        "item.quality.missing",
        "item.quality.extra",
    }
    missing_only, count = db.list_entries(
        criteria=EntryFilter(
            version="1.21.1",
            quality=QualityFilter(status="mismatch", direction="missing"),
        )
    )
    assert count == 1 and missing_only[0].key == "item.quality.missing"
    unknown, unknown_count = db.list_entries(
        criteria=EntryFilter(
            version="1.21.1", quality=QualityFilter(status="unknown_source")
        )
    )
    assert unknown_count == 1 and unknown[0].key == "item.quality.unknown"


def test_repair_newline_policy_is_scoped_and_keeps_literal_backslash_n_strict():
    assert repair_input_issues("a\nb", "甲\n乙") == (False, (), False)
    assert repair_input_issues("a\nb", "甲") == (True, (), False)
    assert repair_input_issues("a %s\nb", "甲") == (
        True,
        ("少了 1 個「%s」",),
        True,
    )
    assert repair_input_issues("Use %s\nnext", "用\n譯文") == (
        True,
        ("少了 1 個「%s」",),
        False,
    )
    assert repair_input_issues("a\r\nb", "甲\nb") == (False, (), False)
    # 現有的標記正規化器會將單獨 CR 視為一般文字，而不是 LF。
    assert repair_output_issues("a\rb", "ab") == []
    assert repair_output_issues("Use %s\nnext", "使用 %s next") == []
    assert repair_output_issues("Use %s\\nnext", "使用 %s\nnext")
    # 一般品質診斷會刻意保留精確的換行數量。
    assert "換行" in " ".join(token_issues("a\nb", "甲"))


def test_quality_repair_history_does_not_count_as_manual_activity(db):
    db.ingest(
        "1.21.1",
        [item(key="item.quality.activity", en="Source %s", tw="Source")],
    )
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, "Manual", actor="editor", propagate=False)
    manual_history = db.entry_detail(entry.id).history[0]
    db._conn.execute(
        "UPDATE history SET at='2020-01-01 00:00:00' WHERE id=?",
        (manual_history.id,),
    )
    db._conn.execute(
        "INSERT INTO history (entry_id, batch, action, at, source_id) "
        "VALUES (?, 'repair', 'quality_repair', '2099-01-01 00:00:00', 6)",
        (entry.id,),
    )
    db._conn.commit()

    assert db.get_entry(entry.id).last_manual_at == "2020-01-01 00:00:00"
    recent_manual, recent_count = db.list_entries(
        criteria=EntryFilter(
            version="1.21.1",
            time=TimeFilter(
                kind="manual_activity",
                start_utc="2090-01-01 00:00:00",
                end_utc="2100-01-01 00:00:00",
                unknown_policy="exclude",
            ),
        )
    )
    assert recent_count == 0 and recent_manual == []


@pytest.mark.parametrize(
    "sort_by",
    [
        "entry_newest",
        "entry_oldest",
        "default_desc",
        "effective_updated_newest",
        "effective_updated_oldest",
        "manual_activity_newest",
        "manual_activity_oldest",
        "default",
    ],
)
def test_quality_page_matches_full_filter_order_for_every_sort_mode(
    db, sort_by, monkeypatch
):
    from translation_tool.translation_db import repository

    monkeypatch.setattr(repository, "_QUALITY_PAGE_BUFFER_LIMIT", 2)
    db.ingest(
        "1.21.1",
        [
            item(
                mod="quality-page",
                key=f"item.quality.page.{index:02}",
                en=f"Source {index} %s",
                tw=f"Translation {index}",
            )
            for index in range(8)
        ],
    )
    rows = db.list_entries("1.21.1", mod_id="quality-page", limit=20)[0]
    for index, row in enumerate(rows):
        stamp = f"2026-01-{index + 1:02} 12:00:00"
        db._conn.execute("UPDATE entry SET created_at=? WHERE id=?", (stamp, row.id))
        db._conn.execute(
            "UPDATE translation SET updated_at=? WHERE entry_id=?", (stamp, row.id)
        )
        db._conn.execute(
            "INSERT INTO history (entry_id, batch, action, at) "
            "VALUES (?, ?, 'manual', ?)",
            (row.id, f"manual-{row.id}", stamp),
        )

    criteria = EntryFilter(
        version="1.21.1",
        mod_id="quality-page",
        quality=QualityFilter(status="mismatch"),
        sort_by=sort_by,
    )
    complete = db._query_entry_rows(criteria)
    expected = db._sort_filtered_rows(complete, sort_by)[2:5]
    page, total = db.list_entries(criteria=criteria, limit=3, offset=2)

    assert total == len(complete) == 8
    assert [row.id for row in page] == [row[0] for row in expected]


def test_quality_filter_observes_cancellation_before_query(db):
    from threading import Event

    from translation_tool.utils.cancellation import TaskCancelled, cancel_scope

    db.ingest(
        "1.21.1",
        [item(key="item.cancel.quality", en="Use %s", tw="譯文")],
    )
    cancelled = Event()
    cancelled.set()

    with cancel_scope(cancelled.is_set), pytest.raises(TaskCancelled):
        db.list_entries(
            criteria=EntryFilter(
                version="1.21.1", quality=QualityFilter(status="mismatch")
            ),
            limit=10,
        )


def test_all_sort_modes_put_nulls_last_and_stable_ids(db):
    db.ingest("1.21.1", [item(key=f"item.sort.{n}", tw=f"文字 {n}") for n in range(3)])
    rows = db.list_entries("1.21.1", limit=10)[0]
    db.save_manual(rows[0].id, "人工 A", propagate=False)
    sorted_rows, _ = db.list_entries(
        criteria=EntryFilter(version="1.21.1", sort_by="manual_activity_newest")
    )
    assert [row.id for row in sorted_rows] == [rows[0].id, rows[1].id, rows[2].id]


def test_effective_source_stats_group_codes_review_states_and_untranslated(db):
    codes = (SRC_AI, SRC_JAR_TW, SRC_JAR_CN, SRC_SUBTITLE, 4, 5, 100)
    for code in codes:
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    KIND_LANG,
                    "foo",
                    f"item.source.{code}",
                    f"Source {code}",
                    f"譯文 {code}",
                    source=code,
                )
            ],
        )

    manual_entries = []
    for suffix, status in (
        ("unreviewed", "unreviewed"),
        ("reviewed", "reviewed"),
        ("legacy", "legacy_unknown"),
    ):
        db.ingest("1.21.1", [item(key=f"item.manual.{suffix}", tw="原譯文")])
        entry = next(
            row
            for row in db.list_entries("1.21.1")[0]
            if row.key == f"item.manual.{suffix}"
        )
        db.save_manual(entry.id, f"人工 {suffix}", propagate=False)
        if status == "reviewed":
            db.review_manual(entry.id, expected_zh_tw=f"人工 {suffix}", propagate=False)
        elif status == "legacy_unknown":
            db._conn.execute(
                "UPDATE translation SET review_status='legacy_unknown' "
                "WHERE entry_id=? AND source=?",
                (entry.id, SRC_MANUAL),
            )
            db._conn.execute(
                "UPDATE effective SET review_status='legacy_unknown' WHERE entry_id=?",
                (entry.id,),
            )
            db._conn.commit()
        manual_entries.append(entry)

    db.ingest("1.21.1", [item(key="item.no.translation", tw="")])
    db.ingest("1.20.1", [item(key="item.other-version", tw="另一版譯文")])
    stats = db.effective_source_stats_by_version()
    buckets = {(row.source, row.review_status): row.count for row in stats}
    assert all(buckets[(code, "unreviewed")] == 1 for code in codes)
    assert buckets[(SRC_MANUAL, "unreviewed")] == 1
    assert buckets[(SRC_MANUAL, "reviewed")] == 1
    assert buckets[(SRC_MANUAL, "legacy_unknown")] == 1
    assert buckets[(None, None)] == 1
    totals = {}
    for row in stats:
        totals[row.mc_version] = totals.get(row.mc_version, 0) + row.count
    assert totals == {row.mc_version: row.total for row in db.version_stats()}
    assert db._version_stats_from_source_stats(stats) == db._version_stats_rows()


def test_manual_review_state_filter_matches_list_and_count(db):
    db.ingest("1.21.1", [item(tw="來源譯文"), item(key="k2", tw="另一來源譯文")])
    rows, _ = db.list_entries("1.21.1")
    db.save_manual(rows[0].id, "待審核", propagate=False)
    db.review_manual(rows[1].id, expected_zh_tw="另一來源譯文", propagate=False)

    unreviewed, unreviewed_count = db.list_entries(
        "1.21.1", source=SRC_MANUAL, review_status="unreviewed"
    )
    reviewed, reviewed_count = db.list_entries("1.21.1", review_status="reviewed")
    assert unreviewed_count == len(unreviewed) == 1
    assert reviewed_count == len(reviewed) == 1
    assert unreviewed[0].review_status == "unreviewed"
    # 審核不改來源：已審核的那筆仍是模組自帶來源
    assert reviewed[0].review_status == "reviewed" and reviewed[0].source == SRC_JAR_TW
    # 來源與審核狀態各自獨立，可以組合篩選
    combined, combined_count = db.list_entries(
        "1.21.1", source=SRC_JAR_TW, review_status="reviewed"
    )
    assert combined_count == len(combined) == 1
    _none, none_count = db.list_entries(
        "1.21.1", source=SRC_MANUAL, review_status="reviewed"
    )
    assert none_count == 0


def test_review_sync_replaces_other_versions_even_with_different_text(db):
    db.ingest("1.21.1", [item(tw="相同譯文")])
    db.ingest("1.20.1", [item(tw="相同譯文")])
    db.ingest("1.19.2", [item(tw="不同譯文")])
    selected = db.list_entries("1.21.1")[0][0]

    done = db.review_manual(
        selected.id,
        expected_zh_tw="相同譯文",
        actor="reviewer",
        propagate=True,
    )
    assert {change.mc_version for change in done} == {"1.21.1", "1.20.1", "1.19.2"}
    for version in ("1.21.1", "1.20.1", "1.19.2"):
        row = db.list_entries(version)[0][0]
        # 來源不變（仍是模組自帶），譯文與審核狀態一致
        assert row.source == SRC_JAR_TW
        assert row.review_status == "reviewed" and row.zh_tw == "相同譯文"

    # 被取代的譯文留在歷史，整批還原後 1.19.2 回到原本的來源譯文
    other = db.list_entries("1.19.2")[0][0]
    history = db.entry_detail(other.id).history[0]
    assert history.old_zh_tw == "不同譯文"
    db.revert(history.id, whole_batch=True)
    restored = db.list_entries("1.19.2")[0][0]
    assert restored.zh_tw == "不同譯文" and restored.review_status == "unreviewed"
    assert db.list_entries("1.21.1")[0][0].review_status == "unreviewed"


def _drop_stat_summary(conn):
    """舊版資料庫沒有統計摘要：移除摘要表、觸發器與 meta 標記。"""
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'stat_%'"
    ).fetchall():
        conn.execute(f"DROP TRIGGER {name}")
    conn.execute("DROP TABLE IF EXISTS stat_src")
    conn.execute("DROP TABLE IF EXISTS stat_counter")
    conn.execute(
        "DELETE FROM meta WHERE key IN ('stats_schema_version', 'stats_valid')"
    )


def _make_v1_db(path):
    from translation_tool.translation_db.schema import SRC_MANUAL

    old = TranslationDB(path)
    old.ingest("1.21.1", [item(tw="來源譯文")])
    entry = old.list_entries("1.21.1")[0][0]
    old.save_manual(entry.id, "舊人工譯文", actor="old-editor", propagate=False)
    old._conn.execute(
        "UPDATE translation SET checker='old-checker' WHERE entry_id=? AND source=?",
        (entry.id, SRC_MANUAL),
    )
    old._conn.commit()
    old.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER IF EXISTS translation_insert_timestamp_revision")
        conn.execute("DROP TRIGGER IF EXISTS translation_update_revision")
        conn.execute("DROP TRIGGER IF EXISTS translation_text_change_clears_review")
        conn.execute("DROP INDEX IF EXISTS idx_effective_source_review")
        _drop_stat_summary(conn)
        conn.execute("ALTER TABLE translation DROP COLUMN review_status")
        conn.execute("ALTER TABLE translation DROP COLUMN created_at")
        conn.execute("ALTER TABLE translation DROP COLUMN revision")
        conn.execute("ALTER TABLE effective DROP COLUMN review_status")
        for column in (
            "prev_checker",
            "prev_review_status",
            "new_checker",
            "new_review_status",
            "prev_revision",
            "new_revision",
            "source_id",
        ):
            conn.execute(f"ALTER TABLE history DROP COLUMN {column}")
        conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    return entry.id


def test_schema_v1_migration_marks_legacy_manual_unknown_and_keeps_backup(tmp_path):
    """驗證 v1 遷移標記舊人工狀態為未知並保留備份。"""
    from translation_tool.translation_db.schema import SCHEMA_VERSION

    path = tmp_path / "legacy.db"
    entry_id = _make_v1_db(path)
    db = TranslationDB(path)
    detail = db.entry_detail(entry_id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    assert SCHEMA_VERSION == 10
    assert manual.review_status == "legacy_unknown" and manual.checker == "old-checker"
    assert detail.entry.review_status == "legacy_unknown"
    assert list(tmp_path.glob("legacy.db.pre-schema-v2-*.bak"))
    assert list(tmp_path.glob("legacy.db.pre-schema-v3-*.bak"))
    assert list(tmp_path.glob("legacy.db.pre-schema-v4-*.bak"))
    assert list(tmp_path.glob("legacy.db.pre-schema-v5-*.bak"))
    assert list(tmp_path.glob("legacy.db.pre-schema-v6-*.bak"))
    assert list(tmp_path.glob("legacy.db.pre-schema-v7-*.bak"))
    assert list(tmp_path.glob("legacy.db.pre-schema-v8-*.bak"))
    assert list(tmp_path.glob("legacy.db.pre-schema-v9-*.bak"))
    assert list(tmp_path.glob("legacy.db.pre-schema-v10-*.bak"))
    assert db._q(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND name IN ('smart_review_run', 'smart_review_candidate') "
        "ORDER BY name"
    ) == [("smart_review_candidate",), ("smart_review_run",)]
    db.close()

    # 重新開啟具冪等性：不會建立第二份遷移備份。
    backups_before = sorted(tmp_path.glob("legacy.db.pre-schema-v*.bak"))
    db = TranslationDB(path)
    backups_after = sorted(tmp_path.glob("legacy.db.pre-schema-v*.bak"))
    assert backups_after == backups_before
    db.close()


def test_readonly_v1_database_reports_legacy_unknown_without_migrating(tmp_path):
    path = tmp_path / "readonly-legacy.db"
    entry_id = _make_v1_db(path)
    db = TranslationDB(path, readonly=True)
    entry = db.get_entry(entry_id)
    detail = db.entry_detail(entry_id)
    assert entry.review_status == "legacy_unknown"
    assert (
        next(
            row for row in detail.translations if row.source == SRC_MANUAL
        ).review_status
        == "legacy_unknown"
    )
    stats = db.effective_source_stats_by_version()
    assert any(
        row.source == SRC_MANUAL
        and row.review_status == "legacy_unknown"
        and row.count == 1
        for row in stats
    )
    db.close()
    with sqlite3.connect(path) as conn:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(translation)")}
    assert version == "1" and "review_status" not in columns
    assert not list(tmp_path.glob("readonly-legacy.db.pre-schema-v2-*.bak"))
    assert not list(tmp_path.glob("readonly-legacy.db.pre-schema-v3-*.bak"))


def test_schema_v2_migration_keeps_first_seen_unknown_and_supports_readonly(tmp_path):
    path = tmp_path / "legacy-v2.db"
    old = TranslationDB(path)
    old.ingest("1.21.1", [item(tw="舊譯文")])
    entry_id = old.list_entries("1.21.1")[0][0].id
    old.close()

    # 將目前的測試夾具轉換為 v2 形狀。v2 有審查狀態，但沒有首次看見時間戳或
    # 修訂版本欄位。
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER IF EXISTS translation_insert_timestamp_revision")
        conn.execute("DROP TRIGGER IF EXISTS translation_update_revision")
        conn.execute("DROP TRIGGER IF EXISTS translation_text_change_clears_review")
        conn.execute("ALTER TABLE translation DROP COLUMN created_at")
        conn.execute("ALTER TABLE translation DROP COLUMN revision")
        conn.execute("ALTER TABLE history DROP COLUMN prev_revision")
        conn.execute("ALTER TABLE history DROP COLUMN new_revision")
        conn.execute("UPDATE meta SET value='2' WHERE key='schema_version'")

    readonly = TranslationDB(path, readonly=True)
    assert readonly.get_entry(entry_id).translation_created_at is None
    assert readonly.list_entries("1.21.1")[0][0].translation_created_at is None
    readonly.close()
    assert not list(tmp_path.glob("legacy-v2.db.pre-schema-v3-*.bak"))

    migrated = TranslationDB(path)
    assert migrated.get_entry(entry_id).translation_created_at is None
    assert list(tmp_path.glob("legacy-v2.db.pre-schema-v3-*.bak"))
    migrated.save_manual(
        entry_id, "新人工譯文", actor="migration-test", propagate=False
    )
    manual = next(
        row
        for row in migrated.entry_detail(entry_id).translations
        if row.source == SRC_MANUAL
    )
    assert manual.created_at is not None
    migrated.close()


def test_schema_v3_migration_backup_includes_committed_wal_snapshot(tmp_path):
    """驗證 v3 遷移備份包含已提交的 WAL 快照。"""
    path = tmp_path / "legacy-v3-wal.db"
    db = TranslationDB(path)
    db.ingest("1.21.1", [item(key="item.wal", en="Before WAL", tw="譯文")])
    entry_id = db.list_entries("1.21.1")[0][0].id
    db.close()

    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("ALTER TABLE history DROP COLUMN source_id")
    writer.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    writer.execute("UPDATE entry SET en_us='Committed in WAL' WHERE id=?", (entry_id,))
    writer.commit()
    assert path.with_name(path.name + "-wal").exists()

    migrated = TranslationDB(path)
    assert migrated.get_entry(entry_id).en_us == "Committed in WAL"
    assert (
        migrated._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        == "10"
    )
    backups = list(tmp_path.glob("legacy-v3-wal.db.pre-schema-v4-*.bak"))
    assert len(backups) == 1
    writer.close()
    migrated.close()

    with sqlite3.connect(backups[0]) as backup:
        assert (
            backup.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0]
            == "3"
        )
        assert (
            backup.execute(
                "SELECT en_us FROM entry WHERE id=?", (entry_id,)
            ).fetchone()[0]
            == "Committed in WAL"
        )
        history_columns = {
            row[1] for row in backup.execute("PRAGMA table_info(history)")
        }
        assert "source_id" not in history_columns

    reopened = TranslationDB(path)
    assert list(tmp_path.glob("legacy-v3-wal.db.pre-schema-v4-*.bak")) == backups
    reopened.close()


def test_schema_migration_failure_rolls_back_all_review_columns(tmp_path, monkeypatch):
    from translation_tool.translation_db import schema

    path = tmp_path / "rollback-legacy.db"
    _make_v1_db(path)
    migrate = schema._migrate_review_state_v2

    def fail_after_migration(conn):
        migrate(conn)
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(schema, "_migrate_review_state_v2", fail_after_migration)
    with pytest.raises(RuntimeError, match="simulated migration failure"):
        TranslationDB(path)
    with sqlite3.connect(path) as conn:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        translation_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(translation)")
        }
        effective_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(effective)")
        }
    assert version == "1"
    assert "review_status" not in translation_columns
    assert "review_status" not in effective_columns
    assert list(tmp_path.glob("rollback-legacy.db.pre-schema-v2-*.bak"))


# -------------------------------------------------------- AI 寫回
def test_write_back_creates_entry_and_fills_blank_other_versions(db):
    db.ingest("1.20.1", [item()])  # 其他版本：相同內容、沒有譯文
    db.ingest("1.19.2", [item(tw="已有")])  # 其他版本：已有譯文，不可被覆蓋
    stats = db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "鋼製外殼")],
    )
    assert (stats.written, stats.filled_other) == (1, 1)
    assert db.list_entries("1.21.1")[0][0].zh_tw == "鋼製外殼"  # 目標版本條目自動建立
    assert db.list_entries("1.20.1")[0][0].source == SRC_AI
    assert db.list_entries("1.19.2")[0][0].zh_tw == "已有"


def test_write_back_is_insert_only(db):
    db.ingest("1.21.1", [item()])
    w = [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "第一次")]
    db.write_back("1.21.1", w)
    stats = db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "第二次")],
    )
    assert stats.written == 0 and stats.skipped == 1
    assert db.list_entries("1.21.1")[0][0].zh_tw == "第一次"


def test_write_back_skips_when_english_differs(db):
    db.ingest("1.21.1", [item(en="Old Text Here")])
    stats = db.write_back(
        "1.21.1", [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "New Text Here", "譯")]
    )
    assert stats.written == 0 and stats.skipped == 1


# ---------------------------------------------------------- 查詢
def test_list_entries_filters_and_detail(db):
    db.ingest(
        "1.21.1",
        [
            item(tw="A"),
            item(key="k2", en="Second Entry"),
            item(key="k3", en="Steel Casing", tw="B", mod="bar"),
        ],
    )
    assert db.list_entries("1.21.1", state="none")[1] == 1
    assert db.list_entries("1.21.1", mod_id="bar")[1] == 1
    assert db.list_entries("1.21.1", query="second")[1] == 1
    assert db.list_entries("1.21.1", query="second", query_field="source")[1] == 1
    assert db.list_entries("1.21.1", query="second", query_field="translation")[1] == 0
    assert db.list_entries("1.21.1", query="k2", query_field="key")[1] == 1
    assert db.list_entries("1.21.1", query="100%")[1] == 0  # % 不是萬用字元
    first = db.list_entries("1.21.1", mod_id="foo", query="item.foo.a")[0][0]
    detail = db.entry_detail(first.id)
    assert [r.key for r in detail.same_text] == ["k3"]  # 原文相同、不同模組


def test_overview_and_stats(db):
    db.ingest("1.21.1", [item(tw="A"), item(key="k2", en="Second Entry")])
    db.ingest("1.20.1", [item(tw="B")])
    ov = db.overview()
    assert ov["mods"] == 1 and ov["diff"] == 1
    stats = {s.mc_version: s for s in db.version_stats()}
    assert stats["1.21.1"].total == 2 and stats["1.21.1"].untranslated == 1
    assert db.missing_by_mod("1.21.1")[0]["missing"] == 1


# ---------------------------------------------------------- 解析器
def test_resolver_target_first_then_cross_version(db):
    db.ingest("1.21.1", [item(tw="新")])
    db.ingest("1.20.1", [item(key="only.old", tw="舊版獨有")])
    db.ingest("1.19.2", [item(key="only.old", tw="更舊")])
    r = TranslationResolver(db, "1.21.1")
    assert r.lookup(KIND_LANG, "foo", "item.foo.a", "Steel Casing").zh_tw == "新"
    hit = r.lookup(KIND_LANG, "foo", "only.old", "Steel Casing")
    assert hit.cross and hit.zh_tw == "舊版獨有"  # 取版本最接近者
    assert r.lookup(KIND_LANG, "foo", "item.foo.a", "Changed Text") is None
    assert r.stats.en_mismatch == 1
    assert (
        TranslationResolver(db, "1.21.1", cross_version=False).lookup(
            KIND_LANG, "foo", "only.old", "Steel Casing"
        )
        is None
    )


def test_split_items_by_db_uses_root_relative_identity(db, tmp_path):
    db.ingest("1.21.1", [item(tw="鋼製外殼")])
    f = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    items = [
        {
            "file": str(f),
            "path": "item.foo.a",
            "source_text": "Steel Casing",
            "text": "Steel Casing",
            "cache_type": "lang",
        },
        {
            "file": str(f),
            "path": "item.foo.zzz",
            "source_text": "Other Thing",
            "text": "Other Thing",
            "cache_type": "lang",
        },
    ]
    hits, rest = split_items_by_db(TranslationResolver(db, "1.21.1"), items, tmp_path)
    assert [h["text"] for h in hits] == ["鋼製外殼"] and len(rest) == 1
    assert split_items_by_db(None, items, tmp_path) == ([], items)


def test_write_back_buffer_flushes_translated_items(db, tmp_path):
    f = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    buf = WriteBackBuffer(db, "1.21.1", tmp_path)
    buf.add(
        {"file": str(f), "path": "item.foo.a", "source_text": "Steel Casing"},
        "鋼製外殼",
    )
    buf.add(
        {
            "file": str(tmp_path / "x" / "y.json"),
            "path": "k",
            "source_text": "ignored text",
        },
        "不適用",
    )
    stats = buf.flush()
    assert stats.written == 1 and buf.flush().written == 0


def test_write_back_buffer_resolves_advancement_identity_and_lookup(db, tmp_path):
    f = tmp_path / "data" / "foo" / "advancement" / "story" / "start.json"
    item_data = {
        "file": str(f),
        "path": "display.title.fallback",
        "source_text": "Start Here",
        "text": "從這裡開始",
        "cache_type": "advancement",
    }
    buf = WriteBackBuffer(db, "1.21.1", tmp_path)
    buf.add(item_data)
    assert buf.flush().written == 1

    hits, rest = split_items_by_db(
        TranslationResolver(db, "1.21.1"),
        [{**item_data, "text": "Start Here"}],
        tmp_path,
    )
    assert [item["text"] for item in hits] == ["從這裡開始"]
    assert rest == []


# ---------------------------------------------------------- jar 掃描
def make_jar(
    path: Path, files: dict[str, object], nested: dict[str, bytes] | None = None
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, ensure_ascii=False))
        for name, blob in (nested or {}).items():
            zf.writestr(name, blob)
    return path


def jar_bytes(files: dict[str, object]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, ensure_ascii=False))
    return buf.getvalue()


LANG_JAR = {
    "assets/foo/lang/en_us.json": {
        "item.foo.a": "Steel Casing",
        "item.foo.b": "Infused Alloy",
    },
    "assets/foo/lang/zh_tw.json": {"item.foo.a": "鋼製外殼"},
    "assets/foo/lang/zh_cn.json": {"item.foo.b": "注入合金"},
    "assets/foo/patchouli_books/guide/en_us/entries/intro.json": {
        "name": "Getting Started Guide",
        "pages": [{"type": "text", "text": "Welcome to the guide book."}],
    },
    "assets/foo/patchouli_books/guide/zh_tw/entries/intro.json": {
        "name": "入門指南",
        "pages": [{"type": "text", "text": "歡迎使用本書。"}],
    },
}


def test_scan_jar_reads_lang_and_patchouli(tmp_path):
    jar = make_jar(tmp_path / "foo.jar", LANG_JAR)
    res = scan_jar(jar, ScanOptions("1.21.1"), ("patchouli_books",))
    by_key = {i.key: i for i in res.items}
    assert by_key["item.foo.a"].zh_tw == "鋼製外殼"
    assert by_key["item.foo.b"].zh_cn == "注入合金" and by_key["item.foo.b"].zh_tw == ""
    page = by_key["patchouli_books/guide/entries/intro.json#pages[0].text"]
    assert (page.kind, page.zh_tw) == (KIND_PATCHOULI, "歡迎使用本書。")
    only_lang = scan_jar(
        jar, ScanOptions("1.21.1", include_patchouli=False), ("patchouli_books",)
    )
    assert all(i.kind == KIND_LANG for i in only_lang.items)


def test_scan_jar_recurses_into_jarjar_and_respects_option(tmp_path):
    inner = jar_bytes(
        {"assets/inner/lang/en_us.json": {"block.inner.x": "Inner Block Name"}}
    )
    jar = make_jar(
        tmp_path / "outer.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Outer Text Here"}},
        {"META-INF/jarjar/inner.jar": inner},
    )
    res = scan_jar(jar, ScanOptions("1.21.1"), ())
    assert {i.mod_id for i in res.items} == {"foo", "inner"} and res.nested_jars == 1
    off = scan_jar(jar, ScanOptions("1.21.1", scan_nested=False), ())
    assert {i.mod_id for i in off.items} == {"foo"}


def test_scan_jar_bad_archive_reports_error(tmp_path):
    bad = tmp_path / "bad.jar"
    bad.write_bytes(b"not a zip")
    res = scan_jar(bad, ScanOptions("1.21.1"), ())
    assert res.error and not res.items


def test_scan_folder_end_to_end_is_idempotent(db, tmp_path):
    make_jar(tmp_path / "foo.jar", LANG_JAR)
    make_jar(tmp_path / "empty.jar", {"readme.txt": "x"})
    (tmp_path / "broken.jar").write_bytes(b"nope")
    opts = ScanOptions("1.21.1")

    def run():
        last = None
        for upd in scan_folder_generator(db, tmp_path, opts, workers=2):
            last = upd
        return last["report"]

    first = run()
    assert first.jars_total == 3 and first.jars_with_lang == 1
    assert first.jars_without_lang == 1 and first.jars_failed == ["broken.jar"]
    assert first.stats.new_entries == 4  # 2 lang + 2 patchouli 欄位
    second = run()
    assert second.stats.new_entries == 0 and second.stats.existing == 4
    assert db.last_scans(1)[0]["mc_version"] == "1.21.1"


def test_scan_folder_can_cancel(db, tmp_path):
    for n in range(3):
        make_jar(
            tmp_path / f"m{n}.jar",
            {f"assets/m{n}/lang/en_us.json": {"a.b": "Some Text Here"}},
        )
    gen = scan_folder_generator(
        db, tmp_path, ScanOptions("1.21.1"), should_cancel=lambda: True, workers=1
    )
    report = [u for u in gen if "report" in u][-1]["report"]
    assert report.cancelled is True


def test_scan_dry_run_reads_but_never_writes(db, tmp_path):
    make_jar(tmp_path / "foo.jar", LANG_JAR)
    opts = ScanOptions("1.21.1", dry_run=True)
    last = None
    for upd in scan_folder_generator(db, tmp_path, opts, workers=1):
        last = upd
    report = last["report"]
    assert report.dry_run and report.items_found == 4 and report.stats.new_entries == 0
    assert db.count_entries() == 0 and db.last_scans() == []
    # 預覽不需要資料庫
    assert [u for u in scan_folder_generator(None, tmp_path, opts, workers=1)][-1][
        "report"
    ].items_found == 4
    with pytest.raises(ValueError):
        list(scan_folder_generator(None, tmp_path, ScanOptions("1.21.1")))


# ------------------------------------------------ 與語系合併相同的清理規則
RULES = [{"from": "存儲", "to": "儲存"}]
OVERLAPPING_RULES = [
    {"from": "苔石磚", "to": "青苔石磚"},
    {"from": "苔石", "to": "青苔鵝卵石"},
]


def test_scan_ignores_non_cjk_values_in_chinese_files(tmp_path):
    """zh_tw／zh_cn 裡只是英文（沒翻譯的複本）不算譯文，與語系合併的判斷一致。"""
    jar = make_jar(
        tmp_path / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {
                "a.b": "Steel Casing",
                "c.d": "Infused Alloy",
            },
            "assets/foo/lang/zh_tw.json": {"a.b": "Steel Casing"},
            "assets/foo/lang/zh_cn.json": {"c.d": "Infused Alloy"},
        },
    )
    items = {i.key: i for i in scan_jar(jar, ScanOptions("1.21.1"), ()).items}
    assert (items["a.b"].zh_tw, items["a.b"].zh_cn) == ("", "")
    assert (items["c.d"].zh_tw, items["c.d"].zh_cn) == ("", "")


def test_scan_applies_replace_rules_to_traditional_text(tmp_path):
    jar = make_jar(
        tmp_path / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {"a.b": "Storage Block"},
            "assets/foo/lang/zh_tw.json": {"a.b": "存儲方塊"},
        },
    )
    on = scan_jar(jar, ScanOptions("1.21.1", rules=RULES), ())
    assert on.items[0].zh_tw == "儲存方塊"
    off = scan_jar(jar, ScanOptions("1.21.1", apply_rules=False), ())
    assert off.items[0].zh_tw == "存儲方塊"


def test_scan_does_not_reapply_overlapping_rule_output(tmp_path):
    jar = make_jar(
        tmp_path / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {"a.b": "Mossy Stone Bricks"},
            "assets/foo/lang/zh_tw.json": {"a.b": "苔石磚"},
        },
    )

    result = scan_jar(jar, ScanOptions("1.21.1", rules=OVERLAPPING_RULES), ())

    assert result.items[0].zh_tw == "青苔石磚"


def test_cn_conversion_also_applies_replace_rules(db, tmp_path, monkeypatch):
    from translation_tool.translation_db import scanner

    make_jar(
        tmp_path / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {"a.b": "Storage Block"},
            "assets/foo/lang/zh_cn.json": {"a.b": "存储方块"},
        },
    )
    monkeypatch.setattr(scanner, "load_rules", lambda: RULES)
    list(scan_folder_generator(db, tmp_path, ScanOptions("1.21.1"), workers=1))
    row = db.list_entries("1.21.1")[0][0]
    assert (row.zh_tw, row.source) == ("儲存方塊", SRC_JAR_CN)  # OpenCC 轉繁後再套規則
    assert db.entry_detail(row.id).translations[0].zh_cn == "存储方块"  # 簡中原文保留


# ------------------------------------------------ 換行、前後空白、格式碼原樣保留
SPECIAL = "§a哈囉§r\n第二行 %s\\n字面換行 "  # 真換行、字面 \n、§ 格式碼、%s、結尾空白


def test_values_with_newlines_codes_and_trailing_space_are_stored_verbatim(db):
    db.ingest("1.21.1", [item(en="Hello %s\nWorld", tw=SPECIAL)])
    row = db.list_entries("1.21.1")[0][0]
    assert row.zh_tw == SPECIAL and row.en_us == "Hello %s\nWorld"


def test_manual_save_and_write_back_do_not_trim_values(db):
    db.ingest("1.21.1", [item()])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, SPECIAL)
    assert db.get_entry(entry.id).zh_tw == SPECIAL
    with pytest.raises(ValueError):
        db.save_manual(entry.id, " \n ")  # 全是空白仍然視為空

    db.write_back(
        "1.20.1",
        [WriteBackItem(KIND_LANG, "foo", "k.x", "Some English", " 前後空白\n")],
    )
    assert db.list_entries("1.20.1")[0][0].zh_tw == " 前後空白\n"


def test_resolver_returns_value_with_special_characters_intact(db):
    db.ingest("1.21.1", [item(en="Hello %s\nWorld", tw=SPECIAL)])
    hit = TranslationResolver(db, "1.21.1").lookup(
        KIND_LANG, "foo", "item.foo.a", "Hello %s\nWorld"
    )
    assert hit.zh_tw == SPECIAL


# ------------------------------------------------ 翻譯 ZIP：zh_tw 不判讀、直接匯入
TRANSLATED_ZIP = {
    "assets/foo/lang/zh_tw.json": {
        "item.foo.a": "鋼製外殼",
        "item.foo.plain": "Vanilla",  # 沒有中文也照單全收
        "item.foo.multi": "§a哈囉 %s\n第二行 ",
        "item.foo.b": "存儲方塊",  # 不套替換規則
    },
}


def run_scan(db, folder, **kw):
    opts = ScanOptions("1.21.1", **kw)
    return [u for u in scan_folder_generator(db, folder, opts, workers=1)][-1]["report"]


def test_classify_member_finds_root_anywhere_in_the_path():
    ident = classify_member("pack/1.20/assets/foo/lang/zh_tw.json")
    assert (ident.mod_id, ident.lang) == ("foo", "zh_tw")
    book = classify_member("x/data/foo/patchouli_books/guide/zh_tw/e.json")
    assert book.file_key == "patchouli_books/guide/e.json"
    assert classify_member("textures/assets/readme.txt") is None


def test_translated_zip_imports_every_zh_tw_value_without_judging(db, tmp_path):
    make_jar(tmp_path / "pack.zip", TRANSLATED_ZIP)
    report = run_scan(
        db,
        tmp_path / "pack.zip",
        translated=True,
        rules=RULES,
        translation_source=SRC_SUBTITLE,
    )
    assert report.stats.new_entries == 4 and report.stats.adopted == 0
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert rows["item.foo.plain"].zh_tw == "Vanilla"  # 無中文也匯入
    assert rows["item.foo.multi"].zh_tw == "§a哈囉 %s\n第二行 "  # 逐字
    assert rows["item.foo.b"].zh_tw == "存儲方塊"  # 不套替換規則
    assert all(r.source == SRC_SUBTITLE and r.en_us == "" for r in rows.values())


def test_zip_without_english_gets_original_text_when_jar_is_scanned_later(db, tmp_path):
    make_jar(tmp_path / "zips" / "pack.zip", TRANSLATED_ZIP)
    run_scan(db, tmp_path / "zips", translated=True)
    make_jar(
        tmp_path / "mods" / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {
                "item.foo.a": "Steel Casing",
                "item.foo.b": "Storage Block",
            }
        },
    )
    report = run_scan(db, tmp_path / "mods")
    assert report.stats.adopted == 2 and report.stats.en_changed == 0
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert (rows["item.foo.a"].en_us, rows["item.foo.a"].zh_tw) == (
        "Steel Casing",
        "鋼製外殼",
    )
    assert rows["item.foo.plain"].en_us == ""  # jar 沒有的鍵值維持原文未知
    again = run_scan(db, tmp_path / "mods")
    assert again.stats.adopted == 0  # 重掃不會重複計算


def test_zip_import_after_jar_scan_adds_a_second_source(db, tmp_path):
    make_jar(
        tmp_path / "mods" / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {"item.foo.a": "Steel Casing"},
            "assets/foo/lang/zh_tw.json": {"item.foo.a": "自帶譯名"},
        },
    )
    run_scan(db, tmp_path / "mods")
    make_jar(
        tmp_path / "pack.zip",
        {"assets/foo/lang/zh_tw.json": {"item.foo.a": "鋼製外殼"}},
    )
    report = run_scan(
        db, tmp_path / "pack.zip", translated=True, translation_source=SRC_SUBTITLE
    )
    assert report.stats.new_entries == 0 and report.stats.added_translations == 1
    row = db.list_entries("1.21.1")[0][0]
    assert (row.en_us, row.zh_tw, row.source) == (
        "Steel Casing",
        "鋼製外殼",
        SRC_SUBTITLE,
    )  # 町宮優先於自帶


def test_zip_with_both_languages_pairs_them_and_scans_jars_inside(db, tmp_path):
    inner = jar_bytes({"assets/inner/lang/zh_tw.json": {"k.a": "內層譯文"}})
    make_jar(
        tmp_path / "pack.zip",
        {
            "assets/foo/lang/en_us.json": {"item.foo.a": "Steel Casing"},
            "assets/foo/lang/zh_tw.json": {"item.foo.a": "鋼製外殼"},
        },
        {"libs/inner-mod.jar": inner},  # 任何位置的 jar 都掃
    )
    report = run_scan(db, tmp_path / "pack.zip", translated=True)
    rows = {(r.mod_id, r.key): r for r in db.list_entries("1.21.1")[0]}
    assert rows[("foo", "item.foo.a")].en_us == "Steel Casing"
    assert rows[("inner", "k.a")].zh_tw == "內層譯文" and report.nested_jars == 1


def test_clean_english_switch_controls_filtering_of_english_values(tmp_path):
    jar = make_jar(
        tmp_path / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {
                "a.real": "Steel Casing Block",
                "a.short": "ab",
                "a.id": "minecraft:stone",
            }
        },
    )
    on = {i.key for i in scan_jar(jar, ScanOptions("1.21.1"), ()).items}
    off = {
        i.key
        for i in scan_jar(jar, ScanOptions("1.21.1", clean_english=False), ()).items
    }
    assert on == {"a.real"}  # 與機器翻譯相同的判斷：略過過短字串與技術 ID
    assert off == {"a.real", "a.short"}  # 關閉：放寬文字篩選，仍排除技術 ID


def test_translated_patchouli_uses_field_names_not_content(db, tmp_path):
    make_jar(
        tmp_path / "pack.zip",
        {
            "assets/foo/patchouli_books/guide/zh_tw/entries/a.json": {
                "name": "入門",
                "category": "patchouli:basics",  # 資源引用不是文字
                "pages": [{"type": "text", "text": "歡迎。"}],
            }
        },
    )
    run_scan(db, tmp_path / "pack.zip", translated=True)
    keys = {r.key.split("#")[1]: r.zh_tw for r in db.list_entries("1.21.1")[0]}
    assert keys == {
        "name": "入門",
        "pages[0].text": "歡迎。",
    }  # type／category 這類結構欄位不收


def test_unknown_english_entries_never_sync_or_look_different(db):
    db.ingest("1.21.1", [item(en="", tw="甲")])
    db.ingest("1.20.1", [item(en="", tw="乙")])
    entry = db.list_entries("1.21.1")[0][0]
    assert entry.diff is False  # 沒有原文就沒有「相同內容」可比
    assert [i.mc_version for i in db.preview_manual(entry.id, "丙")] == ["1.21.1"]
    db.save_manual(entry.id, "丙")
    assert db.list_entries("1.20.1")[0][0].zh_tw == "乙"
    detail = db.entry_detail(entry.id)
    assert detail.same_text == [] and detail.versions == ["1.21.1"]
    assert db.overview()["no_source"] == 2 and db.overview()["diff"] == 0


def test_find_jars_accepts_a_single_file_and_zip_folders(tmp_path):
    from translation_tool.translation_db.scanner import find_jars

    make_jar(tmp_path / "a.zip", {"x.txt": "1"})
    make_jar(tmp_path / "sub" / "b.jar", {"x.txt": "1"})
    assert find_jars(tmp_path / "a.zip", translated=True) == [tmp_path / "a.zip"]
    assert [p.name for p in find_jars(tmp_path, translated=True)] == ["a.zip", "b.jar"]
    assert [p.name for p in find_jars(tmp_path)] == ["b.jar"]


# ------------------------------------------------ 內嵌 jar 的讀取要計入 archive 的累計預算
def test_nested_jars_are_charged_to_the_archive_budget(tmp_path, monkeypatch):
    from translation_tool.translation_db import scanner
    from translation_tool.utils import zip_safety

    inner = {
        f"META-INF/jarjar/lib{i}.jar": jar_bytes(
            {f"assets/m{i}/lang/en_us.json": {"a.b": "Some Text Here"}}
        )
        for i in range(5)
    }
    jar = make_jar(
        tmp_path / "big.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Outer Text Here"}},
        inner,
    )

    ok = scan_jar(jar, ScanOptions("1.21.1"), ())
    assert ok.nested_jars == 5 and not ok.error  # 預算足夠時照常掃描

    monkeypatch.setattr(
        scanner,
        "ZipReadBudget",
        lambda label="": zip_safety.ZipReadBudget(10_000, 3, label),
    )

    limited = scan_jar(jar, ScanOptions("1.21.1"), ())
    assert (
        limited.error and "累計" in limited.error
    )  # 超過成員數上限：回報並停止，不會無限讀下去
    assert limited.nested_jars < 5


# ------------------------------------------------ 審查 #168：外部 SQLite、部分寫入、遞迴預算、取消
from translation_tool.translation_db import DbSettings, open_db  # noqa: E402
from translation_tool.utils import zip_safety  # noqa: E402


def deflated_jar(
    files: dict[str, object], nested: dict[str, bytes] | None = None
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, ensure_ascii=False))
        for name, blob in (nested or {}).items():
            zf.writestr(name, blob)
    return buf.getvalue()


def test_foreign_sqlite_is_never_initialised_or_modified(tmp_path):
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE users (id INTEGER, meta TEXT)")
    conn.commit()
    conn.close()
    before = other.read_bytes()
    for create in (True, False):
        with pytest.raises(ValueError, match="不是 Mod 翻譯資料庫"):
            TranslationDB(other, create=create)
    with pytest.raises(ValueError):
        TranslationDB(other, readonly=True)
    assert (
        other.read_bytes() == before
    )  # 一個位元組都沒動（沒有 DDL、沒有改 journal mode）
    check = sqlite3.connect(other)
    assert [r[0] for r in check.execute("SELECT name FROM sqlite_master")] == ["users"]
    assert check.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
    check.close()
    # 設定指到其他 SQLite：功能停用（回傳 None），不拋出也不修改
    assert open_db(DbSettings(path=str(other)), create=True) is None
    assert open_db(DbSettings(path=str(other)), create=False) is None
    assert other.read_bytes() == before


def test_lookalike_database_without_our_schema_is_rejected(tmp_path):
    odd = tmp_path / "odd.db"
    conn = sqlite3.connect(odd)  # 有 meta 與 entry 但欄位、schema_version 都不是我們的
    conn.executescript("CREATE TABLE meta (k TEXT); CREATE TABLE entry (x INTEGER);")
    conn.close()
    with pytest.raises(ValueError):
        TranslationDB(odd)


def test_non_sqlite_and_empty_files(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database" * 20)
    with pytest.raises(ValueError):
        TranslationDB(junk)
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="尚未初始化"):
        TranslationDB(empty, create=False)  # 翻譯流程不會把空檔案初始化
    db = TranslationDB(empty)  # 掃描／介面 (create=True) 可以
    assert db.count_entries() == 0
    db.close()


def test_database_from_a_newer_version_is_refused_without_touching_it(tmp_path):
    """太新的資料庫在第一次可寫連線之前就拒絕：位元組不變、日誌模式不變、不產生 -wal/-shm。"""
    path = tmp_path / "new.db"
    TranslationDB(path).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    for create in (True, False):
        with pytest.raises(ValueError, match="版本較新"):
            TranslationDB(path, create=create)
    assert path.read_bytes() == before
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    conn.close()
    assert not (tmp_path / "new.db-wal").exists()
    assert not (tmp_path / "new.db-shm").exists()


def test_archive_that_hits_a_safety_limit_commits_nothing(db, tmp_path, monkeypatch):
    """前面的語言檔讀成功、後面才超過上限：整個壓縮檔失敗，不寫入部分資料，也不算成功。"""
    from translation_tool.translation_db import scanner

    make_jar(
        tmp_path / "bomb.jar",
        {
            "assets/aaa/lang/en_us.json": {"a.b": "First mod text"},
            "assets/bbb/lang/en_us.json": {"a.b": "Second mod text"},
            "assets/ccc/lang/en_us.json": {"a.b": "Third mod text"},
        },
    )
    make_jar(
        tmp_path / "good.jar",
        {"assets/ok/lang/en_us.json": {"a.b": "Healthy mod text"}},
    )
    real = zip_safety.ZipReadBudget

    def budget(options, label):
        return real(
            max_bytes=10_000_000,
            max_members=2 if label == "bomb.jar" else 100,
            label=label,
        )

    monkeypatch.setattr(scanner, "_new_budget", budget)
    monkeypatch.setattr(scanner, "_new_tree_budget", lambda o, label: real(label=label))

    res = scan_jar(tmp_path / "bomb.jar", ScanOptions("1.21.1"), ())
    assert res.error and res.items == []  # 契約：error → 清空 items

    report = run_scan(db, tmp_path)
    assert report.jars_failed == ["bomb.jar"] and report.jars_with_lang == 1
    assert {r.mod_id for r in db.list_entries("1.21.1")[0]} == {
        "ok"
    }  # bomb.jar 一筆都沒進資料庫


def test_oversized_nested_jar_is_skipped_but_counted_not_silent(tmp_path, monkeypatch):
    from translation_tool.translation_db import scanner

    inner = jar_bytes({"assets/inner/lang/en_us.json": {"k.a": "Inner text here"}})
    jar = make_jar(
        tmp_path / "outer.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Outer text here"}},
        {"META-INF/jarjar/big.jar": inner, "META-INF/jarjar/bad.jar": b"not a zip"},
    )
    monkeypatch.setattr(scanner, "MAX_FILE_BYTES", 10)  # 讓 big.jar 超過單檔上限
    res = scan_jar(jar, ScanOptions("1.21.1"), ())
    assert (
        not res.error and res.skipped_nested == 2
    )  # 外層內容保留，略過的內嵌 jar 有記錄
    assert {i.mod_id for i in res.items} == {"foo"}


def test_nested_archives_share_a_tree_wide_budget(tmp_path, monkeypatch):
    """每個內嵌 jar 的二進位資料很小（高壓縮），但解壓後加總超過整棵樹的預算 → 整包失敗。"""
    from translation_tool.translation_db import scanner

    text = "word " * 30_000  # 約 150KB，壓縮後很小
    inner = {
        f"META-INF/jarjar/lib{i}.jar": deflated_jar(
            {f"assets/m{i}/lang/en_us.json": {"k.a": text}}
        )
        for i in range(4)
    }
    jar = tmp_path / "tree.jar"
    with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "assets/foo/lang/en_us.json", json.dumps({"a.b": "Outer text here"})
        )
        for name, blob in inner.items():
            zf.writestr(name, blob)

    ok = scan_jar(jar, ScanOptions("1.21.1"), ())
    assert not ok.error and ok.nested_jars == 4  # 預設預算：全部讀得到

    real = zip_safety.ZipReadBudget
    monkeypatch.setattr(
        scanner,
        "_new_tree_budget",
        lambda o, label: real(max_bytes=300_000, label=label),
    )
    limited = scan_jar(jar, ScanOptions("1.21.1"), ())
    assert limited.error and "累計" in limited.error
    assert limited.items == [] and limited.nested_jars < 4  # 超過就停止，不寫部分資料


def test_cancel_stops_inside_a_large_member_without_finishing_it(tmp_path):
    big = "word " * 2_000_000  # 約 10MB 的單一語言檔，壓縮後很小
    jar = tmp_path / "huge.jar"
    with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("assets/foo/lang/en_us.json", json.dumps({"a.b": big}))
    checks: list[int] = []

    def cancel() -> bool:
        checks.append(1)
        return len(checks) > 3  # 讀了幾個區塊後取消

    res = scan_jar(jar, ScanOptions("1.21.1"), (), should_cancel=cancel)
    assert res.cancelled and res.items == []
    assert len(checks) < 20  # 10MB 以 64KB 分塊約 160 次；取消後立刻停止，而不是讀完


def test_cancel_with_running_workers_returns_promptly_and_writes_nothing(db, tmp_path):
    import time

    big = "word " * 1_000_000
    for n in range(4):
        with zipfile.ZipFile(tmp_path / f"m{n}.jar", "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"assets/m{n}/lang/en_us.json", json.dumps({"a.b": big}))
    started = time.monotonic()
    gen = scan_folder_generator(
        db, tmp_path, ScanOptions("1.21.1"), should_cancel=lambda: True, workers=4
    )
    report = [u for u in gen if "report" in u][-1]["report"]
    assert report.cancelled and db.count_entries() == 0
    assert time.monotonic() - started < 5


# ------------------------------------------------------------------ log：UI 與後台一致、錯誤訊息明確
def test_skipped_nested_jar_is_named_in_the_scan_log(db, tmp_path):
    make_jar(
        tmp_path / "m" / "outer.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Outer Text Here"}},
        nested={"META-INF/jarjar/broken.jar": b"this is not a zip"},
    )
    updates = list(scan_folder_generator(db, tmp_path / "m", ScanOptions("1.21.1")))
    line = next(u for u in updates if "outer.jar" in u.get("log", ""))
    assert line["level"] == "warning"
    assert "broken.jar" in line["log"] and "不是有效的 zip" in line["log"]


def test_failed_archive_and_summary_are_flagged_as_warnings(db, tmp_path):
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "bad.jar").write_bytes(b"not a zip")
    updates = list(scan_folder_generator(db, tmp_path / "m", ScanOptions("1.21.1")))
    assert any(u.get("level") == "error" and "bad.jar" in u["log"] for u in updates)
    final = updates[-1]
    assert final["level"] == "warning" and "失敗 1 個" in final["log"]


def test_ingest_failure_names_the_jar_version_and_database(db, tmp_path, monkeypatch):
    make_jar(
        tmp_path / "m" / "foo.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Some Text Here"}},
    )

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "ingest_many", boom)
    with pytest.raises(RuntimeError) as err:
        list(scan_folder_generator(db, tmp_path / "m", ScanOptions("1.21.1")))
    msg = str(err.value)
    assert "foo.jar" in msg and "1.21.1" in msg and "database is locked" in msg


def test_missing_folder_message_hints_what_to_check(db, tmp_path):
    (tmp_path / "empty").mkdir()
    first = next(scan_folder_generator(db, tmp_path / "empty", ScanOptions("1.21.1")))
    assert first["level"] == "warning" and "請確認路徑" in first["log"]


def test_database_problem_explains_unusable_files(tmp_path):
    from translation_tool.translation_db.settings import DbSettings, database_problem

    missing = DbSettings(path=str(tmp_path / "none.db"))
    assert database_problem(missing) == ""  # 尚未建立不算問題

    foreign = tmp_path / "other.db"
    c = sqlite3.connect(foreign)
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()
    assert "不是 Mod 翻譯資料庫" in database_problem(DbSettings(path=str(foreign)))

    newer = tmp_path / "newer.db"
    TranslationDB(newer).close()
    c = sqlite3.connect(newer)
    c.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    c.commit()
    c.close()
    assert "比本程式新" in database_problem(DbSettings(path=str(newer)))


def test_scan_jar_ignores_book_translations_that_do_not_match_en_us(tmp_path):
    """頁數不同的手冊譯文以路徑對應會錯位，不能匯入；格式一致的照常匯入。"""
    book = "assets/foo/patchouli_books/guide"
    jar = make_jar(
        tmp_path / "foo.jar",
        {
            f"{book}/en_us/entries/stale.json": {
                "name": "Modes",
                "pages": [
                    {"type": "text", "text": "First page text here."},
                    {"type": "text", "text": "Second page text here."},
                ],
            },
            f"{book}/zh_cn/entries/stale.json": {
                "name": "模式",
                "pages": [{"type": "text", "text": "只有一頁的舊譯文。"}],
            },
            f"{book}/en_us/entries/ok.json": {
                "name": "Intro",
                "pages": [{"type": "text", "text": "Welcome to the book."}],
            },
            f"{book}/zh_tw/entries/ok.json": {
                "name": "簡介",
                "pages": [{"type": "text", "text": "歡迎使用本書。"}],
            },
        },
    )
    res = scan_jar(jar, ScanOptions("1.21.1"), ("patchouli_books",))
    by_key = {i.key: i for i in res.items}
    stale = [i for k, i in by_key.items() if "stale.json" in k]
    assert stale and all(i.zh_cn == "" and i.zh_tw == "" for i in stale)
    ok = by_key["patchouli_books/guide/entries/ok.json#pages[0].text"]
    assert ok.zh_tw == "歡迎使用本書。"


def test_text_change_on_reviewed_non_manual_row_clears_review(db):
    """非人工來源的譯文被改寫（例如重新匯入）時，舊的「已審核」不能沿用到新譯文。"""
    db.ingest("1.21.1", [item(tw="舊譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.review_manual(entry.id, expected_zh_tw="舊譯文", propagate=False)
    assert db.get_entry(entry.id).review_status == "reviewed"

    db._conn.execute(
        "UPDATE translation SET zh_tw='新譯文' WHERE entry_id=? AND source=?",
        (entry.id, SRC_JAR_TW),
    )
    db._conn.commit()
    row = db._conn.execute(
        "SELECT review_status FROM translation WHERE entry_id=? AND source=?",
        (entry.id, SRC_JAR_TW),
    ).fetchone()
    assert row[0] is None


def test_schema_v6_moves_only_review_created_manual_rows_back(tmp_path):
    from translation_tool.translation_db.schema import SRC_AI, SRC_JAR_TW, SRC_MANUAL

    path = tmp_path / "v5.db"
    old = TranslationDB(path)
    old.ingest(
        "1.21.1",
        [
            item(key="k.same", tw="同文"),
            item(key="k.hand", tw="手改文"),
            item(key="k.nohist", tw="無紀錄文"),
            item(key="k.edited", tw="來源文"),
            item(key="k.batch", tw="批次文"),
        ],
    )
    rows = {row.key: row for row in old.list_entries("1.21.1")[0]}
    old.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", "k.same", rows["k.same"].en_us, "同文")],
        source=SRC_AI,
        fill_other_versions=False,
    )
    old.set_priority((SRC_MANUAL, SRC_AI, SRC_JAR_TW))
    conn = old._conn
    # 舊版審核留下的資料：人工列已審核；歷史分別是「審核建立」「手動儲存過」「沒有紀錄」
    manual_text = {
        "k.same": "同文",
        "k.hand": "手改文",
        "k.nohist": "無紀錄文",
        "k.edited": "人工改過",
        "k.batch": "批次文",
    }
    for key, text in manual_text.items():
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
            "VALUES (?,?,?,?, 'reviewed')",
            (rows[key].id, SRC_MANUAL, text, "reviewer"),
        )
    revisions = {
        key: conn.execute(
            "SELECT revision FROM translation WHERE entry_id=? AND source=?",
            (rows[key].id, SRC_MANUAL),
        ).fetchone()[0]
        for key in manual_text
    }
    for key, action in (
        ("k.same", "review"),
        ("k.hand", "manual"),
        ("k.hand", "review"),
        ("k.batch", "review"),
        ("k.batch", "batch_replace"),
        ("k.edited", "review"),
    ):
        conn.execute(
            "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, "
            "new_zh_tw, prev_manual, new_revision) VALUES (?,?,?,?,?,?,NULL,?)",
            (
                rows[key].id,
                "b",
                "reviewer",
                action,
                "",
                manual_text[key],
                revisions[key],
            ),
        )
    conn.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
    conn.commit()
    old.close()

    db = TranslationDB(path)
    by_key = {r.key: r for r in db.list_entries("1.21.1")[0]}
    # 審核建立、文字相同：移回使用者優先序中較前的來源（AI 在 JAR_TW 前面）
    assert (by_key["k.same"].source, by_key["k.same"].review_status) == (
        SRC_AI,
        "reviewed",
    )
    assert not any(
        t.source == SRC_MANUAL
        for t in db.entry_detail(by_key["k.same"].id).translations
    )
    assert db.entry_detail(by_key["k.same"].id).history[0].action == "review_migrate"
    # 手動儲存過、批次取代過、沒有紀錄、文字不同：維持人工來源
    for key in ("k.hand", "k.nohist", "k.edited", "k.batch"):
        assert (by_key[key].source, by_key[key].review_status) == (
            SRC_MANUAL,
            "reviewed",
        )
    assert list(tmp_path.glob("v5.db.pre-schema-v6-*.bak"))
    db.close()


def test_reviewed_filter_includes_legacy_unknown_rows(db):
    db.ingest("1.21.1", [item(key="a", tw="甲"), item(key="b", tw="乙")])
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    db.save_manual(rows["a"].id, "甲甲", propagate=False)
    db._conn.execute(
        "UPDATE translation SET review_status='legacy_unknown' "
        "WHERE entry_id=? AND source=?",
        (rows["a"].id, SRC_MANUAL),
    )
    db._conn.execute(
        "UPDATE effective SET review_status='legacy_unknown' WHERE entry_id=?",
        (rows["a"].id,),
    )
    db._conn.commit()
    db.review_manual(rows["b"].id, expected_zh_tw="乙", propagate=False)

    found, total = db.list_entries("1.21.1", review_status="reviewed")
    assert total == 2 and {r.key for r in found} == {"a", "b"}


def test_simplified_only_translation_keeps_the_chosen_source(db):
    from translation_tool.translation_db.schema import SRC_SUBTITLE

    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG, "foo", "k", "", "", zh_cn="简体文字", source=SRC_SUBTITLE
            ),
            ScanItem(KIND_LANG, "foo", "plain", "", "", zh_cn="简体"),
        ],
        convert=lambda text: text.replace("简", "簡"),
    )
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert rows["k"].source == SRC_SUBTITLE and rows["k"].zh_tw == "簡体文字"
    assert rows["plain"].source == SRC_JAR_CN  # 沒有指定來源時維持簡中轉繁


def test_manual_edit_after_review_takes_effect_and_revert_restores_review(db):
    db.ingest("1.21.1", [item(tw="舊譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.review_manual(entry.id, expected_zh_tw="舊譯文", propagate=False)
    assert db.get_entry(entry.id).review_status == "reviewed"

    db.save_manual(entry.id, "新譯文", propagate=False)
    now = db.get_entry(entry.id)
    assert (now.zh_tw, now.source, now.review_status) == (
        "新譯文",
        SRC_MANUAL,
        "unreviewed",
    )
    # 被取代的來源保留譯文，已審核標記退回
    jar = next(
        t for t in db.entry_detail(entry.id).translations if t.source == SRC_JAR_TW
    )
    assert jar.zh_tw == "舊譯文" and jar.review_status != "reviewed"

    # 重新開啟、重建生效譯文後結果一致
    path = db.path
    db.close()
    reopened = TranslationDB(path)
    reopened._rebuild_effective(reopened._conn)
    reopened._conn.commit()
    again = reopened.get_entry(entry.id)
    assert (again.zh_tw, again.source) == ("新譯文", SRC_MANUAL)

    # 還原手動儲存：舊譯文與已審核標記一起回來
    manual_history = next(
        h for h in reopened.entry_detail(entry.id).history if h.action == "manual"
    )
    reopened.revert(manual_history.id)
    back = reopened.get_entry(entry.id)
    assert (back.zh_tw, back.source, back.review_status) == (
        "舊譯文",
        SRC_JAR_TW,
        "reviewed",
    )
    reopened.close()


def test_manual_edit_after_review_propagates_and_reverts_as_one_batch(db):
    for version in ("1.21.1", "1.20.1"):
        db.ingest(version, [item(tw="舊譯文")])
    selected = db.list_entries("1.21.1")[0][0]
    db.review_manual(selected.id, expected_zh_tw="舊譯文", propagate=True)

    db.save_manual(selected.id, "新譯文", propagate=True)
    for version in ("1.21.1", "1.20.1"):
        row = db.list_entries(version)[0][0]
        assert (row.zh_tw, row.source) == ("新譯文", SRC_MANUAL)

    first = next(
        h for h in db.entry_detail(selected.id).history if h.action == "manual"
    )
    db.revert(first.id, whole_batch=True)
    for version in ("1.21.1", "1.20.1"):
        row = db.list_entries(version)[0][0]
        assert (row.zh_tw, row.source, row.review_status) == (
            "舊譯文",
            SRC_JAR_TW,
            "reviewed",
        )


def test_batch_replace_after_review_takes_effect_and_reverts(db):
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", "k", "Hell", "地獄", "", source=SRC_AI)],
    )
    entry = db.list_entries("1.21.1")[0][0]
    db.review_manual(entry.id, expected_zh_tw="地獄", propagate=False)

    plan = db.preview_batch_replace(EntryFilter(version="1.21.1"), "地獄", "下界")
    result = db.execute_batch_replace(plan)
    assert result.updated == 1
    now = db.get_entry(entry.id)
    assert (now.zh_tw, now.source) == ("下界", SRC_MANUAL)

    db.revert_batch_replace(result.batch_id)
    back = db.get_entry(entry.id)
    assert (back.zh_tw, back.source, back.review_status) == (
        "地獄",
        SRC_AI,
        "reviewed",
    )


def test_saving_text_the_manual_row_already_has_switches_the_effective_source(db):
    from translation_tool.translation_db.schema import SRC_AI

    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", entry.key, entry.en_us, "甲")],
        source=SRC_AI,
        fill_other_versions=False,
    )
    # 人工列「乙」未審核；生效的是已審核的 AI「甲」
    db.save_manual(entry.id, "乙", propagate=False)
    db.review_manual(
        entry.id, expected_zh_tw="乙", expected_source=SRC_MANUAL, propagate=False
    )
    db._conn.execute(
        "UPDATE translation SET review_status='unreviewed' WHERE entry_id=? AND source=?",
        (entry.id, SRC_MANUAL),
    )
    db._conn.execute(
        "UPDATE translation SET review_status='reviewed' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._rebuild_effective(db._conn)
    db._conn.commit()
    shown = db.get_entry(entry.id)
    assert (shown.zh_tw, shown.source, shown.review_status) == (
        "甲",
        SRC_AI,
        "reviewed",
    )

    # 人工列已經是「乙」，使用者再儲存一次「乙」：必須切換到人工
    assert db.save_manual(entry.id, "乙", propagate=False)
    now = db.get_entry(entry.id)
    assert (now.zh_tw, now.source) == ("乙", SRC_MANUAL)

    # 還原：回到原本生效的已審核來源
    history = next(h for h in db.entry_detail(entry.id).history if h.action == "manual")
    db.revert(history.id)
    back = db.get_entry(entry.id)
    assert (back.zh_tw, back.source, back.review_status) == ("甲", SRC_AI, "reviewed")


def test_switch_only_save_cannot_be_bypassed_by_reverting_an_older_save(db):
    from translation_tool.translation_db.schema import SRC_AI

    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", entry.key, entry.en_us, "甲")],
        source=SRC_AI,
        fill_other_versions=False,
    )
    db.save_manual(entry.id, "乙", propagate=False)  # 較舊的手動儲存
    older = next(h for h in db.entry_detail(entry.id).history if h.action == "manual")
    # 之後 AI「甲」成為已審核且生效
    db._conn.execute(
        "UPDATE translation SET review_status='reviewed' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._rebuild_effective(db._conn)
    db._conn.commit()
    assert db.get_entry(entry.id).source == SRC_AI
    # 再儲存同一段「乙」：切換回人工
    assert db.save_manual(entry.id, "乙", propagate=False)
    assert db.get_entry(entry.id).source == SRC_MANUAL

    # 更早的歷史紀錄不能越過這次切換
    assert db.revert(older.id) == 0
    now = db.get_entry(entry.id)
    assert (now.zh_tw, now.source) == ("乙", SRC_MANUAL)


def test_custom_priority_above_manual_still_wins_after_save(db):
    from translation_tool.translation_db.schema import SRC_AI

    db.ingest("1.21.1", [item(tw="來源譯文")])
    entry = db.list_entries("1.21.1")[0][0]
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", entry.key, entry.en_us, "甲")],
        source=SRC_AI,
        fill_other_versions=False,
    )
    db.set_priority((SRC_AI, SRC_MANUAL, SRC_JAR_TW))
    db.save_manual(entry.id, "乙", propagate=False)
    shown = db.get_entry(entry.id)
    # 使用者自訂的來源優先序高於人工：人工譯文已存入，但生效仍是 AI（畫面會提示）
    assert (shown.zh_tw, shown.source) == ("甲", SRC_AI)
    rows = {t.source: t.zh_tw for t in db.entry_detail(entry.id).translations}
    assert rows[SRC_MANUAL] == "乙"


def test_batch_replace_reports_entries_shadowed_by_custom_priority(db):
    from translation_tool.translation_db.schema import SRC_AI

    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "k.a", "Hell", "地獄", "", source=SRC_AI),
            ScanItem(KIND_LANG, "foo", "k.b", "Hell2", "地獄二", "", source=SRC_AI),
        ],
    )
    db.set_priority((SRC_AI, SRC_MANUAL, SRC_JAR_TW))
    plan = db.preview_batch_replace(EntryFilter(version="1.21.1"), "地獄", "下界")
    result = db.execute_batch_replace(plan)
    # 兩筆都寫入人工來源，但優先序讓 AI 仍然生效
    assert (result.updated, result.shadowed) == (2, 2)

    db.set_priority((SRC_MANUAL, SRC_AI, SRC_JAR_TW))
    plan = db.preview_batch_replace(EntryFilter(version="1.21.1"), "下界", "冥界")
    result = db.execute_batch_replace(plan)
    assert (result.updated, result.shadowed) == (2, 0)


def test_manual_save_keeps_review_of_source_that_outranks_manual(db):
    from translation_tool.translation_db.schema import SRC_JAR_TW

    db.ingest("1.21.1", [ScanItem(KIND_LANG, "foo", "k", "Hell", "甲", source=SRC_AI)])
    entry = db.list_entries("1.21.1")[0][0]
    db.set_priority((SRC_AI, SRC_MANUAL, SRC_JAR_TW))
    db._conn.execute(
        "UPDATE translation SET review_status='reviewed' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._rebuild_effective(db._conn)
    db._conn.commit()
    assert db.get_entry(entry.id).review_status == "reviewed"

    db.save_manual(entry.id, "乙", propagate=False)

    row = db.get_entry(entry.id)
    assert row.zh_tw == "甲" and row.source == SRC_AI
    assert row.review_status == "reviewed"  # 退回也換不到人工生效，不丟審核標記

    db.set_priority((SRC_MANUAL, SRC_AI, SRC_JAR_TW))
    db.save_manual(entry.id, "丙", propagate=False)
    assert db.get_entry(entry.id).zh_tw == "丙"


def _reviewed_ai_entry(db, en, zh):
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "foo", "k", en, zh, source=SRC_AI)])
    entry = db.list_entries("1.21.1")[0][0]
    db._conn.execute(
        "UPDATE translation SET review_status='reviewed' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._rebuild_effective(db._conn)
    db._conn.commit()
    return entry


def _ai_row(db, entry_id):
    return db._conn.execute(
        "SELECT zh_tw, review_status FROM translation WHERE entry_id=? AND source=?",
        (entry_id, SRC_AI),
    ).fetchone()


_REPAIR_IDENTITY = {
    "expected_version": "1.21.1",
    "expected_kind": KIND_LANG,
    "expected_mod_id": "foo",
    "expected_key": "k",
    "expected_en_us": "Use %s",
    "expected_revision": None,
}


def test_automatic_repair_clears_review_and_history_can_revert(db):
    entry = _reviewed_ai_entry(db, "Use %s", "使用")

    result = db.replace_translation_quality_mismatch(
        entry.id, SRC_AI, "使用", "使用 %s", **_REPAIR_IDENTITY
    )

    assert result.status == "updated"
    # 新文字還沒人看過：審核標記退回，資料庫與歷史快照一致
    assert tuple(_ai_row(db, entry.id)) == ("使用 %s", None)
    history = db.entry_detail(entry.id).history[0]
    assert history.action == "quality_repair"
    assert db.revert(history.id) == 1
    assert tuple(_ai_row(db, entry.id)) == ("使用", "reviewed")


def test_human_confirmed_repair_marks_non_manual_source_reviewed(db):
    db.ingest(
        "1.21.1", [ScanItem(KIND_LANG, "foo", "k", "Use %s", "使用", source=SRC_AI)]
    )
    entry = db.list_entries("1.21.1")[0][0]

    result = db.replace_translation_quality_mismatch(
        entry.id,
        SRC_AI,
        "使用",
        "使用 %s",
        action="quality_repair_review",
        actor="人工",
        **_REPAIR_IDENTITY,
    )

    assert result.status == "updated"
    assert tuple(_ai_row(db, entry.id)) == ("使用 %s", "reviewed")
    assert db.get_entry(entry.id).review_status == "reviewed"
    assert db.revert(db.entry_detail(entry.id).history[0].id) == 1
    assert tuple(_ai_row(db, entry.id)) == ("使用", None)


def test_automatic_repair_of_reviewed_row_is_skipped_when_unreviewing_changes_effective(
    db,
):
    from translation_tool.translation_db.schema import SRC_JAR_TW

    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "k", "Use %s", "使用", source=SRC_AI),
            ScanItem(KIND_LANG, "foo", "k", "Use %s", "別的", source=SRC_JAR_TW),
        ],
    )
    entry = db.list_entries("1.21.1")[0][0]
    db.set_priority((SRC_JAR_TW, SRC_AI, SRC_MANUAL))
    db._conn.execute(
        "UPDATE translation SET review_status='reviewed' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._rebuild_effective(db._conn)
    db._conn.commit()
    assert db.get_entry(entry.id).source == SRC_AI  # 已審核的 AI 贏過排前面的 JAR

    result = db.replace_translation_quality_mismatch(
        entry.id, SRC_AI, "使用", "使用 %s", **_REPAIR_IDENTITY
    )

    assert result.status == "skipped_changed"  # 退回審核會讓 JAR 接手，保留原文
    assert tuple(_ai_row(db, entry.id)) == ("使用", "reviewed")


def test_review_trigger_still_clears_stale_review_for_outside_edits_and_flag_is_gone(
    db,
):
    entry = _reviewed_ai_entry(db, "Use %s", "使用")
    db._conn.execute(
        "UPDATE translation SET zh_tw='別的字' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    assert _ai_row(db, entry.id)[1] is None  # 一般外部改字仍清掉舊審核

    db.replace_translation_quality_mismatch(
        entry.id,
        SRC_AI,
        "別的字",
        "別的字 %s",
        expected_version="1.21.1",
        expected_kind=KIND_LANG,
        expected_mod_id="foo",
        expected_key="k",
        expected_en_us="Use %s",
        expected_revision=None,
        action="quality_repair_review",
    )
    flag = db._conn.execute(
        "SELECT 1 FROM meta WHERE key='keep_review_on_text_change'"
    ).fetchone()
    assert flag is None  # 旗標只存在於交易內


def test_manual_save_keeps_review_when_another_source_would_still_win(db):
    from translation_tool.translation_db.schema import SRC_JAR_TW

    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "k", "Hell", "甲", source=SRC_AI),
            ScanItem(KIND_LANG, "foo", "k", "Hell", "丙", source=SRC_JAR_TW),
        ],
    )
    entry = db.list_entries("1.21.1")[0][0]
    db.set_priority((SRC_AI, SRC_MANUAL, SRC_JAR_TW))
    db._conn.execute(
        "UPDATE translation SET review_status='reviewed' WHERE entry_id=? AND source=?",
        (entry.id, SRC_JAR_TW),
    )
    db._rebuild_effective(db._conn)
    db._conn.commit()
    assert db.get_entry(entry.id).zh_tw == "丙"

    db.save_manual(entry.id, "乙", propagate=False)

    jar = db._conn.execute(
        "SELECT review_status FROM translation WHERE entry_id=? AND source=?",
        (entry.id, SRC_JAR_TW),
    ).fetchone()[0]
    assert jar == "reviewed"  # 退回也換不到人工，不丟審核標記
    assert db.get_entry(entry.id).zh_tw == "丙"  # 畫面不會莫名切成 AI 的甲
