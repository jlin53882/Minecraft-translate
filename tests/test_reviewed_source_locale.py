"""已審核參考（整句重用與術語表）必須與待翻譯項目的來源語系相容。

同形異義詞（例如 en_us 的「Pasta」是義大利麵、pt_br 的「Pasta」是資料夾）若不分語系，
en_us 的已審核譯文會被直接當成 pt_br 項目的完整句答案並跳過模型。
未知來源不得自動視為相容。
"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_KUBEJS_LANG,
    KIND_LANG,
    ScanItem,
    TranslationDB,
    WriteBackItem,
)

V = "1.21.1"


def _scan(key, en, zh, locale, *, selector=None, path=None):
    return ScanItem(
        KIND_KUBEJS_LANG,
        "foo",
        key,
        en,
        zh,
        source_path=path or f"kubejs/assets/foo/lang/{locale or 'x'}.json",
        source_selector=selector or f"lang:{locale}:{key}",
        source_hash=f"h-{key}",
        source_format="lang-json",
        pack_scope="",
        source_locale=locale,
    )


def _review_all(db):
    for row in db.list_entries(V, limit=None)[0]:
        if row.zh_tw:
            db.review_manual(row.id, expected_zh_tw=row.zh_tw, propagate=False)


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "locale.db")
    yield database
    database.close()


def _exact(db, locale, kind=KIND_KUBEJS_LANG):
    return db.reviewed_exact_glossary("foo", V, kind, ("Pasta",), source_locale=locale)


def _terms(db, locale):
    return db.reviewed_lang_glossary(
        "foo", V, ("Cook the Pasta",), source_locale=locale
    )


def _targets(rows):
    return [row["zh_tw"] for row in rows]


def test_en_us_reference_is_not_used_for_a_pt_br_item(db):
    db.ingest(V, [_scan("a.pasta", "Pasta", "義大利麵", "en_us")])
    _review_all(db)

    assert _targets(_exact(db, "en_us")) == ["義大利麵"]
    assert _exact(db, "pt_br") == []
    assert _terms(db, "pt_br") == []


def test_each_locale_gets_only_its_own_reviewed_sentence(db):
    """同形異義詞：兩個語系各有自己的已審核譯文，互不衝突、互不混用。"""
    db.ingest(
        V,
        [
            _scan("a.pasta", "Pasta", "義大利麵", "en_us"),
            _scan("b.pasta", "Pasta", "資料夾", "pt_br"),
        ],
    )
    _review_all(db)

    assert _targets(_exact(db, "en_us")) == ["義大利麵"]
    assert _targets(_exact(db, "pt_br")) == ["資料夾"]
    assert _targets(_terms(db, "en_us")) == ["義大利麵"]
    assert _targets(_terms(db, "pt_br")) == ["資料夾"]


def test_unknown_item_locale_is_not_compatible_with_a_known_reference(db):
    db.ingest(V, [_scan("a.pasta", "Pasta", "義大利麵", "en_us")])
    _review_all(db)

    assert _exact(db, None) == []
    assert _terms(db, None) == []


def test_unknown_reference_locale_is_never_compatible(db):
    """有來源紀錄卻無法確認語系的參考：任何項目語系都不相容。"""
    db.ingest(
        V,
        [
            _scan(
                "a.pasta",
                "Pasta",
                "義大利麵",
                None,
                selector="mystery-selector",
                path="kubejs/misc/data.json",
            )
        ],
    )
    _review_all(db)

    for locale in ("en_us", "pt_br", None):
        assert _exact(db, locale) == []
        assert _terms(db, locale) == []


def test_legacy_entries_without_origins_keep_working(db):
    """升級前的條目沒有來源紀錄（jar 掃描的英文）：項目語系是 en_us 或未知時照舊可重用。"""
    db.write_back(V, [WriteBackItem(KIND_LANG, "foo", "a.pasta", "Pasta", "義大利麵")])
    _review_all(db)

    for locale in (None, "en_us"):
        assert _targets(_exact(db, locale, KIND_LANG)) == ["義大利麵"], locale
    assert _exact(db, "pt_br", KIND_LANG) == []
    assert _targets(_terms(db, None)) == ["義大利麵"]
    assert _terms(db, "pt_br") == []
