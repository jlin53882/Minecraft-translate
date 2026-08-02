"""共用 SnackBar 工具。

設計目的 (PR #85 重構, 2026-08-01):
    14 個 view 各自有 _show_snack_bar / _show_snack wrapper method (~95% 重複 ~140 行),
    PR #85 抽共用 helper 取代所有重複。
    User 訊息「純呼叫策略」: 物理刪除 view 內 wrapper,所有 caller 改成
    show_snack(self.page, ...) 直接呼叫,不要保留 1 行 wrapper。

設計對齊 (避免之前 PR #85 commit 0798022 在 extractor_view 的修法重複):
    - try/except + log_warning (避免 page 沒 mount 時 crash)
    - page.update() 主動推 render (SnackBar 真的跳出)
    - page.show_dialog() + fallback (Flet 0.82.2+ 官方推薦)

Flet 0.85.0 SnackBar API: https://flet.dev/docs/controls/snackbar

使用方法:
    from app.ui.snack import show_snack

    # 基本用法
    show_snack(page, "操作完成")

    # 帶顏色
    show_snack(page, "發生錯誤", color=theme.ERROR)

    # 帶 action 按鈕
    show_snack(page, "已刪除", action_label="復原", on_action=lambda e: restore())

    # 帶文字顏色 (e.g. rules_view.py: theme.WHITE 文字)
    show_snack(page, "已跳至第 1 頁", theme.PRIMARY, text_color=theme.WHITE)

    # 清除已存在的 SnackBar (預設行為,避免 overlay 累積)
    # icon_preview_view.py 用 clear_existing=True 顯式指定 (預設已 True)
"""

from __future__ import annotations

import flet as ft
from app.ui import theme
from translation_tool.utils.log_unit import log_info, log_warning


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
    clear_existing: bool = True,
    **kwargs,
) -> ft.SnackBar:
    """統一的 SnackBar 顯示。

    策略:
    1. log_info 記錄 (統一 debug 訊息)
    2. 優先用 page.show_dialog() (Flet 0.82.2+ 官方推薦)
    3. 保留 page.overlay.append() + snack.open = True 作為 fallback
    4. 呼叫 page.update() 推 render (SnackBar 真的跳出)

    Args:
        page: Flet Page 實例
        message: 顯示的文字訊息
        color: 背景顏色 (預設 theme.ERROR)
        duration: 顯示時間 (毫秒), 預設 4000ms
        action_label: action 按鈕文字 (如 "復原")
        on_action: action 按鈕點擊回調
        persist: 是否持續顯示 (action 存在時自動 True)
        text_color: 文字顏色
        show_close_icon: 是否顯示關閉圖標
        close_icon_color: 關閉圖標顏色
        clear_existing: 是否清除已存在的 SnackBar (避免 overlay 累積)
        **kwargs: 傳給 ft.SnackBar 的額外參數

    Returns:
        ft.SnackBar 實例 (可用於後續手動管理)
    """
    log_info(f"[UI] SnackBar: {message}")

    # 清除已存在的 SnackBar (避免 overlay 累積)
    if clear_existing and hasattr(page, "overlay") and page.overlay:
        for i in range(len(page.overlay) - 1, -1, -1):
            if isinstance(page.overlay[i], ft.SnackBar):
                try:
                    del page.overlay[i]
                except Exception:
                    pass

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

    # 優先使用 page.show_dialog (Flet 0.82.2+ 推薦)
    # 保留 fallback 給還沒升級的環境
    try:
        # 嘗試新版 API
        page.show_dialog(snack)
    except Exception:
        # Fallback 到舊版 (overlay.append + open=True)
        try:
            page.overlay.append(snack)
            snack.open = True
        except Exception as ex:
            log_warning(f"[SNACKBAR] show_snack overlay fallback failed: {ex!r}")

    # page.update() 推 render (SnackBar 跳出關鍵)
    # 跟 PR #98 commit 0798022 對齊: caller 可能已經 page.update() 過自己負責的部分,
    # SnackBar 必須自己再 update 才能從畫面跳出
    try:
        page.update()
    except Exception as ex:
        log_warning(f"[SNACKBAR] show_snack page.update() failed: {ex!r}")

    return snack
