"""kit/states.py：空狀態、載入中、錯誤狀態。"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.ui.design import C
from app.ui.kit.basics import tone_icon
from app.ui.kit.inputs import button


def _centered(controls: list[ft.Control], *, padding: int = 28) -> ft.Container:
    return ft.Container(
        padding=padding,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            controls,
            spacing=8,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def empty_state(
    title: str,
    subtitle: str = "",
    *,
    icon: str = ft.Icons.SEARCH,
    action_text: str | None = None,
    on_action: Callable | None = None,
) -> ft.Container:
    controls: list[ft.Control] = [
        tone_icon(icon, "dia", size=24, box=52, radius=16),
        ft.Text(
            title,
            size=14,
            weight=ft.FontWeight.BOLD,
            color=C.TEXT,
            text_align=ft.TextAlign.CENTER,
        ),
    ]
    if subtitle:
        controls.append(
            ft.Text(subtitle, size=12, color=C.DIM, text_align=ft.TextAlign.CENTER)
        )
    if action_text:
        controls.append(button(action_text, "secondary", size="sm", on_click=on_action))
    return _centered(controls)


def loading_state(text: str = "讀取中…") -> ft.Container:
    return _centered(
        [
            ft.ProgressRing(
                width=28, height=28, stroke_width=3, color=C.EM, bgcolor=C.TRACK
            ),
            ft.Text(text, size=12, color=C.DIM),
        ]
    )


def error_state(
    title: str,
    detail: str = "",
    *,
    on_retry: Callable | None = None,
    retry_text: str = "重試",
) -> ft.Container:
    controls: list[ft.Control] = [
        tone_icon(ft.Icons.ERROR_OUTLINE, "red", size=24, box=52, radius=16),
        ft.Text(
            title,
            size=14,
            weight=ft.FontWeight.BOLD,
            color=C.TEXT,
            text_align=ft.TextAlign.CENTER,
        ),
    ]
    if detail:
        controls.append(
            ft.Text(
                detail,
                size=12,
                color=C.RED,
                selectable=True,
                text_align=ft.TextAlign.CENTER,
            )
        )
    if on_retry:
        controls.append(
            button(
                retry_text,
                "secondary",
                size="sm",
                icon=ft.Icons.REFRESH,
                on_click=on_retry,
            )
        )
    return _centered(controls)
