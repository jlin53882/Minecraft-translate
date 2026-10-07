"""app/views/moddb/char_inspector.py：條目校對的「顯示特殊字元」對照框。

開關打開後，把原文與譯文的換行（↵）、行首行尾空白（·）、格式碼與佔位符攤開對照。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from app.views.moddb.formatting import visible_segments


def _spans(text: str) -> list[ft.TextSpan]:
    styles = {
        "newline": ft.TextStyle(color=C.GOLD, weight=ft.FontWeight.BOLD),
        "space": ft.TextStyle(color=C.RED, weight=ft.FontWeight.BOLD),
        "token": ft.TextStyle(color=C.DIA, weight=ft.FontWeight.BOLD),
    }
    return [ft.TextSpan(seg, styles.get(kind)) for seg, kind in visible_segments(text)]


class CharInspector:
    """開關列＋對照框；``render(原文, 譯文)`` 依開關狀態更新。"""

    def __init__(self, on_toggle: Callable[[], None]):
        self.row = kit.SwitchRow(
            "顯示特殊字元",
            "把換行（↵）、行首行尾空白（·）、格式碼與佔位符攤開，原文與譯文對照",
            False,
            on_change=lambda _e: on_toggle(),
            divider=False,
        )
        self.src = ft.Text("", size=13, selectable=True, color=C.TEXT)
        self.tw = ft.Text("", size=13, selectable=True, color=C.TEXT)
        self.box = ft.Container(
            ft.Column(
                [
                    kit.section_label("原文（特殊字元）"),
                    self.src,
                    kit.section_label("譯文（特殊字元）"),
                    self.tw,
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=C.PANEL2,
            border_radius=design.RADIUS_CONTROL,
            border=ft.Border.all(1, C.LINE),
            visible=False,
        )

    def render(self, source: str, text: str) -> None:
        on = bool(self.row.value)
        self.box.visible = on
        if not on:
            return
        self.src.value = ""
        self.src.spans = _spans(source) or [ft.TextSpan("（無）")]
        self.tw.value = ""
        self.tw.spans = _spans(text) or [ft.TextSpan("（尚無譯文）")]


def scrolling_list(list_view: ft.ListView) -> ft.Container:
    """清單外包一層：捲軸常駐顯示（全域主題預設只在滑過時出現），高度隨容器伸縮。"""
    return ft.Container(
        list_view,
        expand=True,
        theme=ft.Theme(
            scrollbar_theme=ft.ScrollbarTheme(
                thickness=8,
                radius=4,
                thumb_color=C.LINE2,
                main_axis_margin=2,
                thumb_visibility=True,
            )
        ),
    )
