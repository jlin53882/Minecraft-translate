"""官方術語「單獨成句」allowlist：只放行審核過的安全術語，其餘維持不命中。"""

from __future__ import annotations

import pytest

from translation_tool.core import official_locale
from translation_tool.core.official_locale import (
    build_translation_prompt,
    load_official_locale_index,
    terminology_matches_for_item,
    terminology_terms_for_item,
)
from translation_tool.core.terminology_model import (
    MATCH_STANDALONE,
    STANDALONE_ALLOWLIST,
    USAGE_STANDALONE,
    evidence_context_entry,
    evidence_limitations,
)
from translation_tool.utils.text_matching import term_canonical_key


@pytest.fixture
def index():
    """共用的官方索引；命中結果有快取，這裡的測試會改 allowlist，所以每個測試前後都清掉。"""
    shared = load_official_locale_index()
    shared._match_cache.clear()
    yield shared
    shared._match_cache.clear()


def _matches(text: str, index, **extra):
    return terminology_matches_for_item(
        {"path": "custom:a", "source_text": text, **extra}, index=index
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("Netherite", "獄髓"),
        ("Iron", "鐵"),
        ("Gold", "金"),
        ("Copper", "銅"),
        ("§aNetherite§r", "獄髓"),
        ("  copper ", "銅"),
    ],
)
def test_allowlisted_standalone_terms_match(index, source, target) -> None:
    (match,) = _matches(source, index)

    assert match.target_text == target
    assert match.match_type == MATCH_STANDALONE
    assert match.usage == USAGE_STANDALONE
    assert match.source_key  # 保留官方推導來源 key 作為 provenance


@pytest.mark.parametrize("source", ["Max", "Wood", "Planks", "Wooden", "Block", "Step"])
def test_non_allowlisted_standalone_terms_stay_unmatched(index, source) -> None:
    assert _matches(source, index) == []
    assert terminology_terms_for_item({"source_text": source}, index=index) == []


def test_blocker_is_not_mistaken_for_block(index) -> None:
    assert _matches("Blocker", index) == []


def test_full_netherite_sword_is_unchanged(index) -> None:
    (match,) = _matches("Netherite Sword", index)

    assert (match.target_text, match.match_type) == ("獄髓劍", "exact_text")


def test_longer_text_partial_matching_is_unchanged(index) -> None:
    (match,) = _matches("Obtain a Netherite Ingot", index)
    assert match.match_type == "official_phrase"

    sentence = _matches("Mine some Iron ore", index)
    assert all(m.match_type != MATCH_STANDALONE for m in sentence)


def test_allowlist_entries_are_derived_from_and_agree_with_official_locale(
    index,
) -> None:
    """allowlist 只是審核過的子集：目標必須與官方複合詞推導一致，且沒有完整翻譯可用。"""
    assert STANDALONE_ALLOWLIST
    for canonical, target in STANDALONE_ALLOWLIST.items():
        assert canonical == term_canonical_key(canonical)
        assert index.derived_terms_by_en[canonical][1] == target
        # 官方語系沒有任何整句恰為該詞的條目（否則會走 exact_text，而不是 allowlist）。
        assert not any(
            term_canonical_key(record.en_us) == canonical
            for record in index.records_by_key.values()
        )


@pytest.mark.parametrize("bad", ["max", "wood", "planks", "block", "step", "wooden"])
def test_dangerous_terms_are_never_allowlisted(bad) -> None:
    assert bad not in STANDALONE_ALLOWLIST


def test_allowlist_target_drift_blocks_match(index, monkeypatch) -> None:
    """官方推導目標與審核值不同時（語系更新後）不放行。"""
    monkeypatch.setattr(
        official_locale, "STANDALONE_ALLOWLIST", {"netherite": "別的譯名"}
    )

    assert _matches("Netherite", index) == []


def test_prompt_and_smart_review_evidence_share_the_resolver(index) -> None:
    prompt = build_translation_prompt(
        "base", [{"path": "custom:a", "source_text": "Netherite"}], profile="lang"
    )
    assert "Netherite → 獄髓" in prompt

    (match,) = _matches("Netherite", index)
    entry = evidence_context_entry(match)
    assert entry["match_type"] == MATCH_STANDALONE
    assert entry["term_origin"] == USAGE_STANDALONE
    assert entry["zh_tw"] == "獄髓"
    assert "人工審核" in evidence_limitations(match)
