"""kit/cards.py：頁首、區塊卡片、統計卡。"""

from __future__ import annotations

from collections.abc import Sequence

import flet as ft

from app.ui import design
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.ui.kit.basics import expand_kwargs, tone_icon

CARD_RADIUS = design.RADIUS_CARD
BODY_PADDING = ft.Padding(left=18, top=14, right=18, bottom=18)
HEADER_PADDING = ft.Padding(left=18, top=14, right=18, bottom=0)


def page_header(
    title: str,
    subtitle: str = "",
    *,
    icon: str | None = None,
    tone: str = "em",
    actions: Sequence[ft.Control] = (),
) -> ft.Row:
    """頁面標題列：左側圖示 + 標題 / 說明，右側主要動作。"""
    heading = ft.Column(
        [
            ft.Text(title, size=22, weight=ft.FontWeight.BOLD, color=C.TEXT),
            *([ft.Text(subtitle, size=13, color=C.MUTED)] if subtitle else []),
        ],
        spacing=2,
        tight=True,
    )
    left: list[ft.Control] = []
    if icon:
        left.append(tone_icon(icon, tone, size=22, box=46, radius=13))
    left.append(heading)
    return ft.Row(
        [
            ft.Row(left, spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row(
                list(actions),
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.END,
    )


class SectionCard(ft.Container):
    """區塊卡片：標題列（圖示 / 標題 / 徽章 / 右側動作）+ 內容，可收合。

    - ``flush=True`` 時內容不留內距（表格、日誌貼齊卡片邊緣）。
    - ``collapsible=True`` 時標題列最右側出現收合按鈕；內容由 ``body`` 控制可見性。
    - 沒有 ``title`` 時只畫內容（用於「執行面板」這類自帶標題的卡片）。
    """

    def __init__(
        self,
        title: str | None = None,
        content: ft.Control | None = None,
        *,
        icon: str | None = None,
        tone: str = "em",
        badge: ft.Control | None = None,
        actions: Sequence[ft.Control] = (),
        expand: bool | int = False,
        flush: bool = False,
        collapsible: bool = False,
        collapsed: bool = False,
        padding: ft.PaddingValue | None = None,
        bgcolor: str = C.PANEL,
        border_color: str = C.LINE,
    ) -> None:
        self.title = title
        self._collapsible = collapsible
        self._collapsed = collapsed
        self.collapse_button: ft.IconButton | None = None

        self.body = ft.Container(
            content=content,
            padding=padding if padding is not None else (0 if flush else BODY_PADDING),
            visible=not collapsed,
            **expand_kwargs(bool(expand)),
        )

        controls: list[ft.Control] = []
        if title is not None:
            controls.append(self._build_header(title, icon, tone, badge, actions))
        controls.append(self.body)

        super().__init__(
            content=ft.Column(controls, spacing=0, **expand_kwargs(bool(expand))),
            bgcolor=bgcolor,
            border=ft.Border.all(1, border_color),
            border_radius=CARD_RADIUS,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            **expand_kwargs(expand),
        )

    def _build_header(
        self,
        title: str,
        icon: str | None,
        tone: str,
        badge: ft.Control | None,
        actions: Sequence[ft.Control],
    ) -> ft.Container:
        left: list[ft.Control] = []
        if icon:
            left.append(tone_icon(icon, tone, size=15))
        left.append(ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT))
        if badge is not None:
            left.append(badge)
        right: list[ft.Control] = list(actions)
        if self._collapsible:
            self.collapse_button = ft.IconButton(
                icon=ft.Icons.EXPAND_MORE if self._collapsed else ft.Icons.EXPAND_LESS,
                icon_size=18,
                icon_color=C.MUTED,
                tooltip="收合 / 展開",
                on_click=self._toggle,
            )
            right.append(self.collapse_button)
        return ft.Container(
            padding=HEADER_PADDING,
            content=ft.Row(
                [
                    ft.Row(
                        left,
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        right,
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        """設定收合狀態並更新（尚未加入頁面時只改狀態）。"""
        self._collapsed = collapsed
        self.body.visible = not collapsed
        if self.collapse_button is not None:
            self.collapse_button.icon = (
                ft.Icons.EXPAND_MORE if collapsed else ft.Icons.EXPAND_LESS
            )
        try:
            self.update()
        except RuntimeError:
            pass  # 尚未加入 page（單元測試或建構階段）

    def _toggle(self, _e=None) -> None:
        self.set_collapsed(not self._collapsed)


def section_card(
    title: str | None = None, content: ft.Control | None = None, **kwargs
) -> SectionCard:
    """``SectionCard`` 的函式寫法。"""
    return SectionCard(title, content, **kwargs)


def stat_card(
    label: str,
    value: str,
    *,
    icon: str | None = None,
    tone: str = "em",
    delta: str | None = None,
    delta_tone: str = "em",
    expand: bool | int = False,
) -> ft.Container:
    """統計卡：圖示 + 標籤、大數字（等寬字型）、變化量。"""
    head: list[ft.Control] = []
    if icon:
        head.append(tone_icon(icon, tone, size=14))
    head.append(ft.Text(label, size=12, color=C.MUTED, no_wrap=True))
    rows: list[ft.Control] = [
        ft.Row(head, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Text(
            value,
            size=25,
            weight=ft.FontWeight.BOLD,
            color=C.TEXT,
            font_family=design.FONT_MONO,
        ),
    ]
    if delta:
        rows.append(ft.Text(delta, size=11.5, color=get_tone(delta_tone).fg))
    return ft.Container(
        content=ft.Column(rows, spacing=4, tight=True),
        padding=ft.Padding.symmetric(horizontal=16, vertical=13),
        bgcolor=C.PANEL,
        border=ft.Border.all(1, C.LINE),
        border_radius=CARD_RADIUS,
        **expand_kwargs(expand),
    )
