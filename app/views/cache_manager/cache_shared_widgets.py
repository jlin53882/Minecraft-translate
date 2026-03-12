"""app/views/cache_manager/cache_shared_widgets.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import flet as ft


def bordered_block(*, content: ft.Control, padding: int = 10, radius: int = 10, expand: bool = False):
    """Cache UI 共用外框。

    用在總覽卡片/右側資訊卡/日誌卡，統一邊框與圓角。
    """
    return ft.Container(
        expand=expand,
        padding=padding,
        border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=radius,
        content=content,
    )
