"""Shared presentation helpers for format diagnostics."""

from __future__ import annotations

from collections.abc import Iterable

import flet as ft

from app.services_impl.moddb_format_diff_service import FormatDiagnostic, FormatSpan
from app.ui.design import C


def diagnostic_tone(issue: FormatDiagnostic) -> str:
    return "red" if issue.direction == "extra" else "gold"


def diagnostic_label(issue: FormatDiagnostic) -> str:
    if issue.category == "newline" and issue.identifier == "\n":
        return f"換行數量不一致 · 原文 {issue.source_count} / 譯文 {issue.target_count}"
    action = "缺少" if issue.direction == "missing" else "多出"
    return f"{action} {issue.display_token} × {abs(issue.delta)}"


def diagnostic_spans(
    text: str,
    occurrences: Iterable[FormatSpan],
    affected_identifiers: set[str],
    *,
    highlight: bool = True,
    issue_color: str = C.RED,
    show_line_breaks: bool = False,
) -> list[ft.TextSpan]:
    """Render original text and emphasize only known diagnostic occurrences."""

    def visible(value: str) -> str:
        return (
            value.replace("\r\n", "\n").replace("\n", "↵\n")
            if show_line_breaks
            else value
        )

    if not highlight:
        return [ft.TextSpan(visible(text or "（無）"))]
    spans: list[ft.TextSpan] = []
    cursor = 0
    for occurrence in occurrences:
        if occurrence.start < cursor:
            continue
        if occurrence.start > cursor:
            spans.append(ft.TextSpan(visible(text[cursor : occurrence.start])))
        raw = text[occurrence.start : occurrence.end]
        affected = occurrence.token in affected_identifiers
        style = ft.TextStyle(
            color=issue_color if affected else C.TEXT,
            weight=ft.FontWeight.W_700 if affected else None,
            bgcolor=C.RED_BG if affected and issue_color == C.RED else None,
        )
        spans.append(ft.TextSpan(visible(raw), style=style))
        cursor = occurrence.end
    if cursor < len(text):
        spans.append(ft.TextSpan(visible(text[cursor:])))
    return spans or [ft.TextSpan(visible(text or "（無）"))]


def quality_issue_summary(
    issues: Iterable[str],
    whitespace_note: str = "",
) -> tuple[str, ...]:
    """Normalize row/review summaries without changing persisted diagnostics."""
    values = [*issues]
    if whitespace_note:
        values.append(whitespace_note)
    return tuple(values)


__all__ = [
    "diagnostic_label",
    "diagnostic_spans",
    "diagnostic_tone",
    "quality_issue_summary",
]
