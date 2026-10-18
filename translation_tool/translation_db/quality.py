"""Translation format quality and compatibility adapters for storage and UI."""

from __future__ import annotations

from collections import Counter

from translation_tool.core.format_diagnostics import (
    analyze_format_diagnostics,
    format_identifier_label,
    format_kind_policy,
)
from translation_tool.core.format_tokens import (
    format_token_spans,
    format_tokens,  # noqa: F401 - compatibility re-export
    token_category,
)
from translation_tool.core.guide_format import guide_issues
from translation_tool.translation_db.models import QualityFilter, QualityIssueDelta
from translation_tool.utils.species_names import (
    is_potential_species_name,
    is_species_candidate,
)


def _token_counts_for_kind(text: str, kind: str | None) -> Counter[str]:
    counts = format_tokens(text)
    if format_kind_policy(kind).newline_policy == "soft":
        counts.pop("\n", None)
    return counts


def _token_issues_from_counts(
    wanted: Counter[str], actual: Counter[str]
) -> list[str]:
    """Compatibility adapter for callers that already computed token counts."""
    issues: list[str] = []
    for token in sorted(set(wanted) | set(actual)):
        delta = actual[token] - wanted[token]
        if delta:
            name = format_identifier_label(token, token_category(token))
            issues.append(
                f"{'多了' if delta > 0 else '少了'} {abs(delta)} 個「{name}」"
            )
    return issues


def token_issues(source: str, translated: str) -> list[str]:
    """Legacy token-only entry point backed by the common scanner."""
    return token_issues_for_kind(source, translated, kind="lang")


def token_issues_for_kind(
    source: str, translated: str, *, kind: str | None = None
) -> list[str]:
    """Validate protected tokens while applying the kind newline policy."""
    return _token_issues_from_counts(
        _token_counts_for_kind(source, kind),
        _token_counts_for_kind(translated, kind),
    )


def guide_structure_issues(
    source: str, translated: str, *, kind: str | None
) -> list[str]:
    """Return Guide/Markdown hard-structure differences for structured kinds."""
    if not format_kind_policy(kind).has_structured_marks or not source:
        return []
    return guide_issues(source, translated or "")


def repair_input_issues(
    source: str, previous_translation: str
) -> tuple[bool, tuple[str, ...], bool]:
    """Legacy token-only repair gate."""
    return repair_input_issues_for_kind(source, previous_translation, kind="lang")


def repair_input_issues_for_kind(
    source: str, previous_translation: str, *, kind: str | None = None
) -> tuple[bool, tuple[str, ...], bool]:
    """Return repair eligibility, hard issues, and mixed-newline status."""
    wanted = format_tokens(source)
    previous = format_tokens(previous_translation)
    newline_mismatch = wanted["\n"] != previous["\n"]
    policy = format_kind_policy(kind)
    if policy.has_structured_marks:
        token_issues_found = token_issues_for_kind(
            source, previous_translation, kind=kind
        )
    else:
        token_issues_found = _token_issues_from_counts(wanted, previous)
    structure_issues = guide_structure_issues(
        source, previous_translation, kind=kind
    )
    issues = (*token_issues_found, *structure_issues)
    eligible = bool(issues) if policy.newline_policy == "soft" else bool(
        newline_mismatch or issues
    )
    return eligible, issues, bool(newline_mismatch and issues)


def repair_input_issues_from_counts(
    wanted: Counter[str], previous: Counter[str]
) -> tuple[bool, tuple[str, ...], bool]:
    """``repair_input_issues`` for callers that already counted tokens."""
    newline_mismatch = wanted["\n"] != previous["\n"]
    wanted.pop("\n", None)
    previous.pop("\n", None)
    hard_issues = tuple(_token_issues_from_counts(wanted, previous))
    return (
        newline_mismatch or bool(hard_issues),
        hard_issues,
        bool(newline_mismatch and hard_issues),
    )


def repair_output_issues(source: str, translated: str) -> list[str]:
    """Legacy repair-output gate that allows actual newline count changes."""
    return repair_output_issues_for_kind(source, translated, kind="lang")


def repair_output_issues_for_kind(
    source: str,
    translated: str,
    *,
    kind: str | None = None,
    allow_actual_newlines: bool = True,
) -> list[str]:
    """Validate repair output with an explicit actual-newline policy."""
    result = analyze_format_diagnostics(
        source, translated, kind=kind or "lang", repair=True
    )
    return [
        issue.legacy_message
        for issue in result.diagnostics
        if not (
            allow_actual_newlines
            and issue.category == "newline"
            and issue.identifier == "\n"
        )
    ] + [
        issue
        for issue in guide_structure_issues(source, translated, kind=kind)
        if issue not in result.legacy_messages
    ]


def translation_issues(
    source: str,
    translated: str,
    *,
    kind: str | None = None,
    repair: bool = False,
) -> list[str]:
    """Return common format and Guide structure diagnostics as stable messages."""
    if repair:
        return repair_output_issues_for_kind(
            source,
            translated,
            kind=kind,
            allow_actual_newlines=True,
        )
    result = analyze_format_diagnostics(
        source, translated, kind=kind or "lang"
    )
    return list(result.legacy_messages) + [
        issue
        for issue in guide_structure_issues(source, translated, kind=kind)
        if issue not in result.legacy_messages
    ]


def _diagnostic_map(result):
    return {
        (issue.category, issue.identifier, issue.direction): (abs(issue.delta), issue)
        for issue in result.diagnostics
    }


def _token_issue_deltas_from_results(
    before_result, after_result
) -> tuple[QualityIssueDelta, ...]:
    before = _diagnostic_map(before_result)
    after = _diagnostic_map(after_result)
    deltas: list[QualityIssueDelta] = []
    for key in sorted(set(before) | set(after)):
        old_count, old_issue = before.get(key, (0, None))
        new_count, new_issue = after.get(key, (0, None))
        if old_count == new_count:
            continue
        selected = new_issue or old_issue
        assert selected is not None
        deltas.append(
            QualityIssueDelta(
                selected.identifier,
                selected.direction,
                old_count,
                new_count,
                category=selected.category,
                identifier=selected.identifier,
                display=selected.display_token,
            )
        )
    return tuple(deltas)


def format_quality_comparison(
    source: str,
    before: str,
    after: str,
    *,
    kind: str | None = None,
) -> tuple[list[str], list[str], tuple[QualityIssueDelta, ...]]:
    """Compare batch quality through the common kind-aware diagnostics contract."""
    before_result = analyze_format_diagnostics(
        source, before, kind=kind or "lang"
    )
    after_result = analyze_format_diagnostics(source, after, kind=kind or "lang")
    return (
        translation_issues(source, before, kind=kind),
        translation_issues(source, after, kind=kind),
        _token_issue_deltas_from_results(before_result, after_result),
    )


def token_quality_comparison(
    source: str,
    before: str,
    after: str,
    *,
    kind: str | None = None,
) -> tuple[list[str], list[str], tuple[QualityIssueDelta, ...]]:
    """Legacy name for :func:`format_quality_comparison`."""
    return format_quality_comparison(source, before, after, kind=kind)


def whitespace_note(text: str) -> str:
    """Describe leading/trailing whitespace without modifying stored text."""
    if not text or text == text.strip():
        return ""
    return "譯文前後有空白或換行，儲存時會原樣保留"


def quality_state(
    source: str, translated: str, *, kind: str | None = None
) -> tuple[str, tuple[str, ...], str]:
    """Return status, diagnostics and whitespace warning for one effective value."""
    if not source:
        return "unknown_source", (), whitespace_note(translated)
    if not translated:
        return "missing_translation", (), ""
    issues = tuple(translation_issues(source, translated, kind=kind))
    note = whitespace_note(translated)
    if issues:
        return "mismatch", issues, note
    return ("whitespace" if note else "consistent"), issues, note


def matches_quality(
    source: str,
    translated: str,
    criteria: QualityFilter,
    key: str = "",
    *,
    kind: str | None = None,
) -> bool:
    """Match one quality filter while keeping the fast paths for large scans."""
    if not criteria.active:
        return True
    status = "mismatch" if criteria.status == "all" else criteria.status
    if status == "species_name":
        return bool(source) and is_species_candidate(key, source)
    if status == "species_format":
        return bool(source) and is_potential_species_name(source)
    if status == "unknown_source":
        return not source
    if status == "missing_translation":
        return bool(source) and not translated
    if status == "whitespace":
        return bool(translated) and bool(whitespace_note(translated))
    if status not in {"consistent", "mismatch"} or not source or not translated:
        return False

    policy = format_kind_policy(kind)
    if not policy.has_structured_marks and not (
        format_token_spans(source) or format_token_spans(translated)
    ):
        return status == "consistent"

    result = analyze_format_diagnostics(source, translated, kind=kind or "lang")
    if status == "consistent":
        return not translation_issues(source, translated, kind=kind)

    structure_issues = guide_structure_issues(source, translated, kind=kind)
    direction = criteria.direction
    if direction not in {"all", "missing", "extra"}:
        raise ValueError(f"未知 token 不一致方向：{direction}")
    category = criteria.token_category
    if category not in {"all", "placeholder", "minecraft", "patchouli", "newline"}:
        raise ValueError(f"未知 token 類別：{category}")
    if structure_issues and direction == "all" and category == "all":
        return True

    def selected(direction_name: str) -> bool:
        return any(
            issue.direction == direction_name
            and (category == "all" or issue.category == category)
            for issue in result.diagnostics
        )

    return (direction in {"all", "missing"} and selected("missing")) or (
        direction in {"all", "extra"} and selected("extra")
    )


__all__ = [
    "format_quality_comparison",
    "format_token_spans",
    "format_tokens",
    "guide_structure_issues",
    "matches_quality",
    "quality_state",
    "repair_input_issues",
    "repair_input_issues_for_kind",
    "repair_input_issues_from_counts",
    "repair_output_issues",
    "repair_output_issues_for_kind",
    "token_issues",
    "token_issues_for_kind",
    "token_quality_comparison",
    "token_category",
    "translation_issues",
    "whitespace_note",
]
