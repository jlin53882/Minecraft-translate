"""Reusable read-only format-difference viewer for Mod DB editing screens."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from app.views.moddb.format_issue_rendering import diagnostic_spans
from translation_tool.core.format_diagnostics import (
    FormatDiagnosticsResult,
    analyze_format_diagnostics,
)


class FormatDiffViewer:
    """Show one shared format contract result with optional token highlighting."""

    def __init__(
        self,
        on_toggle: Callable[[], None] | None = None,
        *,
        switch_label: str = "顯示格式差異",
    ) -> None:
        self._on_toggle = on_toggle
        self._result: FormatDiagnosticsResult | None = None
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
        diff_result: FormatDiagnosticsResult | None = None,
        *,
        kind: str | None = None,
    ) -> FormatDiagnosticsResult:
        """Render current texts; stale results are discarded and recomputed."""
        kind = kind or "lang"
        result = diff_result
        if (
            result is None
            or result.source_text != (source_text or "")
            or result.target_text != (target_text or "")
            or result.kind != kind
        ):
            result = analyze_format_diagnostics(source_text, target_text, kind=kind)
        self._result = result
        self.summary.value = "；".join(result.summary_lines)
        self.summary.color = C.GOLD if result.has_issues else C.EM
        self.box.visible = bool(self.row.value)
        self._render_texts(result)
        return result

    def _render_texts(self, result: FormatDiagnosticsResult) -> None:
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
        self.src.spans = diagnostic_spans(
            result.source_text,
            source_occurrences,
            source_tokens,
            issue_color=C.GOLD,
            show_line_breaks=True,
        )
        self.tw.value = ""
        self.tw.spans = diagnostic_spans(
            result.target_text,
            target_occurrences,
            target_tokens,
            issue_color=C.RED,
            show_line_breaks=True,
        )

    def _toggle(self) -> None:
        if self._result is not None:
            self.box.visible = bool(self.row.value)
            self._render_texts(self._result)
        if self._on_toggle is not None:
            self._on_toggle()


__all__ = ["FormatDiffViewer"]
