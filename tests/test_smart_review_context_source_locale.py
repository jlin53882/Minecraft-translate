"""智慧校對的本地證據必須與候選的來源語系相容。

與翻譯端相同的規則（``reviewed_locale_compatible``）：en_us 的已審核「Pasta→義大利麵」
不是 pt_br「Pasta」的證據；語系未知的參考不相容；官方 Minecraft 術語是英文，
只用於 en_us 或語系未知的候選。
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


def _scan(mod, key, en, zh, locale, *, same_pack="pack-a", selector=None, path=None):
    return ScanItem(
        KIND_KUBEJS_LANG,
        mod,
        key,
        en,
        zh,
        source_path=path or f"kubejs/assets/{mod}/lang/{locale or 'x'}.json",
        source_selector=selector or f"lang:{locale}:{key}",
        source_hash=f"h-{mod}-{key}",
        source_format="lang-json",
        pack_scope=same_pack,
        source_locale=locale,
    )


def _review_all(db):
    for row in db.list_entries(V, limit=None)[0]:
        if row.zh_tw:
            db.review_manual(row.id, expected_zh_tw=row.zh_tw, propagate=False)


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "ctx-locale.db")
    yield database
    database.close()


def _context(db, key):
    row = next(e for e in db.list_entries(V, limit=None)[0] if e.key == key)
    db._smart_review_context_cache = {}
    return db._smart_review_context(row.id, row.kind, V, row.mod_id, row.key, row.en_us)


def _targets(context, *reference_types):
    return {
        item["zh_tw"]
        for item in context
        if item["reference_type"] in reference_types and item.get("zh_tw")
    }


GLOSSARY = ("reviewed_lang_glossary",)
SAME_TEXT = ("same_text_reviewed",)
OFFICIAL = ("official_locale_exact", "official_locale_term", "official_locale")


def _seed_pasta(db, *, en_ref_mod="demo@pack-a"):
    db.ingest(
        V,
        [
            _scan(en_ref_mod, "ref.pasta", "Pasta Maker", "義大利麵機", "en_us"),
            _scan("demo@pack-a", "tip.pt", "Cook with the Pasta Maker", "", "pt_br"),
            _scan("demo@pack-a", "tip.en", "Cook with the Pasta Maker", "", "en_us"),
        ],
    )
    _review_all(db)


def test_en_us_glossary_term_is_not_evidence_for_a_pt_br_candidate(db):
    _seed_pasta(db)

    assert "義大利麵機" in _targets(_context(db, "tip.en"), *GLOSSARY)
    assert _targets(_context(db, "tip.pt"), *GLOSSARY) == set()


def test_pt_br_reference_is_evidence_only_for_pt_br(db):
    db.ingest(
        V,
        [
            _scan("demo@pack-a", "ref.pt", "Pasta Maker", "資料夾機", "pt_br"),
            _scan("demo@pack-a", "tip.pt", "Cook with the Pasta Maker", "", "pt_br"),
            _scan("demo@pack-a", "tip.en", "Cook with the Pasta Maker", "", "en_us"),
        ],
    )
    _review_all(db)

    assert "資料夾機" in _targets(_context(db, "tip.pt"), *GLOSSARY)
    assert _targets(_context(db, "tip.en"), *GLOSSARY) == set()


def test_same_text_reference_must_share_the_source_locale(db):
    db.ingest(
        V,
        [
            _scan("other@pack-a", "ref.en", "Pasta", "義大利麵", "en_us"),
            _scan("demo@pack-a", "tip.pt", "Pasta", "", "pt_br"),
            _scan("demo@pack-a", "tip.en", "Pasta", "", "en_us"),
        ],
    )
    _review_all(db)

    # 完整相同的原文可能由術語表或同文證據任一條路徑帶入（同一列只保留一筆）
    evidence = (*GLOSSARY, *SAME_TEXT)
    assert "義大利麵" in _targets(_context(db, "tip.en"), *evidence)
    assert _targets(_context(db, "tip.pt"), *evidence) == set()


def test_module_name_lang_fallback_respects_the_locale(db):
    db.ingest(
        V,
        [
            ScanItem(
                KIND_LANG,
                "demo",
                "block.pasta",
                "Pasta Maker",
                "義大利麵機",
                source_path="x/en_us.json",
                source_selector="lang:en_us:block.pasta",
                source_hash="h1",
                source_format="lang-json",
                pack_scope="",
                source_locale="en_us",
            ),
            _scan("demo@pack-a", "tip.pt", "Cook with the Pasta Maker", "", "pt_br"),
            _scan("demo@pack-a", "tip.en", "Cook with the Pasta Maker", "", "en_us"),
        ],
    )
    _review_all(db)

    assert "義大利麵機" in _targets(_context(db, "tip.en"), *GLOSSARY)
    assert _targets(_context(db, "tip.pt"), *GLOSSARY) == set()


def test_unknown_reference_locale_is_never_evidence(db):
    db.ingest(
        V,
        [
            _scan(
                "demo@pack-a",
                "ref.unknown",
                "Pasta Maker",
                "義大利麵機",
                None,
                selector="mystery",
                path="kubejs/misc/data.json",
            ),
            _scan("demo@pack-a", "tip.en", "Cook with the Pasta Maker", "", "en_us"),
        ],
    )
    _review_all(db)

    assert _targets(_context(db, "tip.en"), *GLOSSARY) == set()


def test_official_english_terms_only_apply_to_english_or_unknown_candidates(db):
    db.ingest(
        V,
        [
            _scan("demo@pack-a", "tip.pt", "Iron Ingot", "", "pt_br"),
            _scan("demo@pack-a", "tip.en", "Iron Ingot", "", "en_us"),
        ],
    )

    en_context = _context(db, "tip.en")
    pt_context = _context(db, "tip.pt")

    def official(ctx):
        return [i for i in ctx if str(i["reference_type"]).startswith("official")]

    assert official(en_context)
    assert official(pt_context) == []


def test_legacy_entries_without_origins_keep_their_evidence(db):
    """升級前沒有來源紀錄的條目（jar 的英文）：行為不變。"""
    db.write_back(
        V,
        [
            WriteBackItem(
                KIND_LANG, "demo", "block.pasta", "Pasta Maker", "義大利麵機"
            ),
            WriteBackItem(
                KIND_LANG, "demo", "tip.cook", "Cook with the Pasta Maker", "烹調"
            ),
        ],
        fill_other_versions=False,
    )
    ref = next(e for e in db.list_entries(V, limit=None)[0] if e.key == "block.pasta")
    db.review_manual(ref.id, expected_zh_tw=ref.zh_tw, propagate=False)

    assert "義大利麵機" in _targets(_context(db, "tip.cook"), *GLOSSARY)


def test_effective_locale_rule_for_kinds_without_a_locale_concept():
    from translation_tool.core.source_locale import (
        effective_source_locale,
        reviewed_locale_compatible,
    )

    assert effective_source_locale("kubejs_script", None) == "en_us"
    assert effective_source_locale("ftb_quests", "") == "en_us"
    assert effective_source_locale("kubejs_lang", None) is None
    assert effective_source_locale("kubejs_script", "pt-BR") == "pt_br"
    # 腳本字串視為英文：可用 en_us 的已審核參考，但不會用 pt_br 的
    assert reviewed_locale_compatible(
        effective_source_locale("kubejs_script", None), "en_us"
    )
    assert not reviewed_locale_compatible(
        effective_source_locale("kubejs_script", None), "pt_br"
    )
