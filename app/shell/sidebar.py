"""app/shell/sidebar.py：左側導覽（品牌、快速跳轉入口、4 大分組、設定、主題切換）。

只負責畫面與選取狀態；切頁、開面板、切主題都透過 callback 交給 ``AppShell``。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.view_registry import (
    NAV_GROUPS,
    SYSTEM_GROUP,
    ViewSpec,
    get_spec,
    specs_in_group,
)

SIDEBAR_WIDTH = 236
SIDEBAR_WIDTH_COMPACT = 68
ITEM_HEIGHT = 38


class NavItem(ft.Container):
    """側欄的一個頁面項目：圖示 + 名稱 + 可選的數字 / 圓點徽章。"""

    def __init__(
        self, spec: ViewSpec, on_select: Callable[[str], None], *, compact: bool = False
    ) -> None:
        self.spec = spec
        self._on_select = on_select
        self.selected = False
        self.compact = compact
        self._badge_slot = ft.Container()
        self.icon = ft.Icon(spec.icon, size=19, color=C.MUTED)
        self.label = ft.Text(
            spec.label,
            size=13.5,
            weight=ft.FontWeight.W_500,
            color=C.MUTED,
            no_wrap=True,
            expand=True,
        )
        self._row = ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        super().__init__(
            content=self._row,
            height=ITEM_HEIGHT,
            padding=ft.Padding.symmetric(horizontal=12),
            border_radius=design.RADIUS_CONTROL,
            ink=True,
            on_click=lambda _e: self._on_select(self.spec.key),
            tooltip=spec.label if compact else None,
        )
        self._layout()

    def _layout(self) -> None:
        self._row.controls = (
            [self.icon] if self.compact else [self.icon, self.label, self._badge_slot]
        )
        self._row.alignment = (
            ft.MainAxisAlignment.CENTER if self.compact else ft.MainAxisAlignment.START
        )
        self.tooltip = self.spec.label if self.compact else None

    def set_compact(self, compact: bool) -> None:
        self.compact = compact
        self._layout()

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self.bgcolor = C.EM_BG if selected else None
        self.icon.color = C.EM if selected else C.MUTED
        self.label.color = C.TEXT if selected else C.MUTED
        self.label.weight = ft.FontWeight.W_700 if selected else ft.FontWeight.W_500

    def set_badge(self, value: int | str | None, tone: str = "gold") -> None:
        """``None`` 清除徽章；``"•"`` 顯示圓點；其他顯示文字 / 數字。"""
        if value is None or value == 0:
            self._badge_slot.content = None
        elif value == "•":
            self._badge_slot.content = ft.Container(
                width=8, height=8, border_radius=4, bgcolor=get_tone(tone).fg
            )
        else:
            self._badge_slot.content = kit.count_badge(value, tone)


class Sidebar(ft.Container):
    """整條側欄。``set_selected`` / ``set_badge`` 由外殼呼叫。"""

    def __init__(
        self,
        *,
        on_select: Callable[[str], None],
        on_search: Callable[[], None],
        on_theme: Callable[[str], None],
        mode: str = "dark",
        compact: bool = False,
    ) -> None:
        self._on_select = on_select
        self._on_search = on_search
        self._on_theme = on_theme
        self.compact = compact
        self.items: dict[str, NavItem] = {}
        self._group_labels: list[ft.Control] = []
        self._selected: str | None = None
        self._badges: dict[str, tuple[int | str, str]] = {}
        self._brand_text = ft.Column(
            [
                ft.Text(
                    "MC 繁化工坊",
                    size=15,
                    weight=ft.FontWeight.W_800,
                    color=C.TEXT,
                    no_wrap=True,
                ),
                ft.Text("Minecraft 模組包翻譯", size=11, color=C.DIM, no_wrap=True),
            ],
            spacing=0,
            tight=True,
        )
        self._brand_row = ft.Row(
            spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )
        self._search_label = ft.Row(
            [
                ft.Icon(ft.Icons.SEARCH, size=16, color=C.DIM),
                ft.Text("快速跳轉…", size=12.5, color=C.DIM, expand=True),
                kit.kbd("Ctrl"),
                kit.kbd("P"),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.search_button = ft.Container(
            content=self._search_label,
            height=38,
            padding=ft.Padding.symmetric(horizontal=12),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
            ink=True,
            on_click=lambda _e: self._on_search(),
            tooltip="快速跳轉 (Ctrl+P)",
        )
        self.theme_toggle = kit.Segmented(
            [
                ("light", "", ft.Icons.LIGHT_MODE_OUTLINED),
                ("dark", "", ft.Icons.DARK_MODE_OUTLINED),
            ],
            mode,
            on_change=lambda m: self._on_theme(m),
        )
        self._nav_column = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        self._bottom = ft.Column(spacing=10)
        super().__init__(
            width=SIDEBAR_WIDTH,
            bgcolor=C.SIDE,
            border=ft.Border.only(right=ft.BorderSide(1, C.LINE)),
            padding=ft.Padding.symmetric(horizontal=12, vertical=14),
            content=ft.Column(
                [self._brand_row, self.search_button, self._nav_column, self._bottom],
                spacing=14,
                expand=True,
            ),
        )
        self._build()

    # -- 建構 ----------------------------------------------------------------

    def _brand_logo(self) -> ft.Container:
        return ft.Container(
            width=36,
            height=36,
            border_radius=10,
            bgcolor=C.EM_BG,
            border=ft.Border.all(1, C.EM_LINE),
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.VIEW_IN_AR, size=20, color=C.EM),
        )

    def _build(self) -> None:
        self.items.clear()
        self._group_labels.clear()
        nav: list[ft.Control] = []
        for group in NAV_GROUPS:
            specs = specs_in_group(group.key)
            if not specs:
                continue
            label = ft.Container(
                content=ft.Text(
                    group.label,
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=C.DIM,
                ),
                padding=ft.Padding(left=12, top=12, right=0, bottom=4),
                visible=not self.compact,
            )
            self._group_labels.append(label)
            nav.append(label)
            for spec in specs:
                nav.append(self._make_item(spec))
            if self.compact:
                nav.append(ft.Container(height=4))
        self._nav_column.controls = nav

        system_items = [
            self._make_item(spec) for spec in specs_in_group(SYSTEM_GROUP.key)
        ]
        self._bottom.controls = [
            ft.Divider(height=1, color=C.LINE),
            *system_items,
            ft.Container(content=self.theme_toggle, alignment=ft.Alignment.CENTER)
            if not self.compact
            else ft.Container(),
        ]
        self._brand_row.controls = (
            [self._brand_logo()]
            if self.compact
            else [self._brand_logo(), self._brand_text]
        )
        self.search_button.content = (
            ft.Icon(ft.Icons.SEARCH, size=18, color=C.DIM)
            if self.compact
            else self._search_label
        )
        self.search_button.alignment = ft.Alignment.CENTER if self.compact else None
        self.width = SIDEBAR_WIDTH_COMPACT if self.compact else SIDEBAR_WIDTH
        if self._selected:
            self.set_selected(self._selected)

    def _make_item(self, spec: ViewSpec) -> NavItem:
        item = NavItem(spec, self._on_select, compact=self.compact)
        if spec.key in self._badges:
            item.set_badge(*self._badges[spec.key])
        self.items[spec.key] = item
        return item

    # -- 狀態 ----------------------------------------------------------------

    def set_selected(self, view_key: str) -> None:
        get_spec(view_key)  # 未知 key 直接丟 KeyError
        self._selected = view_key
        for key, item in self.items.items():
            item.set_selected(key == view_key)

    @property
    def selected(self) -> str | None:
        return self._selected

    def set_badge(
        self, view_key: str, value: int | str | None, tone: str = "gold"
    ) -> None:
        if value is None or value == 0:
            self._badges.pop(view_key, None)
        else:
            self._badges[view_key] = (value, tone)
        item = self.items.get(view_key)
        if item is not None:
            item.set_badge(value, tone)

    def set_mode(self, mode: str) -> None:
        """同步主題切換鈕（不觸發 callback）。"""
        self.theme_toggle.select(mode, notify=False)

    def set_compact(self, compact: bool) -> None:
        if compact == self.compact:
            return
        self.compact = compact
        self._build()
