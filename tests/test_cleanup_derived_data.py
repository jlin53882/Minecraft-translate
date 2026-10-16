"""資料清理（刪除／還原）與觸發器維護的衍生資料、快取的互動。

統計摘要、最近人工活動、生效譯文衍生欄位與品質旗標都由 SQLite 觸發器維護；
清理一次刪除或寫回大量條目，每一步之後都必須和全庫重算一致，且畫面讀到的數字立即正確。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.translation_db import EntryFilter, ScanItem, TranslationDB

V1, V2 = "1.20.1", "1.21.1"
LICENSE = "Permission is hereby granted, free of charge, to any person."
PLACEHOLDER = "[TODO: photo goes here]"
TABLES = ("entry", "translation", "effective", "history", "src_change")


def _items() -> list[ScanItem]:
    return [
        ScanItem("guide", "m", "g/lic.md#0", LICENSE, zh_tw="授權條款"),
        ScanItem("patchouli", "m", "b/e.json#pages[1].text", PLACEHOLDER),
        ScanItem("lang", "m", "item.m.a", "Alpha"),
        ScanItem("lang", "m", "item.m.b", "Beta", zh_tw="乙"),
    ]


@pytest.fixture
def db(tmp_path: Path):
    database = TranslationDB(tmp_path / "t.db")
    database.ingest(V1, _items())
    database.ingest(V2, _items())
    for version in (V1, V2):
        database.save_manual(
            _id(database, version, "b/e.json#pages[1].text"), "[待補照片]"
        )
        database.save_manual(_id(database, version, "item.m.a"), "甲")
    # 原文變更紀錄：同一鍵值換了原文
    database.ingest(V2, [ScanItem("guide", "m", "g/lic.md#0", LICENSE + " v2")])
    yield database
    database.close()


def _id(db: TranslationDB, version: str, key: str) -> int:
    return db._one(
        "SELECT id FROM entry WHERE mc_version = ? AND key = ?", (version, key)
    )[0]


def _dump(db: TranslationDB) -> dict[str, list]:
    """所有資料表、所有欄位（含 qflags、diff、upd 等衍生欄位）。"""
    return {
        t: [list(r) for r in db._q(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in TABLES
    }


def _assert_derived_consistent(db: TranslationDB) -> None:
    assert db.verify_stat_summary() == []
    assert db.verify_entry_activity() == []
    assert db.verify_effective_columns() == []
    assert db.verify_quality_flags() == []
    assert db._one("PRAGMA foreign_key_check") is None
    assert db._one("PRAGMA integrity_check")[0] == "ok"
    assert db._one("SELECT 1 FROM meta WHERE key = 'tx_active'") is None


def _screen(db: TranslationDB) -> dict:
    """畫面會讀的數字（都走快取）。"""
    return {
        "overview": db.overview(),
        "untranslated": [db.count_untranslated(v) for v in (V1, V2)],
        "reusable": [db.count_reusable(v) for v in (V1, V2)],
        "entries": [
            db.list_entries(criteria=EntryFilter(version=v), limit=100)[1]
            for v in (V1, V2)
        ],
        "diff": db.list_entries(
            criteria=EntryFilter(version=V2, state="diff"), limit=100
        )[1],
    }


def _recomputed(db: TranslationDB) -> dict:
    """不經快取、不用衍生資料，直接從原始資料表算出的對照值。"""
    return {
        "entries": [
            db._one("SELECT COUNT(*) FROM entry WHERE mc_version = ?", (v,))[0]
            for v in (V1, V2)
        ],
        "src_changed": db._one("SELECT COUNT(*) FROM src_change")[0],
        "no_source": db._one("SELECT COUNT(*) FROM entry WHERE en_us = ''")[0],
    }


def _protected_path(db: TranslationDB, tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    db.export_protected_report(V2, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["items"]:
        item["delete"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _clean(db: TranslationDB, path: Path):
    return db.apply_cleanup(path, db.preview_cleanup(path).token)


def test_delete_updates_counts_and_derived_data_immediately(db, tmp_path):
    before_screen = _screen(db)
    before = _recomputed(db)
    plan = _clean(db, _protected_path(db, tmp_path, "p.json"))
    assert plan.deletable >= 1

    _assert_derived_consistent(db)
    after = _recomputed(db)
    assert after["entries"][1] == before["entries"][1] - plan.deletable
    assert after["entries"][0] == before["entries"][0]  # 另一個版本不受影響
    screen = _screen(db)
    assert screen["entries"] == after["entries"]  # 清單筆數立即正確（沒有沿用舊快取）
    assert screen != before_screen
    assert screen["overview"]["src_changed"] == after["src_changed"]


def test_revert_restores_derived_data_and_everything_the_user_wrote(db, tmp_path):
    before_dump = _dump(db)
    before_screen = _screen(db)
    plan = _clean(db, _protected_path(db, tmp_path, "p.json"))
    assert _dump(db) != before_dump

    result = db.revert_cleanup(plan.batch_id)

    assert result.complete and result.conflicts == []
    _assert_derived_consistent(db)
    # 譯文、審核狀態、checker、revision、歷史、生效譯文與衍生欄位完全回到刪除前
    assert _dump(db) == before_dump
    assert _screen(db) == before_screen


def test_second_connection_never_reads_stale_numbers(db, tmp_path):
    reader = TranslationDB(db.path, readonly=True) if hasattr(db, "path") else None
    if reader is None:
        pytest.skip("無法取得資料庫路徑")
    try:
        before = reader.overview(), reader.count_untranslated(V2)
        plan = _clean(db, _protected_path(db, tmp_path, "p.json"))
        mid = _screen(db)
        assert (
            reader.list_entries(criteria=EntryFilter(version=V2), limit=100)[1]
            == (mid["entries"][1])
        )
        db.revert_cleanup(plan.batch_id)
        assert (reader.overview(), reader.count_untranslated(V2)) == before
    finally:
        reader.close()


def test_rescan_delete_revert_sequence_stays_consistent(db, tmp_path):
    original = _dump(db)
    items = _items()

    plan1 = _clean(db, _protected_path(db, tmp_path, "p1.json"))
    _assert_derived_consistent(db)

    # 刪除後重新掃描：被刪的條目以新編號重新出現
    db.ingest(V2, items)
    _assert_derived_consistent(db)
    assert _recomputed(db)["entries"][1] >= 1

    # 還原：原位置已有同一條目 → 保留為待還原，不覆蓋較新的資料
    result = db.revert_cleanup(plan1.batch_id)
    _assert_derived_consistent(db)
    assert result.restored + len(result.conflicts) >= 1

    # 再刪除、再還原、再掃描
    path2 = _protected_path(db, tmp_path, "p2.json")
    if json.loads(path2.read_text(encoding="utf-8"))["items"]:
        plan2 = _clean(db, path2)
        _assert_derived_consistent(db)
        db.revert_cleanup(plan2.batch_id)
        _assert_derived_consistent(db)
    db.ingest(V2, items)
    _assert_derived_consistent(db)

    # 重新掃描讓同一條目回來時，還原不覆蓋新資料：批次保持待還原，被刪的人工譯文仍在快照裡
    assert not result.complete and result.conflicts
    batch = db._one(
        "SELECT payload, reverted_at FROM cleanup_batch WHERE id = ?", (plan1.batch_id,)
    )
    assert batch[1] is None
    assert "[待補照片]" in batch[0]
    assert original  # 起始資料確實存在
    manual = {
        r[0]
        for r in db._q(
            "SELECT t.zh_tw FROM translation t JOIN entry e ON e.id = t.entry_id "
            "WHERE e.mc_version = ?",
            (V2,),
        )
    }
    assert "甲" in manual  # 沒被清理的條目：人工譯文完整保留
    assert db._one("SELECT COUNT(*) FROM history WHERE action = 'manual'")[0] >= 2


def test_cleanup_works_when_derived_data_is_missing(db, tmp_path):
    """舊資料庫（沒有衍生資料）也要能清理與還原，結果和有衍生資料時相同。"""
    for trigger in db._q(
        "SELECT name FROM sqlite_schema WHERE type='trigger' "
        "AND (name LIKE 'stat%' OR name LIKE 'activity%' OR name LIKE 'effcol%' "
        "OR name LIKE 'qflags%' OR name LIKE 'chg_%')"
    ):
        db._conn.execute(f"DROP TRIGGER {trigger[0]}")
    db._conn.commit()
    effective_sql = (
        "SELECT entry_id, zh_tw, source, checker, review_status FROM effective "
        "ORDER BY entry_id, source"
    )
    translation_sql = (
        "SELECT entry_id, source, zh_tw, revision, checker, review_status "
        "FROM translation ORDER BY entry_id, source"
    )
    history_sql = (
        "SELECT entry_id, actor, action, old_zh_tw, new_zh_tw FROM history "
        "ORDER BY entry_id, id"
    )
    user_data = (effective_sql, translation_sql, history_sql)
    before = [db._q(sql) for sql in user_data]
    plan = _clean(db, _protected_path(db, tmp_path, "p.json"))
    db.revert_cleanup(plan.batch_id)
    assert [db._q(sql) for sql in user_data] == before
