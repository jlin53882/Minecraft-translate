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


def apply_status_style(chip: ft.Chip, tone) -> None:
    """把狀態晶片設成指定色組（接受色組名稱或舊背景色）。"""
    t = get_tone(resolve_tone_name(tone))
    chip.bgcolor = t.bg
    chip.label_text_style = ft.TextStyle(color=t.fg, size=12.5)
    chip.side = ft.BorderSide(1, t.line)


def set_chip_status(chip: ft.Chip, text: str, tone="neutral") -> None:
    """更新晶片文字與色組。"""
    chip.label = ft.Text(text)
    apply_status_style(chip, tone)
