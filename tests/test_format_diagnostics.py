from __future__ import annotations

import pytest

from app.services_impl.moddb_format_diff_service import compare_format_diff
from translation_tool.core.format_diagnostics import (
    FormatAnalysis,
    FormatDiagnostic,
    FormatKindPolicy,
    analyze_format_diagnostics,
    format_kind_policy,
    register_format_kind,
)
from translation_tool.core.format_tokens import format_tokens as core_format_tokens
from translation_tool.core.guide_format import guide_issues
from translation_tool.translation_db.models import BatchReplaceChange, QualityFilter
from translation_tool.translation_db.quality import (
    format_quality_comparison,
    matches_quality,
    quality_state,
)
from translation_tool.translation_db.quality import (
    format_tokens as quality_format_tokens,
)


def test_registered_kinds_keep_cache_and_newline_policies_explicit():
    assert format_kind_policy("lang").cache_type == "lang"
    assert format_kind_policy("guide").cache_type == "patchouli"
    assert format_kind_policy("patchouli").newline_policy == "soft"
    advancement = format_kind_policy("advancement")
    assert advancement.cache_type == "patchouli"
    assert advancement.analyzer_name == "token"
    assert callable(advancement.analyzer)
    assert advancement.newline_policy == "hard"


def test_guide_diagnostics_include_structured_marks_and_exact_spans():
    source = "# Title\nSee <br> [label](target) **bold**"
    translated = "標題\nSee [label](other) **bold**"

    result = analyze_format_diagnostics(source, translated, kind="guide")
    identifiers = {
        (issue.category, issue.identifier, issue.direction) for issue in result.issues
    }

    assert ("guide", "heading:#", "missing") in identifiers
    assert ("guide", "tag:<br>", "missing") in identifiers
    assert ("guide", "link:target", "missing") in identifiers
    assert ("guide", "link:other", "extra") in identifiers
    heading = next(issue for issue in result.issues if issue.identifier == "heading:#")
    assert heading.location_confidence == "exact"
    assert heading.source_occurrences[0].line == 1
    assert heading.source_occurrences[0].column == 1


def test_guide_legacy_wrapper_uses_the_same_structured_contract():
    source = "## Heading\n- item"
    translated = "標題\nitem"
    result = analyze_format_diagnostics(source, translated, kind="guide")
    assert tuple(guide_issues(source, translated)) == result.legacy_messages
    assert quality_state(source, translated, kind="guide")[0] == "mismatch"
    assert matches_quality(
        source,
        translated,
        QualityFilter(status="mismatch"),
        kind="guide",
    )


def test_soft_newlines_are_ignored_for_guide_and_patchouli():
    source = "Paragraph one\nParagraph two"
    translated = "段落一\n段落二"
    assert not analyze_format_diagnostics(source, translated, kind="guide").has_issues
    assert not analyze_format_diagnostics(
        source, translated, kind="patchouli"
    ).has_issues


def test_advancement_uses_token_contract_and_hard_newlines_without_guide_parser():
    newline = analyze_format_diagnostics("A\nB", "AB", kind="advancement")
    assert [(issue.category, issue.identifier) for issue in newline.issues] == [
        ("newline", "\n")
    ]
    assert not analyze_format_diagnostics(
        "# Heading", "Heading", kind="advancement"
    ).has_issues


def test_lang_contract_keeps_literal_newline_distinct_from_actual_newline():
    result = compare_format_diff("A\nB\\n%s", "AB%s")
    assert {(issue.identifier, issue.direction) for issue in result.issues} == {
        ("\\n", "missing"),
        ("\n", "missing"),
    }


def test_batch_comparison_has_stable_guide_and_token_delta_identity():
    before, _after, deltas = format_quality_comparison(
        "## Heading %s",
        "標題",
        "## 標題 %s %s",
        kind="guide",
    )
    assert any("標題前綴" in issue for issue in before)
    assert any("%s" in issue for issue in before)
    assert {
        (delta.category, delta.identifier, delta.direction) for delta in deltas
    } == {
        ("guide", "heading:##", "missing"),
        ("placeholder", "%s", "missing"),
        ("placeholder", "%s", "extra"),
    }
    row = BatchReplaceChange(
        entry_id=1,
        kind="guide",
        mc_version="1.21.1",
        mod_id="test",
        key="key",
        en_us="## Heading %s",
        old_zh_tw="標題",
        new_zh_tw="## 標題 %s %s",
        effective_source=0,
        effective_checker="",
        effective_review_status=None,
        effective_revision=1,
        effective_updated_at=None,
        manual_zh_tw=None,
        manual_checker=None,
        manual_review_status=None,
        manual_revision=None,
        quality_deltas=deltas,
    )
    assert row.quality_change_kind == "mixed"


def test_future_policy_registration_does_not_claim_an_unimplemented_parser():
    register_format_kind(
        FormatKindPolicy(
            "future-format-test",
            "future-cache",
            "future-prompt",
            "unimplemented",
            "hard",
        )
    )
    with pytest.raises(NotImplementedError):
        analyze_format_diagnostics("source", "translation", kind="future-format-test")


def test_custom_analyzer_is_registered_without_a_dispatch_branch():
    def analyze_custom(
        source: str, translated: str, newline_policy: str
    ) -> FormatAnalysis:
        assert (source, translated, newline_policy) == ("source", "translation", "hard")
        return FormatAnalysis(
            source_occurrences=(),
            target_occurrences=(),
            diagnostics=(
                FormatDiagnostic(
                    identifier="custom:marker",
                    category="custom",
                    direction="missing",
                    source_count=1,
                    target_count=0,
                    delta=-1,
                ),
            ),
        )

    register_format_kind(
        FormatKindPolicy(
            "custom-format-test",
            "custom-cache",
            "custom-prompt",
            analyze_custom,
            "hard",
        )
    )
    policy = format_kind_policy("custom-format-test")
    assert policy.analyzer_name == "analyze_custom"
    assert analyze_format_diagnostics(
        "source", "translation", kind="custom-format-test"
    ).has_issues


def test_unknown_kind_is_an_explicit_legacy_fallback_and_scanner_has_one_owner():
    policy = format_kind_policy("legacy-kind-not-in-registry")
    assert policy.is_legacy_fallback
    assert policy.analyzer_name == "token"
    assert core_format_tokens is quality_format_tokens
