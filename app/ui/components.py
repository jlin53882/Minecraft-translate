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
# 進度條元件
# -------------------------

class ProgressCard(ft.Container):
    """進度條卡片元件。

    用於長時間操作的視覺反饋，顯示進度百分比和 ETA。

    屬性：
        current: 目前進度值
        total: 總進度值
    """

    def __init__(
        self,
        title: str,
        current: int = 0,
        total: int = 100,
        on_cancel=None,
        **kwargs,
    ):
        self._current = current
        self._total = total
        self._start_time = None
        self._on_cancel = on_cancel
        self._progress_bar = ft.ProgressBar(
            width=200,
            value=current / total if total > 0 else 0,
        )
        self._percent_text = ft.Text(
            f"{int(current / total * 100)}%" if total > 0 else "0%",
            size=12,
        )
        self._eta_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._status_text = ft.Text("", size=12)

        # 取消按鈕
        cancel_btn = None
        if on_cancel:
            cancel_btn = ft.TextButton(
                text="取消",
                on_click=lambda _: self._on_cancel() if self._on_cancel else None,
            )

        super().__init__(
            padding=15,
            border_radius=8,
            bgcolor=ft.Colors.SURFACE_VARIANT,
            content=ft.Column(
                [
                    ft.Row(
                        [ft.Text(title, weight=ft.FontWeight.BOLD), cancel_btn],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self._progress_bar,
                    ft.Row(
                        [self._percent_text, self._status_text, self._eta_text],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                ],
                spacing=8,
            ),
            **kwargs,
        )

    @property
    def current(self) -> int:
        return self._current

    @current.setter
    def current(self, value: int):
        self._current = value
        self._update_progress()

    @property
    def total(self) -> int:
        return self._total

    @total.setter
    def total(self, value: int):
        self._total = value
        self._update_progress()

    def _update_progress(self):
        """更新進度條顯示"""
        if self._total > 0:
            ratio = self._current / self._total
            self._progress_bar.value = ratio
            self._percent_text.value = f"{int(ratio * 100)}%"
            self._status_text.value = f"{self._current} / {self._total}"

            # 計算 ETA
            if self._start_time and self._current > 0:
                import time
                elapsed = time.time() - self._start_time
                rate = self._current / elapsed
                remaining = self._total - self._current
                eta_seconds = remaining / rate if rate > 0 else 0

                if eta_seconds < 60:
                    self._eta_text.value = f"約 {int(eta_seconds)} 秒"
                elif eta_seconds < 3600:
                    self._eta_text.value = f"約 {int(eta_seconds / 60)} 分鐘"
                else:
                    self._eta_text.value = f"約 {int(eta_seconds / 3600)} 小時"
        else:
            self._progress_bar.value = None  # 不確定進度
            self._percent_text.value = "處理中..."

    def start(self):
        """開始計時"""
        import time
        self._start_time = time.time()

    def set_status(self, status: str):
        """設定狀態文字"""
        self._status_text.value = status
