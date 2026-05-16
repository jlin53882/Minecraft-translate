"""共用 SnackBar 工具。

Flet 0.85.0 SnackBar API: https://flet.dev/docs/controls/snackbar

使用方法:
    from app.ui.snack import show_snack

    # 基本用法
    show_snack(page, "操作完成")

    # 帶顏色
    show_snack(page, "發生錯誤", color=theme.ERROR)

    # 帶 action 按鈕
    show_snack(page, "已刪除", action_label="復原", on_action=lambda e: restore())
"""

from __future__ import annotations

import flet as ft
from app.ui import theme
from translation_tool.utils.log_unit import log_info


def show_snack(
    page: ft.Page,
    message: str,
    color: str = theme.ERROR,
    *,
    duration: int = 4000,
    action_label: str | None = None,
    on_action=None,
    persist: bool | None = None,
    text_color: str | None = None,
    show_close_icon: bool = False,
    close_icon_color: str | None = None,
    clear_existing: bool = False,
    **kwargs,
) -> ft.SnackBar:
    """統一的 SnackBar 顯示。

    優先使用 page.show_dialog()（Flet 0.82.2+ 官方推薦），
    相容舊寫法 page.overlay.append() + snack.open = True。

    Args:
        page: Flet Page 實例
        message: 顯示的文字訊息
        color: 背景顏色（預設 theme.ERROR）
        duration: 顯示時間（毫秒），預設 4000ms
        action_label: action 按鈕文字（如 "復原"）
        on_action: action 按鈕點擊回調
        persist: 是否持續顯示（action 存在時自動 True）
        text_color: 文字顏色
        show_close_icon: 是否顯示關閉圖標
        close_icon_color: 關閉圖標顏色
        clear_existing: 是否清除已存在的 SnackBar（避免 overlay 累積）
        **kwargs: 传递给 ft.SnackBar 的額外參數

    Returns:
        ft.SnackBar 實例
    """
    log_info(f"[UI] SnackBar: {message}")
    txt = ft.Text(message, color=text_color) if text_color else ft.Text(message)

    snack = ft.SnackBar(
        content=txt,
        bgcolor=color,
        duration=duration,
        **kwargs,
    )

    if action_label:
        snack.action = action_label
        if on_action:
            snack.on_action = on_action
        if persist is None:
            snack.persist = True

    if show_close_icon:
        snack.show_close_icon = True
        if close_icon_color:
            snack.close_icon_color = close_icon_color

    _show_snack_bar(page, snack, clear_existing=clear_existing)
    return snack


def _show_snack_bar(page: ft.Page, snack: ft.SnackBar, clear_existing: bool = False) -> None:
    """內部方法：實際顯示 SnackBar。

    優先使用 page.show_dialog()（Flet 0.82.2+ 官方推薦），
    失敗時 fallback 到 overlay.append() 方式。
    """
    if clear_existing:
        for i in range(len(page.overlay) - 1, -1, -1):
            if isinstance(page.overlay[i], ft.SnackBar):
                del page.overlay[i]

    try:
        page.show_dialog(snack)
        page.update()
    except Exception:
        page.overlay.append(snack)
        snack.open = True
        page.update()