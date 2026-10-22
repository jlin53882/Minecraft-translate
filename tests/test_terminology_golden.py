"""官方術語匹配 golden：固定資料欄位、詞界行為，以及 prompt／evidence adapter 輸出。"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from translation_tool.core.official_locale import (
    build_translation_prompt,
    load_official_locale_index,
    terminology_matches_for_item,
    terminology_terms_for_item,
)
from translation_tool.core.terminology_model import (
    TerminologyMatch,
    evidence_context_entry,
    prompt_glossary_key,
    prompt_term_pair,
)

_GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "terminology_golden.json").read_text(
        encoding="utf-8"
    )
)["cases"]
_FIELDS = (
    "source_text",
    "target_text",
    "authority",
    "usage",
    "match_type",
    "confidence",
    "source_key",
)


def _params() -> list:
    params = []
    for case in _GOLDEN:
        marks = []
        if case.get("xfail"):
            marks.append(pytest.mark.xfail(strict=True, reason=case["xfail"]))
        params.append(pytest.param(case, id=case["id"], marks=marks))
    return params


@pytest.fixture(scope="module")
def index():
    return load_official_locale_index()


@pytest.mark.parametrize("case", _params())
def test_matches_follow_golden(case, index) -> None:
    matches = terminology_matches_for_item(case["item"], index=index)

    assert [{k: getattr(m, k) for k in _FIELDS} for m in matches] == case["expected"]


@pytest.mark.parametrize("case", _params())
def test_prompt_terms_follow_golden(case, index) -> None:
    terms = terminology_terms_for_item(case["item"], index=index)

    assert [list(term) for term in terms] == case["prompt_terms"]


def test_terminology_match_fields_are_frozen_and_fixed() -> None:
    assert [f.name for f in dataclasses.fields(TerminologyMatch)] == [
        "source_text",
        "target_text",
        "authority",
        "usage",
        "match_type",
        "confidence",
        "source_key",
        "evidence_id",
    ]
    match = TerminologyMatch("a", "b", "x", "y", "z", "high", "k", "id")
    with pytest.raises(dataclasses.FrozenInstanceError):
        match.source_text = "changed"  # type: ignore[misc]


def test_evidence_ids_are_stable_and_unique(index) -> None:
    item = {"path": "custom:a", "source_text": "Obtain a Netherite-Diamond Block"}
    first = terminology_matches_for_item(item, index=index)
    second = terminology_matches_for_item(dict(item), index=index)

    assert [m.evidence_id for m in first] == [m.evidence_id for m in second]
    assert len({m.evidence_id for m in first}) == len(first)
    assert all(m.evidence_id.startswith("official-term:") for m in first)


def test_evidence_adapter_keeps_smart_review_entry_shape(index) -> None:
    (match,) = terminology_matches_for_item(
        {"path": "block.minecraft.cobblestone", "source_text": "Cobblestone"},
        index=index,
    )

    assert evidence_context_entry(match) == {
        "id": match.evidence_id,
        "reference_type": "official_locale",
        "mod_id": "minecraft",
        "kind": "lang",
        "key": "block.minecraft.cobblestone",
        "mc_version": "bundled",
        "en_us": "Cobblestone",
        "zh_tw": "鵝卵石",
        "source_text": "Cobblestone",
        "target_text": "鵝卵石",
        "review_status": "reviewed",
        "source": "bundled_official_locale",
        "source_label": "官方 Minecraft",
        "match_type": "exact_key",
        "term_origin": "official_full_translation",
        "confidence": "high",
        "limitations": "官方 Minecraft 完整翻譯；高信心",
    }


def test_evidence_limitations_by_usage(index) -> None:
    matches = terminology_matches_for_item(
        {"path": "custom:a", "source_text": "Netherite Block of Diamond"}, index=index
    )
    limitations = {
        m.source_text: evidence_context_entry(m)["limitations"] for m in matches
    }

    assert limitations == {
        "Block of Diamond": "官方 Minecraft 完整片語；高信心",
        "Netherite": "官方 Minecraft 術語；由官方複合詞拆解；高信心",
    }


def test_prompt_adapter_shows_key_only_for_full_translations(index) -> None:
    full = terminology_matches_for_item(
        {"path": "block.minecraft.cobblestone", "source_text": "Cobblestone"},
        index=index,
    )[0]
    part = terminology_matches_for_item(
        {"path": "custom:a", "source_text": "Click to open the Chest"}, index=index
    )[0]

    assert prompt_glossary_key(full) == "block.minecraft.cobblestone"
    assert prompt_glossary_key(part) == ""
    assert prompt_term_pair(part) == ("Chest", "儲物箱")


def test_translation_prompt_text_is_unchanged() -> None:
    items = [
        {"path": "block.minecraft.cobblestone", "source_text": "Cobblestone"},
        {"path": "custom:a", "source_text": "Click to open the Chest"},
    ]

    prompt = build_translation_prompt("基礎", items, profile="lang")

    assert (
        "- id「0」 key「block.minecraft.cobblestone」：Cobblestone → 鵝卵石" in prompt
    )
    assert "- id「1」 key「custom:a」：Chest → 儲物箱" in prompt
    assert "- key「block.minecraft.cobblestone」：Cobblestone → 鵝卵石" in prompt
    assert "- Chest → 儲物箱" in prompt


def test_smart_review_context_uses_the_evidence_adapter():
    """智慧校對的官方 evidence 只能由 adapter 產生，不得在 context 模組重複組欄位。"""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "translation_tool/translation_db/smart_review_context.py"
    ).read_text(encoding="utf-8")
    assert "evidence_context_entry(" in source
    assert "bundled_official_locale" not in source
    assert "官方 Minecraft 完整翻譯" not in source
