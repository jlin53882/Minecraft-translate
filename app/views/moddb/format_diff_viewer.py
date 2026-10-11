"""Reusable read-only format-difference viewer for Mod DB editing screens."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import flet as ft

from app.services_impl.moddb_format_diff_service import (
    FormatDiffResult,
    FormatTokenOccurrence,
    compare_format_diff,
)
from app.ui import design, kit
from app.ui.design import C


def _text_style(color: str | None = None) -> ft.TextStyle:
    return ft.TextStyle(color=color or C.DIA, weight=ft.FontWeight.W_600)


def _display_text(text: str) -> str:
    """Make real line breaks visible without changing the stored text."""
    return (text or "").replace("\r\n", "\n").replace("\n", "↵\n")


def _spans(
    text: str,
    occurrences: Iterable[FormatTokenOccurrence],
    affected_tokens: set[str],
    issue_color: str,
) -> list[ft.TextSpan]:
    spans: list[ft.TextSpan] = []
    cursor = 0
    for occurrence in occurrences:
        if occurrence.start < cursor:
            continue
        if occurrence.start > cursor:
            spans.append(ft.TextSpan(_display_text(text[cursor : occurrence.start])))
        raw = text[occurrence.start : occurrence.end]
        style = (
            _text_style(issue_color)
            if occurrence.token in affected_tokens
            else _text_style()
        )
        spans.append(ft.TextSpan(_display_text(raw), style))
        cursor = occurrence.end
    if cursor < len(text):
        spans.append(ft.TextSpan(_display_text(text[cursor:])))
    return spans or [ft.TextSpan("（無）")]


class FormatDiffViewer:
    """Show one shared format contract result with optional token highlighting."""

    def __init__(
        self,
        on_toggle: Callable[[], None] | None = None,
        *,
        switch_label: str = "顯示格式差異",
    ) -> None:
        self._on_toggle = on_toggle
        self._result: FormatDiffResult | None = None
        self.row = kit.SwitchRow(
            switch_label,
            "只標示格式標記；不會把提示文字寫入譯文或資料庫",
            False,
            on_change=lambda _e: self._toggle(),
            divider=False,
        )
        self.summary = ft.Text(
            "尚未檢查格式。", size=12, color=C.MUTED, selectable=True
        )
        self.src = ft.Text("", size=13, selectable=True, color=C.TEXT)
        self.tw = ft.Text("", size=13, selectable=True, color=C.TEXT)
        self.box = ft.Container(
            ft.Column(
                [
                    kit.section_label("原文（格式標記）"),
                    self.src,
                    kit.section_label("譯文（格式標記）"),
                    self.tw,
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=C.PANEL2,
            border_radius=design.RADIUS_CONTROL,
            border=ft.Border.all(1, C.LINE),
            visible=False,
        )
        self.control = ft.Column([self.summary, self.row, self.box], spacing=6)

    def render(
        self,
        source_text: str,
        target_text: str,
        diff_result: FormatDiffResult | None = None,
    ) -> FormatDiffResult:
        """Render current texts; stale results are discarded and recomputed."""
        result = diff_result
        if (
            result is None
            or result.source_text != (source_text or "")
            or result.target_text != (target_text or "")
        ):
            result = compare_format_diff(source_text, target_text)
        self._result = result
        self.summary.value = "；".join(result.summary_lines)
        self.summary.color = C.GOLD if result.has_issues else C.EM
        self.box.visible = bool(self.row.value)
        self._render_texts(result)
        return result

    def _render_texts(self, result: FormatDiffResult) -> None:
        if not self.box.visible:
            self.src.value = result.source_text or "（無）"
            self.tw.value = result.target_text or "（尚無譯文）"
            self.src.spans = []
            self.tw.spans = []
            return
        source_occurrences = result.source_occurrences
        target_occurrences = result.target_occurrences
        source_tokens = {
            issue.token for issue in result.issues if issue.source_occurrences
        }
        target_tokens = {
            issue.token for issue in result.issues if issue.target_occurrences
        }
        self.src.value = ""
        self.src.spans = _spans(
            result.source_text, source_occurrences, source_tokens, C.GOLD
        )
        self.tw.value = ""
        self.tw.spans = _spans(
            result.target_text, target_occurrences, target_tokens, C.RED
        )

    def _toggle(self) -> None:
        if self._result is not None:
            self.box.visible = bool(self.row.value)
            self._render_texts(self._result)
        if self._on_toggle is not None:
            self._on_toggle()


__all__ = ["FormatDiffViewer"]
