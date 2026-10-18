"""Shared format-difference review surface for Mod DB screens.

The comparison contract lives in ``translation_tool.core.format_diagnostics``.
This module only turns that result into a small, reusable Flet view; markers and issue
chips never become part of the editable or persisted translation text.
"""

from __future__ import annotations

from collections.abc import Sequence

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from app.views.moddb.format_issue_rendering import (
    diagnostic_label,
    diagnostic_spans,
    diagnostic_tone,
)
from translation_tool.core.format_diagnostics import (
    FormatDiagnosticsResult,
    FormatSpan,
    analyze_format_diagnostics,
)


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
        self._result: FormatDiagnosticsResult | None = None
        self._kind: str = "lang"
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
        self._edit_column = ft.Column(
            spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
        )
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
        diff_result: FormatDiagnosticsResult | None = None,
        *,
        metadata: Sequence[ft.Control] = (),
        kind: str | None = None,
    ) -> FormatDiagnosticsResult:
        source_text = source_text or ""
        target_text = target_text or ""
        kind = kind or "lang"
        result = diff_result
        if (
            result is None
            or result.source_text != source_text
            or result.target_text != target_text
            or result.kind != kind
        ):
            result = analyze_format_diagnostics(source_text, target_text, kind=kind)
        self._result = result
        self._kind = kind
        self._source_text = source_text
        self._target_text = target_text
        self._meta_controls = list(metadata)
        self._refresh()
        return result

    def _select_tab(self, value: str) -> None:
        self._tab = value
        self._refresh()
        self._push()

    def _toggle_highlight(self, event) -> None:
        self._highlight = bool(event.control.value)
        self._refresh()
        self._push()

    def _push(self) -> None:
        """使用者在面板內操作（切頁籤、標示開關）後立刻把新內容送到畫面。

        ``Segmented`` 只更新自己的按鈕；內容容器若不在這裡更新，要等到下一次
        不相關的 ``page.update`` 才會換畫面。
        """
        try:
            self.control.update()
        except RuntimeError:
            pass  # 尚未加入頁面（例如單元測試）；之後的整頁更新會帶上

    def _refresh(self) -> None:
        result = self._result or analyze_format_diagnostics(
            self._source_text, self._target_text, kind=self._kind
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
        edit_mode = self._tab == "edit" and has_editor
        if self._meta_controls and not edit_mode:
            content.append(ft.Row(self._meta_controls, spacing=6, wrap=True))
        # 「標示缺漏」只作用在差異預覽；編輯頁沒有可標示的內容，開關放著會像壞掉
        switch = [] if edit_mode else [self._highlight_switch]
        if self._tabs.visible:
            content.append(
                ft.Row(
                    [self._tabs, ft.Container(expand=True), *switch],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        else:
            content.append(
                ft.Row(
                    [
                        ft.Text("差異預覽", size=12, color=C.MUTED),
                        ft.Container(expand=True),
                        *switch,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        if edit_mode:
            content.extend(self._edit_content())
        else:
            content.extend(self._diff_content(result))
        self.control.content.controls = content

    def _edit_content(self) -> list[ft.Control]:
        """編輯分頁：原文唯讀框在上，譯文標題（含來源標籤）與輸入框在下。"""
        controls: list[ft.Control] = []
        if self.show_source:
            controls += [
                kit.section_label("原文 en_us"),
                ft.Container(
                    ft.Text(
                        self._source_text or "（無）",
                        size=15,
                        selectable=True,
                        color=C.TEXT,
                    ),
                    padding=14,
                    bgcolor=C.PANEL2,
                    border_radius=design.RADIUS_CONTROL,
                    border=ft.Border.all(1, C.LINE),
                ),
            ]
        controls.append(
            ft.Row(
                [kit.section_label("譯文 zh_tw"), *self._meta_controls],
                spacing=8,
                wrap=True,
            )
        )
        controls.append(self.editable_control)
        if self._edit_notice is not None:
            controls.append(self._edit_notice)
        # 撐滿卡片寬度、區塊間留空間；內容少時也不會縮成窄窄一條
        # 同一個 Column 重複使用：每次打字都會重繪，若換成新的容器，輸入框會被
        # 重新掛載而失去游標。
        self._edit_column.controls = controls
        return [self._edit_column]

    def _diff_content(self, result: FormatDiagnosticsResult) -> list[ft.Control]:
        issue_controls = [
            kit.chip(
                diagnostic_label(issue),
                diagnostic_tone(issue),
                icon=ft.Icons.WARNING_AMBER,
            )
            for issue in result.issues
        ]
        # 指南標記（連結目標、標籤等）與編輯頁的提醒共用同一份比對結果
        issue_controls += [
            kit.chip(message, "gold", icon=ft.Icons.WARNING_AMBER)
            for message in result.guide_issues
        ]
        if result.soft_newline_counts is not None:
            issue_controls.append(
                kit.chip(
                    result.summary_lines[-1],
                    "neutral",
                    icon=ft.Icons.INFO_OUTLINE,
                )
            )
        if not result.has_issues:
            issue_controls.append(
                kit.chip("格式檢查通過", "em", icon=ft.Icons.CHECK_CIRCLE_OUTLINE)
            )
        issue_summary: ft.Control = ft.Row(issue_controls, spacing=6, wrap=True)
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
        occurrences: Sequence[FormatSpan],
        affected_tokens: set[str],
        tone: str,
    ) -> ft.Container:
        palette = design.tone(tone)
        rendered = ft.Text(text or "（無）", size=13, color=C.TEXT, selectable=True)
        if self._highlight:
            rendered.value = ""
            rendered.spans = diagnostic_spans(
                text,
                occurrences,
                affected_tokens,
                highlight=True,
            )
        # 固定高度的面板內捲動：長文字超出時可以用卷軸看完整內容
        return ft.Container(
            content=ft.Column(
                [ft.Text(title, size=12, color=palette.fg), rendered],
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            padding=12,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, palette.line),
            border_radius=design.RADIUS_CONTROL,
            width=self.panel_width,
            height=220 if self.compact else 260,
        )

    def _result_table(self, result: FormatDiagnosticsResult) -> ft.Container:
        """檢查項目表：項目／原文數量／譯文數量／差異，欄寬固定、差異欄吃剩餘空間。"""

        def cell(text: str, *, width: float | None = None, expand=None, **style):
            style.setdefault("size", 12)
            return ft.Text(text, width=width, expand=expand, **style)

        rows: list[ft.Control] = [
            ft.Row(
                [
                    cell("檢查項目", expand=2, color=C.MUTED, size=11.5),
                    cell(
                        "原文", width=56, color=C.MUTED, text_align=ft.TextAlign.CENTER
                    ),
                    cell(
                        "譯文", width=56, color=C.MUTED, text_align=ft.TextAlign.CENTER
                    ),
                    cell("差異", expand=3, color=C.MUTED),
                ],
                spacing=8,
            ),
            ft.Divider(height=1, color=C.LINE),
        ]
        for issue in result.issues:
            tone = C.RED if issue.direction == "extra" else C.GOLD
            action = "缺少" if issue.direction == "missing" else "多出"
            detail: list[ft.Control] = [
                cell(
                    f"{action} {abs(issue.delta)} 個",
                    color=tone,
                    weight=ft.FontWeight.W_600,
                )
            ]
            if issue.location_confidence == "unknown":
                detail.append(
                    ft.Text("位置無法可靠判定", size=11, color=C.MUTED, no_wrap=False)
                )
            rows.append(
                ft.Row(
                    [
                        cell(issue.display_token, expand=2, color=C.TEXT),
                        cell(
                            str(issue.source_count),
                            width=56,
                            color=C.TEXT,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        cell(
                            str(issue.target_count),
                            width=56,
                            color=tone,
                            weight=ft.FontWeight.W_600,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Column(detail, spacing=1, expand=3),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
            )
        if result.soft_newline_counts is not None:
            rows.append(
                ft.Text(
                    result.summary_lines[-1],
                    size=12,
                    color=C.MUTED,
                )
            )
        if not result.issues and not result.guide_issues:
            rows.append(ft.Text("所有格式標記數量一致。", size=12, color=C.EM))
        return ft.Container(
            content=ft.Column(rows, spacing=7),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
        )


__all__ = ["FormatDiffReview"]
