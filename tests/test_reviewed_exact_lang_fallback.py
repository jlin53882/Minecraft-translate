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


def test_lang_wins_over_a_conflicting_same_kind_translation(db):
    """lang 是最根本的對照：同種類譯法不同時，以 lang 為準。"""
    _add(db, KIND_LANG, "foo", "block.pressure", "壓力室會處理空氣。")
    _add(db, KIND_KUBEJS_LANG, "foo", "k.pressure", "壓力艙處理空氣。")

    assert [r["zh_tw"] for r in _exact(db, "foo", KIND_KUBEJS_LANG)] == [
        "壓力室會處理空氣。"
    ]


def test_ambiguous_lang_does_not_fall_back_to_the_same_kind(db):
    _add(db, KIND_LANG, "foo", "a.pressure", "壓力室會處理空氣。")
    _add(db, KIND_LANG, "foo", "b.pressure", "壓力艙會處理空氣。")
    _add(db, KIND_KUBEJS_LANG, "foo", "k.pressure", "壓力室處理空氣。")

    assert _exact(db, "foo", KIND_KUBEJS_LANG) == []


def test_unreviewed_lang_is_not_used(db):
    _add(db, KIND_LANG, "foo", "block.pressure", "壓力室會處理空氣。", reviewed=False)

    assert _exact(db, "foo", KIND_KUBEJS_LANG) == []


def test_other_module_or_version_is_not_used(db):
    _add(db, KIND_LANG, "bar", "block.pressure", "壓力室會處理空氣。")
    _add(db, KIND_LANG, "foo", "block.pressure", "壓力室會處理空氣。", version="1.20.1")

    assert _exact(db, "foo", KIND_KUBEJS_LANG) == []
