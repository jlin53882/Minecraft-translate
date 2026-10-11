"""app/views/moddb/char_inspector.py：條目校對的「顯示特殊字元」對照框。

開關打開後，把原文與譯文的換行（↵）、行首行尾空白（·）、格式碼與佔位符攤開對照。
"""

from __future__ import annotations

import flet as ft

from app.ui.design import C
from app.views.moddb.format_diff_viewer import FormatDiffViewer

CharInspector = FormatDiffViewer


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
