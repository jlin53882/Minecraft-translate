"""app/ui/status_chip.py：頁面「執行狀態」晶片的共用樣式。

``ft.Chip`` 只認背景色，這裡統一成「語意色組」：淡底 + 同色系文字 + 邊框，深 / 淺色主題皆適用。
舊呼叫端傳的是 Material 背景色（RED_200 …），``resolve_tone_name`` 會依色系轉成色組。
"""

from __future__ import annotations

import flet as ft

from app.ui.design import tone as get_tone
from app.ui.snack import snack_style

TONE_NAMES = ("neutral", "em", "gold", "red", "dia", "ench")


def resolve_tone_name(value) -> str:
    """色組名稱原樣回傳；其他（舊色票 / 語意色）依色系轉成色組名稱。"""
    if isinstance(value, str) and value in TONE_NAMES:
        return value
    return snack_style(value)[0]


def make_status_chip(text: str = "尚未開始", tone="neutral") -> ft.Container:
    """建立狀態晶片（膠囊容器，文字控制項在 ``.label``）。

    不用 ``ft.Chip``：它沒有 ``on_click``／``on_select`` 時 Flutter 視為停用並整體淡化，
    語意色看不出來。
    """
    label = ft.Text(text, size=12.5, no_wrap=True)
    chip = ft.Container(
        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
        border_radius=16,
        content=label,
    )
    chip.label = label  # type: ignore[attr-defined]
    apply_status_style(chip, tone)
    return chip


def apply_status_style(chip: ft.Container, tone) -> None:
    """把狀態晶片設成指定色組（接受色組名稱或舊背景色）。"""
    t = get_tone(resolve_tone_name(tone))
    chip.bgcolor = t.bg
    chip.label.color = t.fg
    chip.border = ft.Border.all(1, t.line)


def set_chip_status(chip: ft.Container, text: str, tone="neutral") -> None:
    """更新晶片文字與色組。"""
    chip.label.value = text
    apply_status_style(chip, tone)
