"""app/views/moddb/char_inspector.py：條目校對清單的捲軸外框。

特殊字元對照框本身在 ``format_diff_viewer.FormatDiffViewer``。
"""

from __future__ import annotations

import flet as ft

from app.ui.design import C


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
