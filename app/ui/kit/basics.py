"""kit/basics.py：最小的視覺零件（圖示方塊、chip、kbd、標籤、提示文字）。

只做樣式封裝：不碰 services / translation_tool。顏色一律來自 ``app.ui.design``，
所以會跟著深淺色主題自動切換。
"""

from __future__ import annotations

import flet as ft

from app.ui import design
from app.ui.design import C
from app.ui.design import tone as get_tone


def expand_kwargs(expand: bool | int | None) -> dict:
    """只在真的需要時才傳 ``expand``。

    Flet 1.0 對 ``expand=False`` 也會當成 expand 序列化，放進 ``Row(wrap=True)`` 時
    整列會爆版（Flutter 錯誤佔位的灰色大區塊），所以不要把 False / 0 傳下去。
    """
    return {"expand": expand} if expand else {}


def tone_icon(
    icon: str,
    tone: str = "em",
    *,
    size: int = 16,
    box: int = 28,
    radius: int | None = None,
) -> ft.Container:
    """帶淡底的圖示方塊（設計稿的 ``cico`` / ``pico``）。"""
    t = get_tone(tone)
    return ft.Container(
        width=box,
        height=box,
        border_radius=radius if radius is not None else round(box * 0.29),
        bgcolor=t.bg,
        alignment=ft.Alignment.CENTER,
        content=ft.Icon(icon, size=size, color=t.fg),
    )


def chip(
    text: str,
    tone: str = "neutral",
    *,
    icon: str | None = None,
    dot: bool = False,
    tooltip: str | None = None,
) -> ft.Container:
    """狀態膠囊：``chip("完成", "em", icon=ft.Icons.CHECK)``。"""
    t = get_tone(tone)
    parts: list[ft.Control] = []
    if dot:
        parts.append(ft.Container(width=6, height=6, border_radius=3, bgcolor=t.fg))
    if icon:
        parts.append(ft.Icon(icon, size=12, color=t.fg))
    parts.append(
        ft.Text(text, size=11.5, weight=ft.FontWeight.W_500, color=t.fg, no_wrap=True)
    )
    return ft.Container(
        height=22,
        padding=ft.Padding.symmetric(horizontal=9),
        border_radius=11,
        bgcolor=t.bg,
        border=ft.Border.all(1, t.line),
        tooltip=tooltip,
        content=ft.Row(
            parts,
            spacing=5,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def count_badge(count: int | str, tone: str = "gold") -> ft.Container:
    """導覽列用的數字徽章。"""
    t = get_tone(tone)
    return ft.Container(
        height=19,
        padding=ft.Padding.symmetric(horizontal=6),
        border_radius=10,
        bgcolor=t.bg,
        content=ft.Row(
            [
                ft.Text(
                    str(count),
                    size=10.5,
                    weight=ft.FontWeight.BOLD,
                    color=t.fg,
                    font_family=design.FONT_MONO,
                )
            ],
            tight=True,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def kbd(text: str) -> ft.Container:
    """鍵盤按鍵小標籤（快捷鍵提示）。"""
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=5, vertical=1),
        border_radius=5,
        bgcolor=C.RAISED,
        border=ft.Border(
            left=ft.BorderSide(1, C.LINE2),
            top=ft.BorderSide(1, C.LINE2),
            right=ft.BorderSide(1, C.LINE2),
            bottom=ft.BorderSide(2, C.LINE2),
        ),
        content=ft.Text(text, size=10, color=C.MUTED, font_family=design.FONT_MONO),
    )


def section_label(text: str) -> ft.Text:
    """小標籤（設計稿的 ``.lbl``）。"""
    return ft.Text(text, size=11, weight=ft.FontWeight.W_500, color=C.DIM)


def hint_text(text: str, *, error: bool = False) -> ft.Text:
    """欄位下方的說明或錯誤文字。"""
    return ft.Text(text, size=11.5, color=C.RED if error else C.DIM)


def mono_text(text: str, *, size: float = 12, color: str = C.TEXT, **kwargs) -> ft.Text:
    """等寬字型文字（key、路徑、數字）。"""
    return ft.Text(text, size=size, color=color, font_family=design.FONT_MONO, **kwargs)


def vdivider(height: int | None = None) -> ft.Container:
    """垂直細線。"""
    return ft.Container(width=1, height=height, bgcolor=C.LINE)
