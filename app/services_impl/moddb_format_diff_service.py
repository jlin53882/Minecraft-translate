"""Shared special-format comparison for Mod DB editing and repair review."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from translation_tool.translation_db.quality import (
    format_token_spans,
    token_category,
)

DiffDirection = Literal["missing", "extra"]
LocationConfidence = Literal["unknown"]


@dataclass(frozen=True)
class FormatTokenOccurrence:
    """A real token occurrence; offsets refer to the original text."""

    token: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class FormatDiffIssue:
    """A count-based format mismatch shared by all Mod DB UI consumers."""

    token: str
    category: str
    direction: DiffDirection
    source_count: int
    target_count: int
    delta: int
    location_confidence: LocationConfidence = "unknown"
    source_occurrences: tuple[FormatTokenOccurrence, ...] = ()
    target_occurrences: tuple[FormatTokenOccurrence, ...] = ()

    @property
    def display_token(self) -> str:
        return format_token_label(self.token)

    @property
    def legacy_message(self) -> str:
        action = "少了" if self.direction == "missing" else "多了"
        return f"{action} {abs(self.delta)} 個「{self.display_token}」"

    @property
    def summary(self) -> str:
        if self.token == "\n":
            return f"換行數量不一致：原文 {self.source_count}、譯文 {self.target_count}"
        action = "缺少" if self.direction == "missing" else "多出"
        return f"{action} {self.display_token} × {abs(self.delta)}（位置無法可靠判定）"


@dataclass(frozen=True)
class FormatDiffResult:
    """Immutable comparison result; no UI marker is included in either text."""

    source_text: str
    target_text: str
    source_counts: tuple[tuple[str, int], ...]
    target_counts: tuple[tuple[str, int], ...]
    source_occurrences: tuple[FormatTokenOccurrence, ...]
    target_occurrences: tuple[FormatTokenOccurrence, ...]
    issues: tuple[FormatDiffIssue, ...]

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    @property
    def summary_lines(self) -> tuple[str, ...]:
        return tuple(issue.summary for issue in self.issues) or ("格式檢查通過。",)

    @property
    def legacy_messages(self) -> tuple[str, ...]:
        return tuple(issue.legacy_message for issue in self.issues)


def format_token_label(token: str) -> str:
    """Use the established labels while keeping concrete tokens visible."""
    if token == "\n":
        return "換行"
    if token == "\\n":
        return "字面 \\n"
    if token == "$(t:…)":
        return "提示文字 $(t:…)"
    return token


def compare_format_diff(source_text: str, target_text: str) -> FormatDiffResult:
    """Compare only the existing format-token contract, never natural language."""
    source_text = source_text or ""
    target_text = target_text or ""
    source_occurrences = _occurrences(source_text)
    target_occurrences = _occurrences(target_text)
    source_counts = Counter(item.token for item in source_occurrences)
    target_counts = Counter(item.token for item in target_occurrences)
    source_by_token = _group_occurrences(source_occurrences)
    target_by_token = _group_occurrences(target_occurrences)
    issues = []
    for token in sorted(set(source_counts) | set(target_counts)):
        delta = target_counts[token] - source_counts[token]
        if not delta:
            continue
        issues.append(
            FormatDiffIssue(
                token=token,
                category=token_category(token),
                direction="extra" if delta > 0 else "missing",
                source_count=source_counts[token],
                target_count=target_counts[token],
                delta=delta,
                source_occurrences=source_by_token.get(token, ()),
                target_occurrences=target_by_token.get(token, ()),
            )
        )
    return FormatDiffResult(
        source_text=source_text,
        target_text=target_text,
        source_counts=tuple(sorted(source_counts.items())),
        target_counts=tuple(sorted(target_counts.items())),
        source_occurrences=source_occurrences,
        target_occurrences=target_occurrences,
        issues=tuple(issues),
    )


def _occurrences(text: str) -> tuple[FormatTokenOccurrence, ...]:
    return tuple(
        FormatTokenOccurrence(
            token=token,
            start=start,
            end=end,
            line=text.count("\n", 0, start) + 1,
            column=start - text.rfind("\n", 0, start),
        )
        for token, start, end in format_token_spans(text)
    )


def _group_occurrences(
    occurrences: tuple[FormatTokenOccurrence, ...],
) -> dict[str, tuple[FormatTokenOccurrence, ...]]:
    grouped: dict[str, list[FormatTokenOccurrence]] = {}
    for occurrence in occurrences:
        grouped.setdefault(occurrence.token, []).append(occurrence)
    return {token: tuple(items) for token, items in grouped.items()}


__all__ = [
    "FormatDiffIssue",
    "FormatDiffResult",
    "FormatTokenOccurrence",
    "compare_format_diff",
    "format_token_label",
]
