"""資料庫清理：匯出檢查檔、依檢查檔刪除、整批還原。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.cleanup import entry_cleanup_category
from translation_tool.translation_db.models import ScanItem

VERSION = "1.21.1"
LICENSE = "Permission is hereby granted, free of charge, to any person."
TAGS_ONLY = '<ImportStructure src="../s.snbt"></ImportStructure>'
REAL = "Open the terminal with a right click to see all items."


def _db(tmp_path: Path) -> TranslationDB:
    db = TranslationDB(tmp_path / "t.db")
    items = [
        ScanItem("guide", "m", "g/a.md#0", TAGS_ONLY),
        ScanItem("guide", "m", "g/a.md#1", LICENSE),
        ScanItem("guide", "m", "g/a.md#2", REAL),
        ScanItem("guide", "m", "g/a.md#3", "[//]: # (navigation:)"),
        ScanItem(
            "patchouli", "m", "b/e.json#pages[0].multiblock.pattern[1][0]", "BPPB"
        ),
        ScanItem("patchouli", "m", "b/e.json#pages[1].text", "[TODO: photo goes here]"),
        ScanItem("patchouli", "m", "b/e.json#pages[2].text", REAL),
        # 有繁中的垃圾條目：不該被清
        ScanItem("guide", "m", "g/b.md#0", LICENSE, zh_tw="授權條款"),
        ScanItem("lang", "m", "item.m.a", "Alpha"),
    ]
    db.ingest(VERSION, items)
    return db


def _count(db: TranslationDB) -> int:
    return db._one("SELECT COUNT(*) FROM entry")[0]


def test_entry_cleanup_category_by_content_shape():
    assert entry_cleanup_category("guide", "k", TAGS_ONLY) == "tag_only"
    assert entry_cleanup_category("guide", "k", LICENSE) == "license"
    assert entry_cleanup_category("guide", "k", REAL) is None
    assert entry_cleanup_category("patchouli", "x.multiblock.pattern[0]", "BPPB") == (
        "multiblock"
    )
    assert entry_cleanup_category("patchouli", "x#text", "TODO") == "placeholder"
    assert entry_cleanup_category("patchouli", "x#text", "mod:basics/entry") == (
        "not_text"
    )
    assert entry_cleanup_category("patchouli", "x#text", REAL) is None
    assert (
        entry_cleanup_category("lang", "item.a", LICENSE) is None
    )  # 語言檔只用保守規則
    assert entry_cleanup_category("lang", "mirror.left_right", "← →") == "no_letters"
    assert (
        entry_cleanup_category("lang", "attribute.equals", "%s %s") is None
    )  # 格式碼要保留
    assert (
        entry_cleanup_category("lang", "gui.off", "off") is None
    )  # 小寫單字是正常譯文
    assert entry_cleanup_category("lang", "k", "TODO: translate me") == "placeholder"
    assert (
        entry_cleanup_category(
            "advancement", "a.json#display.description.text", "Description"
        )
        == "field_name"
    )
    assert (
        entry_cleanup_category("advancement", "a.json#display.title.text", "Stone Age")
        is None
    )
    assert entry_cleanup_category("guide", "k", "") is None  # 沒有原文不處理


def test_export_lists_only_untouched_candidates_and_does_not_delete(tmp_path):
    db = _db(tmp_path)
    before = _count(db)
    report = db.export_cleanup_report(VERSION, tmp_path / "r.json")
    assert _count(db) == before
    assert report.counts == {
        "tag_only": 1,
        "license": 1,
        "comment": 1,
        "multiblock": 1,
        "placeholder": 1,
    }
    assert report.protected == {"license": 1}  # 有繁中的授權條款略過
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert data["format"] == "mod-db-cleanup-v1"
    assert len(data["items"]) == 5
    assert all(item["delete"] is True for item in data["items"])


def test_apply_deletes_checked_items_and_can_be_reverted(tmp_path):
    db = _db(tmp_path)
    before = _count(db)
    report_path = tmp_path / "r.json"
    db.export_cleanup_report(VERSION, report_path)
    plan = db.apply_cleanup(report_path)
    assert plan.deletable == 5
    assert _count(db) == before - 5
    assert db._one("SELECT COUNT(*) FROM entry WHERE en_us = ?", (REAL,))[0] == 2
    info = db.last_cleanup_batch()
    assert info is not None and info.count == 5 and info.reverted_at is None
    restored, skipped = db.revert_cleanup(info.batch_id)
    assert (restored, skipped) == (5, 0)
    assert _count(db) == before
    assert db.last_cleanup_batch().reverted_at is not None
    with pytest.raises(ValueError):
        db.revert_cleanup(info.batch_id)  # 不能重複還原


def test_items_removed_or_marked_false_in_the_report_are_kept(tmp_path):
    db = _db(tmp_path)
    report_path = tmp_path / "r.json"
    db.export_cleanup_report(VERSION, report_path)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    data["items"][0]["delete"] = False
    del data["items"][1]
    report_path.write_text(json.dumps(data), encoding="utf-8")
    assert db.preview_cleanup(report_path).deletable == 3


def test_apply_revalidates_each_item_against_current_data(tmp_path):
    db = _db(tmp_path)
    report_path = tmp_path / "r.json"
    db.export_cleanup_report(VERSION, report_path)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    by_key = {item["key"]: item for item in data["items"]}
    # 檢查檔之後，有人替其中一筆加了人工譯文
    entry_id = by_key["g/a.md#1"]["id"]
    db.save_manual(entry_id, "授權條款（人工）")
    # 原文變動
    changed = by_key["g/a.md#0"]["id"]
    with db._tx() as conn:
        conn.execute(
            "UPDATE entry SET en_us = ? WHERE id = ?", ("Brand new text here.", changed)
        )
    plan = db.preview_cleanup(report_path)
    assert plan.skipped["已有譯文或歷史"] == 1
    assert plan.skipped["原文已變動"] == 1
    assert plan.deletable == 3
    # 假造的編號與其他版本不會被刪
    data["items"].append({"id": 999999, "delete": True, "category": "license"})
    report_path.write_text(json.dumps(data), encoding="utf-8")
    assert db.preview_cleanup(report_path).skipped["條目已不存在"] == 1


def test_a_foreign_json_file_is_rejected(tmp_path):
    db = _db(tmp_path)
    bad = tmp_path / "x.json"
    bad.write_text('{"items": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        db.preview_cleanup(bad)


def test_revert_skips_entries_that_were_scanned_again(tmp_path):
    db = _db(tmp_path)
    report_path = tmp_path / "r.json"
    db.export_cleanup_report(VERSION, report_path)
    plan = db.apply_cleanup(report_path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE)])
    restored, skipped = db.revert_cleanup(plan.batch_id)
    assert (restored, skipped) == (4, 1)


def test_service_exports_into_a_folder_next_to_the_database_and_lists_newest_first(
    tmp_path,
):
    from app.services_impl import moddb_cleanup_service as svc

    db = _db(tmp_path)
    first = svc.export_cleanup(db, VERSION).path
    assert first.parent == tmp_path / "cleanup_reports"
    other = first.with_name("cleanup-1.21.1-99999999-999999.json")
    other.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    assert svc.list_cleanup_reports(db)[0] == other
    plan = svc.preview_cleanup(db, other)
    assert plan.deletable == 5
