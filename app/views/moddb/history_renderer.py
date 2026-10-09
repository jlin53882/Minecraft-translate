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


def render_history(panel, *, catalog=None) -> None:
    detail = panel.detail
    if detail is None:
        return
    catalog = catalog or source_catalog_for(panel.db())
    controls = [_history_event(panel, event, catalog) for event in detail.history]
    if detail.translations:
        controls.append(kit.section_label("各來源譯文"))
        controls.extend(_translation_row(row, catalog) for row in detail.translations)
    panel.history_col.controls = controls or [kit.hint_text("還沒有異動記錄")]


def _history_event(panel, event, catalog) -> ft.Control:
    action = {
        "manual": "手動儲存（未審核）",
        "review": "人工審核",
        "revert": "還原",
        "batch_replace": "批次取代",
        "batch_revert": "批次還原",
        "ai_retranslate": "AI 重翻",
        "quality_repair": "特殊字元修復",
    }.get(event.action, "其他異動")
    controls: list[ft.Control] = [
        ft.Row(
            [
                ft.Text(
                    f"{action}"
                    + (
                        f"・{source_label(event.source_id, catalog)}"
                        if event.action in ("quality_repair", "revert")
                        and event.source_id is not None
                        else ""
                    )
                    + f"・{event.actor or '—'}",
                    size=11.5,
                    color=C.MUTED,
                ),
                ft.Text(format_taipei_time(event.at), size=11, color=C.DIM),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            wrap=True,
        ),
    ]
    if event.old_zh_tw:
        controls.extend(
            [
                ft.Text(
                    f"原譯：{event.old_zh_tw}",
                    size=12.5,
                    color=C.MUTED,
                    selectable=True,
                ),
                ft.Text(
                    f"新譯：{event.new_zh_tw or ''}",
                    size=12.5,
                    color=C.TEXT,
                    selectable=True,
                ),
            ]
        )
    else:
        controls.append(
            ft.Text(
                f"譯文：{event.new_zh_tw or ''}",
                size=12.5,
                color=C.TEXT,
                selectable=True,
            )
        )
    _append_history_details(controls, panel, event)
    return ft.Column(
        controls,
        spacing=3,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )


def _append_history_details(controls, panel, event) -> None:
    if event.note:
        controls.append(ft.Text(event.note, size=11, color=C.DIM))
    if event.prev_manual is not None:
        controls.append(
            ft.Text(
                f"取代前人工譯文：{event.prev_manual}",
                size=11,
                color=C.DIM,
                selectable=True,
            )
        )
    if event.action in ("manual", "review", "quality_repair"):
        controls.append(
            kit.button(
                "還原這次修復" if event.action == "quality_repair" else "還原這次更新",
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
    return ft.Column(
        [
            ft.Row(
                [
                    kit.chip(
                        source_label(row.source, catalog, row.review_status),
                        source_tone(row.source),
                    ),
                    ft.Text(
                        f"首次 {format_taipei_time(row.created_at)} · "
                        f"更新 {format_taipei_time(row.updated_at)}",
                        size=10.5,
                        color=C.DIM,
                    ),
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                wrap=True,
            ),
            ft.Text(
                row.zh_tw or "",
                size=12,
                color=C.TEXT,
                selectable=True,
            ),
        ],
        spacing=4,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
