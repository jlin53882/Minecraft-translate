"""資料庫清理：匯出檢查檔、依檢查檔刪除、整批還原。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.cleanup import _text_hash, entry_cleanup_category
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


def _apply(db: TranslationDB, path):
    """預覽後帶著 token 套用（核心 API 一定要帶預覽的 token）。"""
    return db.apply_cleanup(path, db.preview_cleanup(path).token)


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
    assert data["format"] == "mod-db-cleanup-v2"
    assert len(data["items"]) == 5
    assert all(item["delete"] is True for item in data["items"])


def test_apply_deletes_checked_items_and_can_be_reverted(tmp_path):
    db = _db(tmp_path)
    before = _count(db)
    report_path = tmp_path / "r.json"
    db.export_cleanup_report(VERSION, report_path)
    plan = _apply(db, report_path)
    assert plan.deletable == 5
    assert _count(db) == before - 5
    assert db._one("SELECT COUNT(*) FROM entry WHERE en_us = ?", (REAL,))[0] == 2
    info = db.last_cleanup_batch()
    assert info is not None and info.count == 5 and info.reverted_at is None
    result = db.revert_cleanup(info.batch_id)
    assert (result.restored, result.pending, result.complete) == (5, 0, True)
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
    plan = _apply(db, report_path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE)])
    result = db.revert_cleanup(plan.batch_id)
    assert (result.restored, len(result.conflicts), result.complete) == (4, 1, False)
    assert db.last_cleanup_batch().reverted_at is None  # 沒全部還原就不算完成


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


def test_lang_display_semantics_do_not_delete_visible_snake_case_values():
    """常數形狀不是充分證據；顯示語意仍保護可用的原文與翻譯。"""
    for key, value in (
        ("block.createaddition.barbed_wire.tooltip", "BARBED_WIRE"),
        (
            "text.autoconfig.simpleplanes.option.FLY_TICKS_PER_COAL",
            "FLY_TICKS_PER_COAL",
        ),
        ("subtitles.clogger_roaring", "clogger_roaring"),
        ("effect.undead_revamp2.tankleak", "tank_leaks"),
    ):
        assert entry_cleanup_category("lang", key, value) is None, key

    # item tooltip 的值沒有足夠語意證明是玩家文字，仍維持保守排除。
    assert entry_cleanup_category("lang", "item.m.oil.tooltip", "SEED_OIL_BUCKET") == (
        "code_value"
    )


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


# ---------------------------------------------------------------- 符合規則但已有譯文或歷史
def _with_translations(tmp_path: Path) -> TranslationDB:
    """``_db`` 再加兩筆人工譯文：一筆是佔位字（垃圾條目），一筆是正常的 Alpha。

    符合規則又有譯文的條目共兩筆：jar 繁中的授權條款、人工翻過的佔位字。
    """
    db = _db(tmp_path)
    for key, text in (("b/e.json#pages[1].text", "[待補照片]"), ("item.m.a", "甲")):
        db.save_manual(db._one("SELECT id FROM entry WHERE key = ?", (key,))[0], text)
    return db


def _protected_report(db: TranslationDB, tmp_path: Path, *, confirm: bool = True):
    """匯出已有譯文的檢查檔；``confirm`` 模擬使用者看過後把所有項目都改成要刪。"""
    path = tmp_path / "p.json"
    report = db.export_protected_report(VERSION, path)
    if confirm:
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data["items"]:
            item["delete"] = True
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return report, path


def test_protected_export_lists_only_rule_matches_that_have_translations(tmp_path):
    db = _with_translations(tmp_path)
    before = _count(db)
    report, path = _protected_report(db, tmp_path, confirm=False)
    assert _count(db) == before  # 匯出不刪任何東西
    assert report.counts == {"license": 1, "placeholder": 1}
    assert report.protected == {}
    assert report.sources == {"模組自帶繁中": 1, "人工": 1}
    data = json.loads(path.read_text(encoding="utf-8"))
    by_key = {item["key"]: item for item in data["items"]}
    assert set(by_key) == {
        "g/b.md#0",
        "b/e.json#pages[1].text",
    }  # 正常譯文 Alpha 不列入
    item = by_key["b/e.json#pages[1].text"]
    assert item["has_translation"] is True
    # 人工寫的中文譯文：即使是確定性類別也預設不刪，要人工確認
    assert item["delete"] is False and item["review"]
    assert (
        by_key["g/b.md#0"]["delete"] is False
    )  # 授權條款是推測類別，有中文譯文也預設不刪
    assert (report.default_selected, report.review_required) == (0, 2)
    assert report.exported_total == 2
    assert item["zh_tw"] == "[待補照片]" and item["source"] == "人工"
    assert item["category"] == "placeholder" and item["state_hash"]
    # 一般檢查檔照舊不列入這兩筆
    rule = db.export_cleanup_report(VERSION, tmp_path / "r.json")
    assert rule.protected == {"license": 1, "placeholder": 1}


def test_protected_cleanup_deletes_translations_and_can_be_reverted(tmp_path):
    db = _with_translations(tmp_path)
    before = _count(db)
    tables = {
        t: db._one(f"SELECT COUNT(*) FROM {t}")[0]
        for t in ("translation", "effective", "history")
    }
    _report, path = _protected_report(db, tmp_path)
    plan = _apply(db, path)
    assert plan.deletable == 2 and plan.with_translation == 2
    assert plan.by_category == {"license": 1, "placeholder": 1}
    assert _count(db) == before - 2
    assert db._one("SELECT COUNT(*) FROM entry WHERE key = 'item.m.a'")[0] == 1
    result = db.revert_cleanup(plan.batch_id)
    assert (result.restored, result.complete) == (2, True)
    assert _count(db) == before
    for table, count in tables.items():
        assert db._one(f"SELECT COUNT(*) FROM {table}")[0] == count, table


def test_protected_item_is_skipped_when_translation_or_history_changed(tmp_path):
    db = _with_translations(tmp_path)
    _report, path = _protected_report(db, tmp_path)
    entry_id = db._one("SELECT id FROM entry WHERE key = 'g/b.md#0'")[0]
    db.save_manual(entry_id, "授權條款（人工確認）")
    plan = db.preview_cleanup(path)
    assert plan.skipped["譯文或歷史已變動"] == 1
    assert plan.deletable == 1 and plan.with_translation == 1


def test_protected_item_still_needs_to_match_the_rules(tmp_path):
    db = _with_translations(tmp_path)
    _report, path = _protected_report(db, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    # 把正常條目 Alpha 偽造成項目：規則不符，不會因為帶旗標就被刪
    alpha = db._one("SELECT id, en_us FROM entry WHERE key = 'item.m.a'")
    data["items"].append(
        {
            "id": alpha[0],
            "delete": True,
            "category": "placeholder",
            "kind": "lang",
            "mod": "m",
            "key": "item.m.a",
            "en_us_hash": _text_hash(alpha[1]),
            "has_translation": True,
        }
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    plan = db.preview_cleanup(path)
    assert plan.deletable == 2
    assert plan.skipped == {"目前規則判為正常內容": 1}


def test_without_the_flag_translated_items_stay_protected(tmp_path):
    db = _with_translations(tmp_path)
    _report, path = _protected_report(db, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["items"]:
        item.pop("has_translation")
    path.write_text(json.dumps(data), encoding="utf-8")
    plan = db.preview_cleanup(path)
    assert plan.deletable == 0
    assert plan.skipped["已有譯文或歷史"] == 2


def test_service_exports_the_translated_report_next_to_the_rule_reports(tmp_path):
    from app.services_impl import moddb_cleanup_service as svc

    db = _with_translations(tmp_path)
    report = svc.export_cleanup(db, VERSION, with_translation=True)
    assert report.path.name.endswith("-translated.json")
    assert report.path.parent == tmp_path / "cleanup_reports"
    assert svc.list_cleanup_reports(db) == [report.path]
    assert svc.preview_cleanup(db, report.path).deletable == 0  # 預設都要人工確認


def test_protected_export_with_nothing_protected_is_empty(tmp_path):
    db = TranslationDB(tmp_path / "e.db")
    db.ingest(VERSION, [ScanItem("lang", "m", "item.m.a", "Alpha", zh_tw="甲")])
    report, path = _protected_report(db, tmp_path)
    assert report.exported_total == 0 and report.counts == {}
    assert json.loads(path.read_text(encoding="utf-8"))["items"] == []


# ---------------------------------------------------------------- 誤判防護
def test_text_overrides_ids_stay_out_and_language_region_is_normal_text():
    key = "b/e.json#pages[2].text_overrides[0][1]"
    # Blood Magic 的 ["Raw Will", "raw"]：右邊是意志類型 id，翻了會壞，不能放行
    assert entry_cleanup_category("patchouli", key, "raw") == "not_text"
    assert entry_cleanup_category("lang", "language.region", "United States") is None
    assert entry_cleanup_category("lang", "language.code", "en_us") == "meta_key"


def test_protected_export_does_not_delete_translated_shape_matches_by_default(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(
        VERSION,
        [
            ScanItem("patchouli", "m", "b/e.json#pages[0].anchor", "addArea"),
            ScanItem("lang", "m", "item.m.tip", "BARBED_WIRE"),
            ScanItem("lang", "m", "item.m.color", "FFFFFF"),
            ScanItem("lang", "m", "_comment", "Note", zh_tw="備註"),
        ],
    )
    for key, text in (
        ("b/e.json#pages[0].anchor", "新增區域()"),
        ("item.m.tip", "鐵絲網"),
        ("item.m.color", "FFFFFF"),
    ):
        db.save_manual(db._one("SELECT id FROM entry WHERE key = ?", (key,))[0], text)
    report, path = _protected_report(db, tmp_path, confirm=False)
    items = {i["key"]: i for i in json.loads(path.read_text("utf-8"))["items"]}
    assert report.review_required == 2 and report.default_selected == 2
    assert (
        items["b/e.json#pages[0].anchor"]["delete"] is False
        and items["b/e.json#pages[0].anchor"]["review"]
    )
    assert items["item.m.tip"]["delete"] is False
    assert items["item.m.color"]["delete"] is True  # 譯文沒有中文：照規則
    assert items["_comment"]["delete"] is True  # 語言檔備註靠鍵判斷，不看譯文
    assert report.exported_total == 4  # 待確認的也算匯出筆數
    plan = _apply(db, path)
    assert plan.requested == 2 and plan.deletable == 2
    assert (
        db._one("SELECT COUNT(*) FROM entry WHERE key = 'b/e.json#pages[0].anchor'")[0]
        == 1
    )


def test_extraction_skips_what_cleanup_would_remove():
    """掃描與清理用同一套規則：清掉的條目不會在下次掃描又被收進來。"""
    from translation_tool.core.text_sniffer import advancement_texts
    from translation_tool.core.translatable_extractor import extract_translatables

    lang = {
        "a.todo": "TODO",
        "a.arrows": "← →",
        "a.deg": "180°",
        "a.fmt": "%s",
        "a.real": "Iron Ingot",
    }
    found = {
        i["path"] for i in extract_translatables(lang, Path("assets/m/lang/en_us.json"))
    }
    assert "a.real" in found and "a.fmt" not in found
    assert not found & {"a.todo", "a.arrows", "a.deg"}
    adv = {
        "criteria": {"x": {}},
        "display": {
            "title": {"text": "Title"},
            "description": {"text": "Real description here"},
        },
    }
    assert advancement_texts(adv) == {
        "display.description.text": "Real description here"
    }


def test_pascal_case_names_are_text_but_lower_camel_identifiers_are_not():
    from translation_tool.core.text_sniffer import classify_book_string

    for name in ("CreeBee", "GraviChestPlate", "CraftTweaker", "FluxoMagnet"):
        assert classify_book_string(name, "name") == "text", name  # 顯示欄位
        assert classify_book_string(name) == "not", name  # 欄位未知：保守不收
    for ident in ("addArea()", "blockPos", "setCanSteal()"):
        assert classify_book_string(ident) == "not", ident


def test_display_fields_are_text_even_when_they_look_like_identifiers():
    """Patchouli 文件明寫 name／title／text 會顯示：單字、A/B、駝峰都是名稱；id 欄位照舊排除。"""
    pairs = {
        "x.json#name": ("pollen_sifter", None),
        "categories/s.json#name": ("Semiblocks/Gadgets", None),
        "x.json#pages[0].title": ("CreeBee", None),
        "x.json#pages[0].text": ("raw", None),
        "x.json#pages[0].anchor": ("intro", "not_text"),
        "x.json#pages[0].item": ("minecraft:stone", "not_text"),
        "x.json#pages[1].images[0]": ("m:textures/a.png", "not_text"),
        "x.json#pages[2].text_overrides[0][1]": ("raw", "not_text"),
    }
    for key, (value, expected) in pairs.items():
        assert entry_cleanup_category("patchouli", key, value) == expected, key
