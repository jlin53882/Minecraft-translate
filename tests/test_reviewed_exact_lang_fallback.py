"""完整句重用：同種類之外，也比對同模組／版本 lang 的已審核完整句。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_KUBEJS_LANG,
    KIND_LANG,
    TranslationDB,
    WriteBackItem,
)

TEXT = "The pressure chamber processes air."


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "exact.db")
    yield database
    database.close()


def _add(db, kind, mod, key, zh, *, reviewed=True, version="1.21.1"):
    db.write_back(
        version,
        [WriteBackItem(kind, mod, key, TEXT, zh)],
        fill_other_versions=False,
    )
    if reviewed:
        row = next(
            entry
            for entry in db.list_entries(version, limit=100)[0]
            if entry.key == key and entry.mod_id == mod
        )
        db.review_manual(row.id, expected_zh_tw=row.zh_tw, actor="tester")


def _exact(db, mod, kind):
    return db.reviewed_exact_glossary(mod, "1.21.1", kind, (TEXT,))


def test_other_kind_reuses_the_reviewed_lang_sentence(db):
    _add(db, KIND_LANG, "foo", "block.pressure", "壓力室會處理空氣。")

    result = _exact(db, "foo", KIND_KUBEJS_LANG)

    assert [row["zh_tw"] for row in result] == ["壓力室會處理空氣。"]


def test_same_kind_still_works_without_a_lang_entry(db):
    _add(db, KIND_KUBEJS_LANG, "foo", "k.pressure", "壓力室處理空氣。")

    assert [r["zh_tw"] for r in _exact(db, "foo", KIND_KUBEJS_LANG)] == [
        "壓力室處理空氣。"
    ]


def test_same_kind_wins_when_it_has_the_sentence(db):
    """同種類有該句就用同種類；同種類沒有時才退回 lang。"""
    _add(db, KIND_LANG, "foo", "block.pressure", "壓力室會處理空氣。")
    _add(db, KIND_KUBEJS_LANG, "foo", "k.pressure", "壓力艙處理空氣。")

    assert [r["zh_tw"] for r in _exact(db, "foo", KIND_KUBEJS_LANG)] == [
        "壓力艙處理空氣。"
    ]


def test_ambiguous_same_kind_does_not_fall_back_to_lang(db):
    _add(db, KIND_LANG, "foo", "block.pressure", "壓力室會處理空氣。")
    _add(db, KIND_KUBEJS_LANG, "foo", "a.pressure", "壓力艙處理空氣。")
    _add(db, KIND_KUBEJS_LANG, "foo", "b.pressure", "壓力倉處理空氣。")

    assert _exact(db, "foo", KIND_KUBEJS_LANG) == []


def test_pack_module_id_matches_the_lang_of_the_name_before_the_at_sign(db):
    """KubeJS／FTB 的模組識別帶有 ``名稱@模組包@序號``，lang 比對 ``@`` 前面的名稱。"""
    _add(db, KIND_LANG, "alltheores", "block.pressure", "壓力室會處理空氣。")

    result = _exact(db, "alltheores@All the Mods 10 8.3@40b3569cc156", KIND_KUBEJS_LANG)

    assert [r["zh_tw"] for r in result] == ["壓力室會處理空氣。"]
    assert (
        _exact(db, "othermod@All the Mods 10 8.3@40b3569cc156", KIND_KUBEJS_LANG) == []
    )


def test_unreviewed_lang_is_not_used(db):
    _add(db, KIND_LANG, "foo", "block.pressure", "壓力室會處理空氣。", reviewed=False)

    assert _exact(db, "foo", KIND_KUBEJS_LANG) == []


def test_other_module_or_version_is_not_used(db):
    _add(db, KIND_LANG, "bar", "block.pressure", "壓力室會處理空氣。")
    _add(db, KIND_LANG, "foo", "block.pressure", "壓力室會處理空氣。", version="1.20.1")

    assert _exact(db, "foo", KIND_KUBEJS_LANG) == []


# --- 術語表：同種類沒有的術語，退回 ``@`` 前面模組名稱的 lang ---------------------

PACK = "foo@Some Pack 1.0@abcdef123456"


def _add_term(db, kind, mod, key, en, zh):
    db.write_back(
        "1.21.1", [WriteBackItem(kind, mod, key, en, zh)], fill_other_versions=False
    )
    row = next(
        entry
        for entry in db.list_entries("1.21.1", limit=100)[0]
        if entry.key == key and entry.mod_id == mod
    )
    db.review_manual(row.id, expected_zh_tw=row.zh_tw, actor="tester")


def _glossary(db, mod, text):
    return {
        row["en_us"]: row["zh_tw"]
        for row in db.reviewed_lang_glossary(mod, "1.21.1", (text,))
    }


def test_pack_glossary_falls_back_to_the_lang_of_the_module_name(db):
    _add_term(db, KIND_LANG, "foo", "block.pc", "Pressure Chamber", "壓力室")

    assert _glossary(db, PACK, "Build a Pressure Chamber now") == {
        "Pressure Chamber": "壓力室"
    }


def test_pack_glossary_same_kind_term_is_not_overridden_by_lang(db):
    _add_term(db, KIND_LANG, "foo", "block.pc", "Pressure Chamber", "壓力室")
    _add_term(db, KIND_KUBEJS_LANG, PACK, "k.pc", "Pressure Chamber", "壓力艙")

    assert _glossary(db, PACK, "Build a Pressure Chamber now") == {
        "Pressure Chamber": "壓力艙"
    }


def test_pack_glossary_ignores_the_lang_of_other_modules(db):
    _add_term(db, KIND_LANG, "bar", "block.pc", "Pressure Chamber", "壓力室")

    assert _glossary(db, PACK, "Build a Pressure Chamber now") == {}


def test_plain_module_glossary_is_unchanged(db):
    _add_term(db, KIND_LANG, "foo", "block.pc", "Pressure Chamber", "壓力室")

    assert _glossary(db, "foo", "Build a Pressure Chamber now") == {
        "Pressure Chamber": "壓力室"
    }
