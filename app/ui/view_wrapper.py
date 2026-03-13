"""View 外框（統一卡片樣式）。

設計目的
- main.py 原本用 create_view_wrapper() 把每個 View 包成「一致的卡片」。
- 但 wrapper 放在 main.py 會讓全站樣式規則難集中管理。
- 因此抽成 app.ui.view_wrapper，讓之後調整 padding/margin/radius/shadow 時只要改一處。

注意
- 這個模組只處理 UI 外觀，不應引入 services/translation_tool 等任何業務邏輯。
"""

from __future__ import annotations

import flet as ft

# -------------------------
# 統一樣式常數（可在此集中調整）
# -------------------------

#: 每個頁面卡片內邊距
VIEW_PADDING: int = 20

#: 每個頁面卡片外邊距
VIEW_MARGIN: int = 10

#: 卡片圓角
VIEW_RADIUS: int = 15

#: 卡片陰影（微弱即可，避免太厚重）
VIEW_SHADOW: ft.BoxShadow = ft.BoxShadow(
    blur_radius=5,
    color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK),
    offset=ft.Offset(0, 2),
)

def wrap_view(content: ft.Control) -> ft.Container:
    """把一個 View 包成一致的卡片外框。

    為了讓整個 app 的每個功能頁看起來一致（像放在一張紙/卡片上），
    我們統一：背景色、邊距、圓角、陰影。

    Args:
        content: 任一 Flet 控制項（通常是各個 *View）。

    Returns:
        ft.Container: 具有一致外框樣式的容器。
    """

    # 這裡的 expand=True 很重要：確保內容可以填滿中間區域。
    return ft.Container(
        content=content,
        padding=VIEW_PADDING,
        expand=True,
        bgcolor=ft.Colors.SURFACE,  # 視圖背景色（Material 3 通常是白/深灰）
        border_radius=VIEW_RADIUS,
        margin=VIEW_MARGIN,
        shadow=VIEW_SHADOW,
    )
