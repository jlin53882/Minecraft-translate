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
    show_snack(page, "發生錯誤", color=C.RED)

    # 帶 action 按鈕
    show_snack(page, "已刪除", action_label="復原", on_action=lambda e: restore())

    # 帶文字顏色 (e.g. rules_view.py: C.ON_EM 文字)
    show_snack(page, "已跳至第 1 頁", C.EM, text_color=C.ON_EM)

    # 清除已存在的 SnackBar (預設行為,避免 overlay 累積)
    # icon_preview_view.py 用 clear_existing=True 顯式指定 (預設已 True)
"""

from __future__ import annotations

import flet as ft

from app.ui import design
from app.ui.design import C
from app.ui.design import tone as get_tone
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.redaction import redact_secrets

# 舊呼叫端傳的是「背景色」（RED_600 / GREEN_600 / C.EM …）。新設計的 toast 是中性面板 + 語意色，
# 所以只看顏色屬於哪個色系，轉成對應的語意色組與圖示。
_HUE_TONES = (
    # 先比對語意色名稱（theme 的舊色名已改指向設計系統的語意色）
    (("tertiaryfixed",), "gold", ft.Icons.WARNING_AMBER),
    (("error",), "red", ft.Icons.ERROR_OUTLINE),
    (("primary",), "em", ft.Icons.CHECK_CIRCLE_OUTLINE),
    (("secondary",), "dia", ft.Icons.INFO_OUTLINE),
    (("tertiary",), "ench", ft.Icons.INFO_OUTLINE),
    # 再比對舊的 Material 色票名稱
    (("red",), "red", ft.Icons.ERROR_OUTLINE),
    (("green", "teal", "success"), "em", ft.Icons.CHECK_CIRCLE_OUTLINE),
    (("orange", "amber", "yellow", "warning"), "gold", ft.Icons.WARNING_AMBER),
    (("purple", "deeppurple", "pink"), "ench", ft.Icons.INFO_OUTLINE),
    (("blue", "cyan", "indigo", "info"), "dia", ft.Icons.INFO_OUTLINE),
)

# AppShell 的 statusbar 約 30px；保留 12px 的視覺間距，避免 floating SnackBar
# 蓋住狀態列。這個局部 token 刻意不 import shell/statusbar，避免 UI kit 反向依賴外殼。
SNACK_BOTTOM_MARGIN = 42


def snack_style(color) -> tuple[str, str]:
    """舊的背景色 → (語意色組名稱, 圖示)。認不得的顏色視為中性。"""
    name = str(getattr(color, "value", color) or "").lower()
    for hues, tone_name, icon in _HUE_TONES:
        if any(hue in name for hue in hues):
            return tone_name, icon
    return "neutral", ft.Icons.INFO_OUTLINE


def _clear_existing_snacks(page: ft.Page) -> None:
    """清除已存在的 SnackBar (避免 overlay 累積)。"""
    if hasattr(page, "overlay") and page.overlay:
        for i in range(len(page.overlay) - 1, -1, -1):
            if isinstance(page.overlay[i], ft.SnackBar):
                try:
                    del page.overlay[i]
                except Exception:  # noqa: BLE001, S110
                    pass


def _apply_snack_layout(page: ft.Page, kwargs: dict) -> None:
    """依頁面寬度決定 SnackBar 的左右 margin／底部安全距離（就地修改 kwargs）。"""
    requested_width = kwargs.pop("width", None)
    snack_width = requested_width or 460
    if "margin" not in kwargs:
        page_width = getattr(page, "width", None)
        if isinstance(page_width, (int, float)) and page_width > 0:
            horizontal_margin = max(12, (page_width - snack_width) / 2)
            kwargs["margin"] = ft.Margin.only(
                left=horizontal_margin,
                right=horizontal_margin,
                bottom=SNACK_BOTTOM_MARGIN,
            )
        else:
            # 測試 double / 舊版 Page 可能沒有 width；至少保留底部安全距離。
            kwargs["margin"] = ft.Margin.only(bottom=SNACK_BOTTOM_MARGIN)
    if requested_width is not None:
        # 呼叫端明確指定 width 時保留其 contract；預設路徑刻意用左右 margin
        # 取得相同的視覺寬度，因 Flet 1.0.1 會在 width + margin 同時存在時忽略 bottom。
        kwargs["width"] = requested_width


def _show_snack_dialog(page: ft.Page, snack: ft.SnackBar) -> None:
    """顯示 SnackBar；show_dialog 失敗時退回 overlay。"""
    # 以 page.show_dialog() 顯示，讓 Flet 的 floating SnackBar 真正套用 bottom margin；
    # 預設不再同時傳固定 width，因 Flet 1.0.1 對「固定 width + margin」會忽略 bottom margin。
    try:
        page.show_dialog(snack)
    except Exception:  # noqa: BLE001
        # Fallback 給只支援 overlay 的 Page 實作。
        try:
            page.overlay.append(snack)
            snack.open = True
        except Exception as ex:  # noqa: BLE001
            log_warning(f"[SNACKBAR] show_snack display failed: {ex!r}")


def show_snack(
    page: ft.Page,
    message: str,
    color: str = C.RED,
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
        color: 背景顏色 (預設 C.RED)
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
    message = redact_secrets(message)  # 提示訊息可能帶有使用者輸入或例外文字（#125）
    log_info(f"[UI] SnackBar: {message}")

    if clear_existing:
        _clear_existing_snacks(page)

    tone_name, _icon = snack_style(color)
    tone = get_tone(tone_name)
    # text_color 是舊版「彩色底上的文字色」；新版用語意色當文字、邊框帶同色系，參數只為相容而保留。
    # content 維持單一 ft.Text（既有呼叫端 / 測試會讀 snack.content.value）
    content = ft.Text(message, color=tone.fg, size=13, weight=ft.FontWeight.W_500)

    _apply_snack_layout(page, kwargs)
    snack = ft.SnackBar(
        content=content,
        bgcolor=C.RAISED,
        duration=duration,
        behavior=ft.SnackBarBehavior.FLOATING,
        shape=ft.RoundedRectangleBorder(
            radius=design.RADIUS_CONTROL + 2, side=ft.BorderSide(1, tone.line)
        ),
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

    _show_snack_dialog(page, snack)

    # page.update() 推 render (SnackBar 跳出關鍵)
    # 跟 PR #98 commit 0798022 對齊: caller 可能已經 page.update() 過自己負責的部分,
    # SnackBar 必須自己再 update 才能從畫面跳出
    try:
        page.update()
    except Exception as ex:  # noqa: BLE001
        log_warning(f"[SNACKBAR] show_snack page.update() failed: {ex!r}")

    return snack
