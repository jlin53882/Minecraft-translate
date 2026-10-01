"""View 外框（統一頁面留白）。

新版外殼（``app.shell``）已提供側欄 / 頂列 / 狀態列，頁面本身不再需要「卡片外框」：
這裡只負責給每個頁面一致的內距，並讓內容填滿中間區域。背景色由 Page 提供，
所以深 / 淺色主題切換時不需要重建頁面。

注意：這個模組只處理 UI 外觀，不應引入 services / translation_tool 等業務邏輯。
"""

from __future__ import annotations

import flet as ft

#: 每個頁面的內邊距（水平 28 / 垂直 24，對應設計稿的內容區留白）
VIEW_PADDING = ft.Padding.symmetric(horizontal=28, vertical=24)


def wrap_view(content: ft.Control) -> ft.Container:
    """把一個 View 包上統一的頁面留白。

    Args:
        content: 任一 Flet 控制項（通常是各個 *View）。

    Returns:
        ft.Container: 透明、填滿可用空間的容器（``expand=True`` 讓內容可以填滿中間區域）。
    """
    return ft.Container(content=content, padding=VIEW_PADDING, expand=True)
