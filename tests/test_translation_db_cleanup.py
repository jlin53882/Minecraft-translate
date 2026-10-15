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


def test_only_an_explicit_true_delete_flag_deletes(tmp_path):
    db = _db(tmp_path)
    report_path = tmp_path / "r.json"
    db.export_cleanup_report(VERSION, report_path)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    data["items"][0]["delete"] = "false"
    data["items"][1]["delete"] = 0
    del data["items"][2]["delete"]
    report_path.write_text(json.dumps(data), encoding="utf-8")
    plan = db.preview_cleanup(report_path)
    assert plan.deletable == len(data["items"]) - 3
    assert plan.skipped["delete 欄位不是 true／false"] == 3
    assert plan.requested == len(data["items"])


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


def test_overview_counts_per_kind_and_category_without_deleting(tmp_path):
    from app.services_impl import moddb_cleanup_service as svc

    db = _db(tmp_path)
    before = _count(db)
    counts, protected = svc.cleanup_overview(db, VERSION)
    assert counts == {
        ("guide", "tag_only"): 1,
        ("guide", "license"): 1,
        ("guide", "comment"): 1,
        ("patchouli", "multiblock"): 1,
        ("patchouli", "placeholder"): 1,
    }
    assert protected == 1
    assert _count(db) == before
    assert svc.cleanup_overview(db, "9.9.9") == ({}, 0)


def test_lang_metadata_and_code_values_are_cleanup_candidates():
    for key, value, expected in (
        ("_comment0", "Note to translators: DO NOT COPY ENTRIES", "meta_key"),
        ("language.code", "en_us", "meta_key"),
        ("biome.x.color", "AAC4D2", "code_value"),
        ("item.m.oil.tooltip", "SEED_OIL_BUCKET", "code_value"),
        ("painting.m.aztec.title", "de_aztec", "code_value"),
        ("link", "https://bugs.mojang.com/browse/MC-1", "code_value"),
    ):
        assert entry_cleanup_category("lang", key, value) == expected, key
    for key, value in (
        ("item.m.a", "Seed Oil Bucket"),
        ("gui.m.mode", "Off"),
        ("biome.x.note", "Color: AAC4D2 is shown"),
    ):
        assert entry_cleanup_category("lang", key, value) is None, key


def test_short_ui_words_are_translatable_but_acronyms_are_not():
    from translation_tool.core.lm_config_rules import is_value_translatable

    for word in ("All", "Off", "Yes", "Red", "no", "Top"):
        assert is_value_translatable(word, is_lang=True), word
    for word in ("NBT", "XP", "ab"):
        assert not is_value_translatable(word, is_lang=True), word


def test_report_items_must_match_the_entry_identity(tmp_path):
    db = _db(tmp_path)
    report_path = tmp_path / "r.json"
    db.export_cleanup_report(VERSION, report_path)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    # 其他資料庫複本裡同一個 id 可能是別的條目：key／mod／kind 任一不符都不刪
    data["items"][0]["key"] = "other/key.md#0"
    data["items"][1]["mod"] = "other_mod"
    data["items"][2].pop("kind")
    report_path.write_text(json.dumps(data), encoding="utf-8")
    plan = db.preview_cleanup(report_path)
    assert plan.skipped["類型、模組或鍵值與檢查檔不符"] == 3
    assert plan.deletable == len(data["items"]) - 3 - plan.skipped["已有譯文或歷史"]


# ---------------------------------------------------------------- 已翻譯內容
def _translated_db(tmp_path: Path) -> TranslationDB:
    """``_db`` 加上兩筆已翻譯條目，共三筆、分在兩個模組。

    模組 m：jar 繁中的授權條款、人工的 Alpha；模組 n：jar 繁中的 Beta。
    """
    db = _db(tmp_path)
    db.ingest(VERSION, [ScanItem("lang", "n", "item.n.b", "Beta", zh_tw="乙")])
    entry_id = db._one("SELECT id FROM entry WHERE key = 'item.m.a'")[0]
    db.save_manual(entry_id, "甲")
    return db


def _translated_report(db: TranslationDB, tmp_path: Path):
    folder = tmp_path / "translated"
    return db.export_translated_report(VERSION, folder), folder


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_translated_export_writes_one_report_per_mod_and_a_summary(tmp_path):
    db = _translated_db(tmp_path)
    before = _count(db)
    report, folder = _translated_report(db, tmp_path)
    assert _count(db) == before  # 匯出不刪任何東西
    assert report.counts == {"translated": 3}
    assert report.protected == {}
    assert report.files == 2
    assert report.sources == {"模組自帶繁中": 2, "人工": 1}
    assert sorted(f.name for f in folder.iterdir()) == [
        "m.json",
        "n.json",
        "summary.txt",
    ]
    m = _read(folder / "m.json")
    assert m["format"] == "mod-db-cleanup-v1" and m["version"] == VERSION
    assert m["summary"]["translated"]["mod"] == "m"
    assert m["summary"]["translated"]["count"] == 2
    assert {item["key"] for item in m["items"]} == {"g/b.md#0", "item.m.a"}
    manual = next(i for i in m["items"] if i["key"] == "item.m.a")
    assert manual["category"] == "translated" and manual["delete"] is True
    assert manual["zh_tw"] == "甲" and manual["source"] == "人工"
    assert manual["zh_tw_hash"] and manual["en_us_hash"]
    assert [i["key"] for i in _read(folder / "n.json")["items"]] == ["item.n.b"]
    summary = (folder / "summary.txt").read_text(encoding="utf-8")
    assert "共 3 筆、2 個模組" in summary
    assert "2\tm\tm.json\t" in summary  # 筆數多的模組在前
    assert "1\tn\tn.json\t模組自帶繁中 1" in summary


def test_translated_folder_is_one_batch_and_can_be_reverted(tmp_path):
    db = _translated_db(tmp_path)
    before = _count(db)
    translations = db._one("SELECT COUNT(*) FROM translation")[0]
    effective = db._one("SELECT COUNT(*) FROM effective")[0]
    history = db._one("SELECT COUNT(*) FROM history")[0]
    assert history >= 1  # 人工儲存會留下歷史
    _report, folder = _translated_report(db, tmp_path)
    plan = db.apply_cleanup(folder)
    assert plan.deletable == 3 and plan.by_category == {"translated": 3}
    assert _count(db) == before - 3
    # 還沒翻譯的條目與一般規則的垃圾條目都不碰
    assert db._one("SELECT COUNT(*) FROM entry WHERE en_us = ?", (REAL,))[0] == 2
    assert db._one("SELECT COUNT(*) FROM effective")[0] == effective - 3
    info = db.last_cleanup_batch()
    assert info.batch_id == plan.batch_id and info.count == 3  # 整個資料夾同一批
    restored, skipped = db.revert_cleanup(plan.batch_id)
    assert (restored, skipped) == (3, 0)
    assert _count(db) == before
    assert db._one("SELECT COUNT(*) FROM translation")[0] == translations
    assert db._one("SELECT COUNT(*) FROM effective")[0] == effective
    assert db._one("SELECT COUNT(*) FROM history")[0] == history


def test_deleting_a_mod_file_keeps_that_mod(tmp_path):
    db = _translated_db(tmp_path)
    _report, folder = _translated_report(db, tmp_path)
    (folder / "n.json").unlink()
    data = _read(folder / "m.json")
    data["items"] = [i for i in data["items"] if i["key"] != "item.m.a"]
    (folder / "m.json").write_text(json.dumps(data), encoding="utf-8")
    plan = db.apply_cleanup(folder)
    assert plan.deletable == 1
    keys = {r[0] for r in db._q("SELECT key FROM entry WHERE mod_id IN ('m', 'n')")}
    assert "item.n.b" in keys and "item.m.a" in keys and "g/b.md#0" not in keys


def test_translated_item_is_skipped_when_its_translation_changed_since_export(tmp_path):
    db = _translated_db(tmp_path)
    _report, folder = _translated_report(db, tmp_path)
    entry_id = db._one("SELECT id FROM entry WHERE key = 'item.m.a'")[0]
    db.save_manual(entry_id, "甲（改過）")
    plan = db.preview_cleanup(folder)
    assert plan.skipped["譯文已變動"] == 1
    assert plan.deletable == 2


def test_translated_item_is_skipped_when_it_no_longer_has_a_translation(tmp_path):
    db = _translated_db(tmp_path)
    _report, folder = _translated_report(db, tmp_path)
    with db._tx() as conn:
        conn.execute(
            "DELETE FROM effective WHERE entry_id IN "
            "(SELECT id FROM entry WHERE key = 'item.m.a')"
        )
    plan = db.preview_cleanup(folder)
    assert plan.skipped["目前沒有生效譯文"] == 1
    assert plan.deletable == 2


def test_a_rule_report_item_can_never_delete_a_translated_entry(tmp_path):
    db = _translated_db(tmp_path)
    _report, folder = _translated_report(db, tmp_path)
    for path in folder.glob("*.json"):
        data = _read(path)
        for item in data["items"]:
            item["category"] = "license"  # 改成一般規則的類別：保護規則照舊
        path.write_text(json.dumps(data), encoding="utf-8")
    plan = db.preview_cleanup(folder)
    assert plan.deletable == 0
    assert plan.skipped["已有譯文或歷史"] == 1  # 授權條款
    assert plan.skipped["目前規則判為正常內容"] == 2  # Alpha、Beta


def test_the_rule_report_still_leaves_translated_entries_alone(tmp_path):
    db = _translated_db(tmp_path)
    report = db.export_cleanup_report(VERSION, tmp_path / "r.json")
    assert "translated" not in report.counts
    assert report.protected == {"license": 1}


def test_report_folder_errors_name_the_problem(tmp_path):
    db = _translated_db(tmp_path)
    _report, folder = _translated_report(db, tmp_path)
    data = _read(folder / "n.json")
    data["version"] = "1.20.1"
    (folder / "n.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="版本不一致"):
        db.preview_cleanup(folder)
    (folder / "n.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="n.json"):
        db.preview_cleanup(folder)
    for path in folder.glob("*.json"):
        path.unlink()
    with pytest.raises(ValueError, match="沒有檢查檔"):
        db.preview_cleanup(folder)


def test_mod_file_names_are_safe_and_unique():
    from translation_tool.translation_db.cleanup import _mod_file_name

    used: set[str] = set()
    assert _mod_file_name("create", used) == "create.json"
    assert _mod_file_name("a:b/c", used) == "a_b_c.json"
    assert _mod_file_name("Create", used) == "Create-2.json"  # Windows 不分大小寫
    assert _mod_file_name("", used) == "_.json"


def test_translated_counts_and_service_export(tmp_path):
    from app.services_impl import moddb_cleanup_service as svc

    db = _translated_db(tmp_path)
    assert svc.translated_overview(db, VERSION) == {"guide": 1, "lang": 2}
    assert svc.translated_overview(db, "9.9.9") == {}
    rule = svc.export_cleanup(db, VERSION).path
    report = svc.export_cleanup(db, VERSION, translated=True)
    assert report.path.is_dir() and report.path.name.endswith("-translated")
    assert report.path.parent == tmp_path / "cleanup_reports"
    assert set(svc.list_cleanup_reports(db)) == {rule, report.path}
    assert svc.report_file_count(report.path) == 2
    assert svc.report_file_count(rule) == 1
    assert svc.preview_cleanup(db, report.path).deletable == 3


def test_translated_export_with_nothing_translated_is_empty(tmp_path):
    db = TranslationDB(tmp_path / "e.db")
    db.ingest(VERSION, [ScanItem("lang", "m", "item.m.a", "Alpha")])
    report, folder = _translated_report(db, tmp_path)
    assert report.total == 0 and report.counts == {} and report.files == 0
    assert [f.name for f in folder.iterdir()] == ["summary.txt"]
    with pytest.raises(ValueError, match="沒有檢查檔"):
        db.preview_cleanup(folder)
