"""Shared special-format comparison for Mod DB editing and repair review."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from translation_tool.core.lm_format_policy import structured_kind
from translation_tool.translation_db.quality import (
    format_token_spans,
    guide_structure_issues,
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
    # 指南條目的結構標記（連結目標、標籤、前綴）；非指南條目為空
    guide_issues: tuple[str, ...] = ()
    # Guide/Patchouli 的一般段落換行可依語意重排；保留數量供 UI 顯示資訊。
    soft_newline_counts: tuple[int, int] | None = None

    @property
    def has_issues(self) -> bool:
        return bool(self.issues or self.guide_issues)

    @property
    def summary_lines(self) -> tuple[str, ...]:
        lines = [*(issue.summary for issue in self.issues), *self.guide_issues]
        if self.soft_newline_counts is not None:
            source_count, target_count = self.soft_newline_counts
            lines.append(
                f"一般段落換行可依語意重排：原文 {source_count}、譯文 {target_count}"
            )
        return tuple(lines) or ("格式檢查通過。",)

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


def compare_format_diff(
    source_text: str, target_text: str, *, kind: str | None = None
) -> FormatDiffResult:
    """Compare the format-token contract, plus guide structure for guide entries.

    Natural language is never compared. ``kind`` selects the guide rules.
    """
    source_text = source_text or ""
    target_text = target_text or ""
    source_occurrences = _occurrences(source_text)
    target_occurrences = _occurrences(target_text)
    source_counts = Counter(item.token for item in source_occurrences)
    target_counts = Counter(item.token for item in target_occurrences)
    source_by_token = _group_occurrences(source_occurrences)
    target_by_token = _group_occurrences(target_occurrences)
    allow_soft_newlines = structured_kind(kind)
    soft_newline_counts = (
        (source_counts.get("\n", 0), target_counts.get("\n", 0))
        if allow_soft_newlines
        and source_counts.get("\n", 0) != target_counts.get("\n", 0)
        else None
    )
    issues = []
    for token in sorted(set(source_counts) | set(target_counts)):
        delta = target_counts[token] - source_counts[token]
        if not delta:
            continue
        if token == "\n" and allow_soft_newlines:
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
        guide_issues=tuple(guide_structure_issues(source_text, target_text, kind=kind)),
        soft_newline_counts=soft_newline_counts,
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
