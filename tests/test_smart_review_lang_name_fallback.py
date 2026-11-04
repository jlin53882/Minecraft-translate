"""智慧校對：KubeJS／FTB 項目同包沒有的術語，退回 ``@`` 前面模組名稱的 lang。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_KUBEJS_LANG,
    KIND_LANG,
    ScanItem,
    TranslationDB,
)

V = "1.21.1"
PACK = "pack-a"
PACK_MOD = f"foo@{PACK}"


def _item(kind, mod, key, en, zh="", scope=""):
    return ScanItem(
        kind,
        mod,
        key,
        en,
        zh,
        source_path=f"x/{mod}/{key}",
        source_selector=f"lang:en_us:{key}",
        source_hash=f"{mod}-{key}",
        source_format="lang-json",
        pack_scope=scope,
    )


def _review_translated(database):
    for row in database.list_entries(V, limit=None)[0]:
        if row.zh_tw:
            database.review_manual(row.id, expected_zh_tw=row.zh_tw, propagate=False)


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "fallback.db")
    yield database
    database.close()


def _context(database, key="tip.x"):
    row = next(
        entry
        for entry in database.list_entries(V, limit=None)[0]
        if entry.key == key and entry.mod_id == PACK_MOD
    )
    return database._smart_review_context(
        row.id, row.kind, V, row.mod_id, row.key, row.en_us
    )


def _glossary(context):
    return {
        item["en_us"].lower(): item["zh_tw"]
        for item in context
        if item["reference_type"] == "reviewed_lang_glossary"
    }


def _seed(db, *, lang_mod="foo", pack_term=None):
    items = [
        _item(KIND_LANG, lang_mod, "block.pc", "Pressure Chamber", "壓力室"),
        _item(
            KIND_KUBEJS_LANG,
            PACK_MOD,
            "tip.x",
            "Build a Pressure Chamber now",
            "",
            PACK,
        ),
    ]
    if pack_term:
        items.append(
            _item(
                KIND_KUBEJS_LANG,
                PACK_MOD,
                "term.pc",
                "Pressure Chamber",
                pack_term,
                PACK,
            )
        )
    db.ingest(V, items)
    _review_translated(db)


def test_pack_item_gets_the_reviewed_lang_term_of_the_module_name(db):
    _seed(db)

    context = _context(db)

    assert _glossary(context) == {"pressure chamber": "壓力室"}
    item = next(i for i in context if i["reference_type"] == "reviewed_lang_glossary")
    assert item["match_type"] == "whole_term_module_name_lang"


def test_same_pack_term_is_not_overridden_by_the_lang_fallback(db):
    _seed(db, pack_term="壓力艙")

    assert _glossary(_context(db)) == {"pressure chamber": "壓力艙"}


def test_lang_of_another_module_is_not_used(db):
    _seed(db, lang_mod="bar")

    assert _glossary(_context(db)) == {}


def test_indexed_lookup_matches_a_brute_force_scan(db):
    """前綴索引只是加速：結果必須與逐列 ``contains_term`` 比對完全相同。"""
    from translation_tool.core.official_locale import normalize_terminology_text
    from translation_tool.utils.text_matching import contains_term

    terms = {
        "block.pc": ("Pressure Chamber", "壓力室"),
        "block.coil": ("Plasma Coil", "電漿線圈"),
        "block.ep": ("Ender-Pearl Gate", "終界珍珠閘"),
        "item.ab": ("Ab Cd", "甲乙"),
    }
    items = [_item(KIND_LANG, "foo", key, en, zh) for key, (en, zh) in terms.items()]
    texts = [
        "Build a Pressure Chamber now",
        "pressure chambers are big",  # 複數：詞界規則不命中
        "Use the plasma coil, then the Plasma Coil again",
        "An Ender-Pearl Gate and the ender pearl gate",
        "Ab Cd and abcd",
        "Nothing here",
    ]
    items += [
        _item(KIND_KUBEJS_LANG, PACK_MOD, f"tip.{i}", text, "", PACK)
        for i, text in enumerate(texts)
    ]
    db.ingest(V, items)
    _review_translated(db)

    for i, text in enumerate(texts):
        expected = {
            normalize_terminology_text(en).strip().lower(): zh
            for en, zh in terms.values()
            if contains_term(
                normalize_terminology_text(text), normalize_terminology_text(en)
            )
        }
        assert _glossary(_context(db, f"tip.{i}")) == expected, text
