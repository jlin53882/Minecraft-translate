"""Render bounded, source-aware batch replacement preview pages."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import flet as ft

from app.services_impl.moddb_service import (
    SRC_MANUAL,
    BatchReplaceChange,
)
from app.services_impl.moddb_source_service import (
    REVIEW_STATUS_LABELS,
    SourceCatalog,
    source_label,
)
from app.ui import design, kit
from app.ui.design import C

_QUALITY_LABELS = {
    "worsened": "品質變差",
    "improved": "品質改善",
    "mixed": "品質混合變化（仍需確認）",
    "unchanged": "品質無變化",
}
_QUALITY_TONES = {
    "worsened": "red",
    "improved": "em",
    "mixed": "gold",
    "unchanged": "neutral",
}
_CATEGORY_LABELS = {
    "placeholder": "佔位符",
    "minecraft": " Minecraft 色碼",
    "patchouli": " Patchouli 巨集",
    "guide": " 指南結構",
    "newline": "換行",
    "other": "特殊 token",
}


def _token_label(token: str) -> str:
    if token == "\n":
        return "實際換行 LF"
    if token == "\\n":
        return "字面 \\n"
    return token if token.isprintable() else token.encode("unicode_escape").decode()


def _quality_details(row: BatchReplaceChange) -> list[str]:
    details = [
        f"{'缺少' if delta.direction == 'missing' else '多出'}"
        f"{_CATEGORY_LABELS.get(delta.diagnostic_category, '特殊 token')}"
        f" `{_token_label(delta.token) if delta.diagnostic_category != 'guide' else delta.display_token}`："
        f"{delta.before} → {delta.after}"
        for delta in row.quality_deltas
    ]
    if row.old_whitespace_note or row.new_whitespace_note:
        before = row.old_whitespace_note or "無空白風險"
        after = row.new_whitespace_note or "無空白風險"
        details.append(f"前後空白風險：{before} → {after}")
    return details or ["特殊字元與前後空白無變化"]


def _effective_source_detail(row: BatchReplaceChange, catalog: SourceCatalog) -> str:
    source_name = source_label(row.effective_source, catalog)
    source_code = "未知" if row.effective_source is None else row.effective_source
    state = row.effective_review_status
    if state not in REVIEW_STATUS_LABELS:
        state = "legacy_unknown" if row.effective_source == SRC_MANUAL else "unreviewed"
    review = f"審核：{REVIEW_STATUS_LABELS[state]}"
    checker = row.effective_checker or "未知"
    return f"生效來源：{source_name}〔{source_code}〕 · checker {checker} · {review}"


def _row_selector(
    row: BatchReplaceChange,
    *,
    excluded_roots: set[int],
    selection_active: bool,
    busy: bool,
    on_toggle: Callable[[int, bool], None],
) -> ft.Control:
    if row.is_extra_version:
        return kit.chip("跨版本額外項目", "dia")
    if not selection_active:
        return kit.chip("根條目", "neutral")
    checked = row.entry_id not in excluded_roots
    return ft.Checkbox(
        value=checked,
        label="處理此根條目" if checked else "個別排除",
        on_change=lambda e: on_toggle(row.entry_id, bool(e.control.value)),
        disabled=busy,
    )


class SelectionBar:
    """預覽清單上方的「全選／全不選」列；勾選改變後自動用新範圍重新預覽。"""

    def __init__(
        self, on_select_all: Callable[[], None], on_clear_all: Callable[[], None]
    ) -> None:
        self.text = ft.Text("", size=12, color=C.MUTED)
        self.select_all_btn = kit.button(
            "全選",
            "ghost",
            size="sm",
            icon=ft.Icons.CHECK_BOX_OUTLINED,
            on_click=lambda _e: on_select_all(),
        )
        self.clear_all_btn = kit.button(
            "全不選",
            "ghost",
            size="sm",
            icon=ft.Icons.CHECK_BOX_OUTLINE_BLANK,
            on_click=lambda _e: on_clear_all(),
        )
        self.control = ft.Row(
            [self.select_all_btn, self.clear_all_btn, self.text],
            spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )

    def sync(self, *, total: int, excluded: int, visible: bool, busy: bool) -> None:
        """``total`` 為可勾選的根條目數，``excluded`` 為其中被排除的筆數。"""
        self.control.visible = visible and total > 0
        selected = max(0, total - excluded)
        self.text.value = f"已勾選 {selected:,d} / {total:,d} 筆根條目"
        self.select_all_btn.disabled = busy or excluded == 0
        self.clear_all_btn.disabled = busy or selected == 0


def _skipped_card(row) -> ft.Control:
    tone = design.tone("gold")
    return ft.Container(
        content=ft.Text(
            f"略過・{row.mc_version}・{row.key}：{row.reason}",
            size=11.5,
            color=tone.fg,
            selectable=True,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=9),
        bgcolor=tone.bg,
        border=ft.Border.all(1, tone.line),
        border_radius=design.RADIUS_CONTROL,
    )


def _diff_line(label: str, text: str, tone_name: str) -> ft.Control:
    tone = design.tone(tone_name)
    return ft.Row(
        [
            ft.Container(
                ft.Text(label, size=11, weight=ft.FontWeight.W_700, color=tone.fg),
                width=24,
                height=20,
                alignment=ft.Alignment.CENTER,
                bgcolor=tone.bg,
                border_radius=6,
            ),
            ft.Text(text, size=12.5, color=C.TEXT, selectable=True, expand=True),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def _change_card(
    row: BatchReplaceChange,
    *,
    catalog: SourceCatalog,
    selector: ft.Control,
    excluded: bool,
) -> ft.Control:
    quality_tone = _QUALITY_TONES[row.quality_change_kind]
    deltas = [
        kit.chip(detail, "red" if quality_tone == "red" else "gold")
        for detail in _quality_details(row)
    ]
    body = ft.Column(
        [
            ft.Row(
                [
                    selector,
                    ft.Container(expand=True),
                    kit.chip(_QUALITY_LABELS[row.quality_change_kind], quality_tone),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(
                f"原文：{row.en_us or '（原文未知）'}",
                size=12,
                color=C.MUTED,
                selectable=True,
            ),
            ft.Container(
                ft.Column(
                    [
                        _diff_line("舊", row.old_zh_tw, "red"),
                        _diff_line("新", row.new_zh_tw, "em"),
                    ],
                    spacing=6,
                ),
                padding=10,
                bgcolor=C.PANEL,
                border=ft.Border.all(1, C.LINE),
                border_radius=design.RADIUS_CONTROL,
            ),
            ft.Row(deltas, spacing=6, run_spacing=4, wrap=True),
            ft.Text(
                f"{row.mc_version} · {row.mod_id} · {row.kind} · {row.key}",
                size=10.5,
                color=C.DIM,
                selectable=True,
            ),
            ft.Text(_effective_source_detail(row, catalog), size=10.5, color=C.DIM),
        ],
        spacing=8,
        tight=True,
    )
    return ft.Container(
        content=body,
        padding=ft.Padding.symmetric(horizontal=14, vertical=12),
        bgcolor=C.PANEL2 if excluded else C.PANEL,
        opacity=0.6 if excluded else 1.0,
        border=ft.Border.all(1, C.LINE),
        border_radius=design.RADIUS_CARD,
    )


def render_batch_preview_rows(
    rows: Iterable[BatchReplaceChange],
    *,
    catalog: SourceCatalog,
    excluded_roots: set[int],
    selection_active: bool,
    show_skipped: bool,
    busy: bool,
    on_toggle: Callable[[int, bool], None],
    empty_message: str,
) -> list[ft.Control]:
    """Build controls only for the supplied page, never the complete plan."""
    controls: list[ft.Control] = []
    for row in rows:
        if show_skipped:
            controls.append(_skipped_card(row))
            continue
        selector = _row_selector(
            row,
            excluded_roots=excluded_roots,
            selection_active=selection_active,
            busy=busy,
            on_toggle=on_toggle,
        )
        excluded = selection_active and row.entry_id in excluded_roots
        controls.append(
            _change_card(row, catalog=catalog, selector=selector, excluded=excluded)
        )
    return controls or [kit.hint_text(empty_message)]
