"""Rendering helpers for the compact Mod DB entry list rows."""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_source_service import (
    source_catalog_for,
    source_label,
    source_tone,
)
from app.ui import design, kit
from app.ui.design import C
from app.views.moddb.formatting import STATE_TONES, shorten


def build_entry_tile(panel, row, *, catalog=None) -> ft.Control:
    tone = design.tone(STATE_TONES[row.state])
    catalog = catalog or source_catalog_for(panel.db())
    return ft.Container(
        data=row.id,
        on_click=lambda _e, entry_id=row.id: panel.select(entry_id),
        ink=True,
        padding=ft.Padding.symmetric(horizontal=14, vertical=9),
        bgcolor=C.EM_BG if panel.selected and panel.selected.id == row.id else None,
        border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
        content=ft.Row(
            [
                ft.Container(
                    width=8,
                    height=8,
                    border_radius=4,
                    bgcolor=tone.fg,
                    margin=ft.Margin.only(top=5),
                ),
                _tile_text(panel, row, catalog),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
    )


def _tile_text(panel, row, catalog) -> ft.Control:
    return ft.Column(
        [
            ft.Text(
                shorten(row.en_us, 48) if row.en_us else "（原文未知）",
                size=13,
                weight=ft.FontWeight.W_500,
                color=C.TEXT if row.en_us else C.DIM,
            ),
            ft.Row(
                [
                    ft.Text(
                        shorten(row.zh_tw, 48) if row.zh_tw else "（未翻譯）",
                        size=12,
                        color=C.MUTED,
                        expand=True,
                    ),
                    *_source_controls(row, catalog),
                    *_quality_controls(row),
                ],
                spacing=6,
            ),
            kit.mono_text(shorten(row.key, 46), size=10.5, color=C.DIM),
        ],
        spacing=1,
        tight=True,
        expand=True,
    )


def _source_controls(row, catalog) -> list[ft.Control]:
    if row.source is None:
        return []
    return [
        kit.chip(
            source_label(row.source, catalog, row.review_status),
            source_tone(row.source),
        )
    ]


def _quality_controls(row) -> list[ft.Control]:
    if row.quality_state not in {
        "mismatch",
        "whitespace",
        "unknown_source",
        "missing_translation",
    }:
        return []
    return [
        ft.Icon(
            ft.Icons.WARNING_AMBER,
            size=15,
            color=C.GOLD,
            tooltip="；".join((*row.quality_issues, row.whitespace_note))
            or "原文或譯文尚未完整，無法檢查特殊字元",
        )
    ]
