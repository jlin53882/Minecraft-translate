"""History and per-source provenance rows for the Mod DB entry editor."""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import format_taipei_time
from app.services_impl.moddb_source_service import (
    source_catalog_for,
    source_label,
    source_tone,
)
from app.ui import kit
from app.ui.design import C
from app.views.moddb.formatting import shorten


def render_history(panel) -> None:
    detail = panel.detail
    if detail is None:
        return
    catalog = source_catalog_for(panel.db())
    controls = [_history_event(panel, event) for event in detail.history]
    if detail.translations:
        controls.append(kit.section_label("各來源譯文"))
        controls.extend(_translation_row(row, catalog) for row in detail.translations)
    panel.history_col.controls = controls or [kit.hint_text("還沒有異動記錄")]


def _history_event(panel, event) -> ft.Control:
    action = {
        "manual": "手動儲存（未審核）",
        "review": "人工審核",
        "revert": "還原",
        "batch_replace": "批次取代",
        "batch_revert": "批次還原",
        "ai_retranslate": "AI 重翻",
    }.get(event.action, "其他異動")
    body = (
        f"{shorten(event.old_zh_tw, 20)} → {shorten(event.new_zh_tw, 20)}"
        if event.old_zh_tw
        else shorten(event.new_zh_tw, 40)
    )
    controls: list[ft.Control] = [
        ft.Row(
            [
                ft.Text(f"{action}・{event.actor or '—'}", size=11.5, color=C.MUTED),
                ft.Text(format_taipei_time(event.at), size=11, color=C.DIM),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        ft.Text(body, size=12.5, color=C.TEXT),
    ]
    _append_history_details(controls, panel, event)
    return ft.Column(controls, spacing=3)


def _append_history_details(controls, panel, event) -> None:
    if event.note:
        controls.append(ft.Text(event.note, size=11, color=C.DIM))
    if event.prev_manual is not None:
        controls.append(
            ft.Text(
                f"取代前人工譯文：{shorten(event.prev_manual, 36)}",
                size=11,
                color=C.DIM,
            )
        )
    if event.action in ("manual", "review"):
        controls.append(
            kit.button(
                "還原這次更新",
                "ghost",
                size="sm",
                on_click=lambda _e, history_id=event.id: panel._revert(history_id),
            )
        )
    elif event.action == "batch_replace":
        controls.append(
            kit.button(
                "整批還原這次取代",
                "ghost",
                size="sm",
                on_click=lambda _e, batch=event.batch: panel._revert_batch(batch),
            )
        )


def _translation_row(row, catalog) -> ft.Control:
    return ft.Row(
        [
            kit.chip(
                source_label(row.source, catalog, row.review_status),
                source_tone(row.source),
            ),
            ft.Text(shorten(row.zh_tw, 28), size=12, color=C.TEXT, expand=True),
            ft.Text(
                f"首次 {format_taipei_time(row.created_at)} · 更新 {format_taipei_time(row.updated_at)}",
                size=10.5,
                color=C.DIM,
            ),
        ],
        spacing=6,
    )
