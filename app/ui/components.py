"""共用 UI 元件（可重用的小拼裝）。

原則
- 只放純 UI / 樣式封裝，不碰任何 services / translation_tool 業務邏輯。
- 讓各 View 盡量用「組裝」而不是重複定義同一套樣式。
- 這些元件要容易單元測試（驗證屬性即可，不做渲染快照）。

目前用途
- 主要用於 TranslationView / CacheView 等大型頁面，降低重複樣式。
"""

from __future__ import annotations

import flet as ft

# -------------------------
# 基礎視覺常數（集中管理）
# -------------------------

CARD_PADDING: int = 16
CARD_RADIUS: int = 10
CARD_BORDER_COLOR = ft.Colors.BLACK12
CARD_BG_COLOR = ft.Colors.WHITE
DIVIDER_COLOR = ft.Colors.GREY_200

def section_header(
    title: str,
    icon: str,
    *,
    icon_color: str = ft.Colors.BLUE_GREY_700,
    icon_size: int = 18,
    title_size: int = 16,
) -> ft.Row:
    """區塊標題列（icon + title）。

    Args:
        title: 區塊標題
        icon: ft.Icons.*
        icon_color: icon 顏色（預設偏中性）
        icon_size: icon 尺寸
        title_size: 標題文字尺寸

    Returns:
        ft.Row
    """

    return ft.Row(
        [
            ft.Icon(icon, size=icon_size, color=icon_color),
            ft.Text(title, weight=ft.FontWeight.BOLD, size=title_size),
        ],
        spacing=8,
    )

def styled_card(
    *,
    title: str,
    icon: str,
    content: ft.Control,
    expand: bool = False,
    icon_color: str = ft.Colors.BLUE_GREY_700,
) -> ft.Container:
    """統一的「區塊卡片」外觀。

    這個元件用在大型 View 內，把每個區塊包成一致的白底卡片。

    Args:
        title: 區塊標題
        icon: 區塊 icon
        content: 內容控制項
        expand: 是否要讓卡片本身 expand

    Returns:
        ft.Container
    """

    body = ft.Container(expand=True, content=content) if expand else content

    return ft.Container(
        expand=expand,
        padding=CARD_PADDING,
        border_radius=CARD_RADIUS,
        bgcolor=CARD_BG_COLOR,
        border=ft.border.all(1, CARD_BORDER_COLOR),
        content=ft.Column(
            [
                section_header(title, icon, icon_color=icon_color),
                ft.Divider(height=1, color=DIVIDER_COLOR),
                body,
            ],
            spacing=12,
            expand=expand,
        ),
    )

def primary_button(
    text: str,
    *,
    icon: str | None = None,
    tooltip: str | None = None,
    on_click=None,
    height: int = 42,
    bgcolor: str = ft.Colors.BLUE_700,
) -> ft.ElevatedButton:
    """主動作按鈕（整個 App 統一的 primary style）。

    Args:
        bgcolor: 主色背景（預設藍色）。需要特殊語意（例如成功/危險）時可換色。
    """

    return ft.ElevatedButton(
        text,
        icon=icon,
        tooltip=tooltip,
        height=height,
        style=ft.ButtonStyle(
            color=ft.Colors.WHITE,
            bgcolor=bgcolor,
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=16,
        ),
        on_click=on_click,
    )

def secondary_button(
    text: str,
    *,
    icon: str | None = None,
    tooltip: str | None = None,
    on_click=None,
    height: int = 42,
) -> ft.OutlinedButton:
    """次要按鈕（outlined）。"""

    return ft.OutlinedButton(
        text,
        icon=icon,
        tooltip=tooltip,
        height=height,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6), padding=16),
        on_click=on_click,
    )

# -------------------------
# 通知元件
# -------------------------

def create_snackbar(
    message: str,
    color: str = ft.Colors.RED_400,
) -> ft.SnackBar:
    """建立 SnackBar 元件（統一的樣式）。

    Args:
        message: 顯示的文字內容
        color: 背景顏色，預設紅色

    Returns:
        SnackBar 元件
    """
    return ft.SnackBar(
        ft.Text(message),
        bgcolor=color,
    )


# -------------------------
# 統一狀態元件
# -------------------------

def loading_state(
    message: str = "載入中...",
    show_spinner: bool = True,
) -> ft.Container:
    """統一的載入狀態顯示。

    Args:
        message: 顯示的文字
        show_spinner: 是否顯示旋轉圖標

    Returns:
        ft.Container
    """
    spinner = ft.CupertinoActivityIndicator() if show_spinner else None

    return ft.Container(
        alignment=ft.alignment.center,
        padding=40,
        content=ft.Column(
            [
                spinner,
                ft.Text(message, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
            ] if spinner else [ft.Text(message, size=14, color=ft.Colors.ON_SURFACE_VARIANT)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),
    )


def empty_state(
    icon: str,
    title: str,
    message: str,
    action_button: ft.Control | None = None,
) -> ft.Container:
    """統一的空資料狀態顯示。

    Args:
        icon: 圖標名稱
        title: 標題
        message: 描述文字
        action_button: 操作按鈕（可選）

    Returns:
        ft.Container
    """
    return ft.Container(
        alignment=ft.alignment.center,
        padding=40,
        content=ft.Column(
            [
                ft.Icon(icon, size=48, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(title, size=16, weight=ft.FontWeight.BOLD),
                ft.Text(message, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
            ] + ([action_button] if action_button else []),
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),
    )


def error_state(
    icon: str,
    title: str,
    message: str,
    retry_button: ft.Control | None = None,
) -> ft.Container:
    """統一的錯誤狀態顯示。

    Args:
        icon: 圖標名稱
        title: 標題
        message: 錯誤描述
        retry_button: 重試按鈕（可選）

    Returns:
        ft.Container
    """
    return ft.Container(
        alignment=ft.alignment.center,
        padding=40,
        content=ft.Column(
            [
                ft.Icon(icon, size=48, color=ft.Colors.ERROR),
                ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.ERROR),
                ft.Text(message, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
            ] + ([retry_button] if retry_button else []),
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),
    )
