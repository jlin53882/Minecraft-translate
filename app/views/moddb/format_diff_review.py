"""Shared format-difference review surface for Mod DB screens.

The comparison contract lives in ``moddb_format_diff_service``.  This module
only turns that result into a small, reusable Flet view; markers and issue
chips never become part of the editable or persisted translation text.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import flet as ft

from app.services_impl.moddb_format_diff_service import (
    FormatDiffIssue,
    FormatDiffResult,
    FormatTokenOccurrence,
    compare_format_diff,
)
from app.ui import design, kit
from app.ui.design import C


def _token_spans(
    text: str,
    occurrences: Iterable[FormatTokenOccurrence],
    affected_tokens: set[str],
    *,
    highlight: bool,
) -> list[ft.TextSpan]:
    """Render real text with optional token emphasis, without inserting markers."""
    if not highlight:
        return [ft.TextSpan(text or "（無）")]
    spans: list[ft.TextSpan] = []
    cursor = 0
    for occurrence in occurrences:
        if occurrence.start < cursor:
            continue
        if occurrence.start > cursor:
            spans.append(ft.TextSpan(text[cursor : occurrence.start]))
        raw = text[occurrence.start : occurrence.end]
        style = ft.TextStyle(
            color=C.RED if occurrence.token in affected_tokens else C.TEXT,
            weight=ft.FontWeight.W_700 if occurrence.token in affected_tokens else None,
            bgcolor=C.RED_BG if occurrence.token in affected_tokens else None,
        )
        spans.append(ft.TextSpan(raw, style=style))
        cursor = occurrence.end
    if cursor < len(text):
        spans.append(ft.TextSpan(text[cursor:]))
    return spans or [ft.TextSpan(text or "（無）")]


def _issue_tone(issue: FormatDiffIssue) -> str:
    return "red" if issue.direction == "extra" else "gold"


def _issue_label(issue: FormatDiffIssue) -> str:
    if issue.token == "\n":
        return f"換行數量不一致 · 原文 {issue.source_count} / 譯文 {issue.target_count}"
    action = "缺少" if issue.direction == "missing" else "多出"
    return f"{action} {issue.display_token} × {abs(issue.delta)}"


class FormatDiffReview:
    """A shared, bounded review panel used by entry review and repair preview."""

    def __init__(
        self,
        *,
        context_label: str,
        source_label: str = "英文原文・en_us",
        target_label: str = "目前譯文・zh_tw",
        editable_control: ft.Control | None = None,
        edit_notice: ft.Control | None = None,
        compact: bool = False,
        panel_width: float | None = None,
        show_source: bool = True,
    ) -> None:
        self.context_label = context_label
        self.source_label = source_label
        self.target_label = target_label
        self.editable_control = editable_control
        self.compact = compact
        self.panel_width = panel_width or (320 if compact else 340)
        self.show_source = show_source
        self._result: FormatDiffResult | None = None
        self._highlight = True
        self._tab = "diff"
        self._meta_controls: list[ft.Control] = []
        self._source_text = ""
        self._target_text = ""
        self._edit_notice: ft.Control | None = edit_notice
        self._tabs = kit.Segmented(
            [("diff", "差異預覽"), ("edit", "編輯譯文")],
            "diff",
            self._select_tab,
        )
        self._highlight_switch = ft.Switch(
            value=True,
            label="標示缺漏",
            on_change=self._toggle_highlight,
        )
        self.body = ft.Column(spacing=10)
        self.control = ft.Container(
            content=ft.Column(spacing=12),
            padding=ft.Padding.symmetric(horizontal=14, vertical=14),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CARD,
        )
        self._refresh()

    def set_editable_control(
        self, control: ft.Control | None, *, notice: ft.Control | None = None
    ) -> None:
        self.editable_control = control
        self._edit_notice = notice
        self._refresh()

    def render(
        self,
        source_text: str,
        target_text: str,
        diff_result: FormatDiffResult | None = None,
        *,
        metadata: Sequence[ft.Control] = (),
    ) -> FormatDiffResult:
        source_text = source_text or ""
        target_text = target_text or ""
        result = diff_result
        if (
            result is None
            or result.source_text != source_text
            or result.target_text != target_text
        ):
            result = compare_format_diff(source_text, target_text)
        self._result = result
        self._source_text = source_text
        self._target_text = target_text
        self._meta_controls = list(metadata)
        self._refresh()
        return result

    def _select_tab(self, value: str) -> None:
        self._tab = value
        self._refresh()

    def _toggle_highlight(self, event) -> None:
        self._highlight = bool(event.control.value)
        self._refresh()

    def _refresh(self) -> None:
        result = self._result or compare_format_diff(
            self._source_text, self._target_text
        )
        has_editor = self.editable_control is not None
        self._tabs.visible = has_editor and not self.compact
        if not has_editor:
            self._tab = "diff"
        self._highlight_switch.value = self._highlight
        header_controls: list[ft.Control] = [
            ft.Column(
                [
                    ft.Text(
                        "格式差異檢視",
                        size=17 if not self.compact else 14,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT,
                    ),
                    ft.Text(
                        "共用於 AI 修復預覽與條目校對",
                        size=11.5,
                        color=C.MUTED,
                    ),
                ],
                spacing=2,
            ),
            ft.Container(expand=True),
            kit.chip(self.context_label, "dia"),
        ]
        content: list[ft.Control] = [
            ft.Row(
                header_controls,
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ]
        if self._meta_controls:
            content.append(ft.Row(self._meta_controls, spacing=6, wrap=True))
        if self._tabs.visible:
            content.append(
                ft.Row(
                    [self._tabs, ft.Container(expand=True), self._highlight_switch],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        else:
            content.append(
                ft.Row(
                    [
                        ft.Text("差異預覽", size=12, color=C.MUTED),
                        ft.Container(expand=True),
                        self._highlight_switch,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        if self._tab == "edit" and has_editor:
            content.append(
                ft.Column(
                    [
                        ft.Text(self.target_label, size=12, color=C.EM),
                        self.editable_control,
                        self._edit_notice,
                    ],
                    spacing=8,
                )
            )
        else:
            content.extend(self._diff_content(result))
        self.control.content.controls = content

    def _diff_content(self, result: FormatDiffResult) -> list[ft.Control]:
        issue_controls = [
            kit.chip(
                _issue_label(issue), _issue_tone(issue), icon=ft.Icons.WARNING_AMBER
            )
            for issue in result.issues
        ]
        issue_summary: ft.Control = (
            ft.Row(issue_controls, spacing=6, wrap=True)
            if issue_controls
            else ft.Row(
                [kit.chip("格式檢查通過", "em", icon=ft.Icons.CHECK_CIRCLE_OUTLINE)],
                spacing=6,
            )
        )
        source_tokens = {
            issue.token for issue in result.issues if issue.source_occurrences
        }
        target_tokens = {
            issue.token for issue in result.issues if issue.target_occurrences
        }
        source_panel = self._text_panel(
            self.source_label,
            result.source_text,
            result.source_occurrences,
            source_tokens,
            "dia",
        )
        target_panel = self._text_panel(
            self.target_label,
            result.target_text,
            result.target_occurrences,
            target_tokens,
            "em",
        )
        comparison_panels = [target_panel]
        if self.show_source:
            comparison_panels.insert(0, source_panel)
        return [
            issue_summary,
            ft.Row(
                comparison_panels,
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            self._result_table(result),
        ]

    def _text_panel(
        self,
        title: str,
        text: str,
        occurrences: Sequence[FormatTokenOccurrence],
        affected_tokens: set[str],
        tone: str,
    ) -> ft.Container:
        palette = design.tone(tone)
        rendered = ft.Text(text or "（無）", size=13, color=C.TEXT, selectable=True)
        if self._highlight:
            rendered.value = ""
            rendered.spans = _token_spans(
                text,
                occurrences,
                affected_tokens,
                highlight=True,
            )
        return ft.Container(
            content=ft.Column(
                [ft.Text(title, size=12, color=palette.fg), rendered], spacing=8
            ),
            padding=12,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, palette.line),
            border_radius=design.RADIUS_CONTROL,
            width=self.panel_width,
            height=220 if self.compact else 260,
        )

    def _result_table(self, result: FormatDiffResult) -> ft.Container:
        rows: list[ft.Control] = [
            ft.Row(
                [
                    ft.Text("檢查項目", size=11.5, color=C.MUTED, expand=True),
                    ft.Text("原文", size=11.5, color=C.MUTED, width=48),
                    ft.Text("譯文", size=11.5, color=C.MUTED, width=48),
                    ft.Text("結果", size=11.5, color=C.MUTED, width=150),
                ],
                spacing=8,
            )
        ]
        for issue in result.issues:
            rows.append(
                ft.Row(
                    [
                        ft.Text(
                            issue.display_token, size=12, color=C.TEXT, expand=True
                        ),
                        ft.Text(
                            str(issue.source_count), size=12, color=C.TEXT, width=48
                        ),
                        ft.Text(
                            str(issue.target_count), size=12, color=C.TEXT, width=48
                        ),
                        ft.Text(
                            f"{_issue_label(issue)}（位置無法可靠判定）"
                            if issue.location_confidence == "unknown"
                            else _issue_label(issue),
                            size=11.5,
                            color=C.RED if issue.direction == "extra" else C.GOLD,
                            width=150,
                        ),
                    ],
                    spacing=8,
                )
            )
        if not result.issues:
            rows.append(ft.Text("所有格式標記數量一致。", size=12, color=C.EM))
        return ft.Container(
            content=ft.Column(rows, spacing=7),
            padding=ft.Padding.symmetric(horizontal=10, vertical=9),
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
        )


__all__ = ["FormatDiffReview"]
