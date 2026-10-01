"""kit/inputs.py：按鈕、開關列、分段切換、分頁器。"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import flet as ft

from app.ui import design
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.ui.kit.basics import expand_kwargs

BUTTON_HEIGHTS = {"sm": 30, "md": 36, "lg": 44}
BUTTON_KINDS = ("primary", "secondary", "gold", "danger", "ghost")


def _button_colors(kind: str) -> tuple[str, str, str | None]:
    """(背景, 文字, 邊框)。"""
    if kind == "primary":
        return C.EM, C.ON_EM, None
    if kind == "gold":
        t = get_tone("gold")
        return t.bg, t.fg, t.line
    if kind == "danger":
        t = get_tone("red")
        return t.bg, t.fg, t.line
    if kind == "ghost":
        return ft.Colors.TRANSPARENT, C.MUTED, None
    return C.PANEL, C.TEXT, C.LINE2


def button(
    text: str,
    kind: str = "secondary",
    *,
    icon: str | None = None,
    on_click: Callable | None = None,
    size: str = "md",
    expand: bool | int = False,
    disabled: bool = False,
    tooltip: str | None = None,
) -> ft.Button:
    """統一的按鈕。kind：primary / secondary / gold / danger / ghost；size：sm / md / lg。"""
    if kind not in BUTTON_KINDS:
        raise ValueError(f"未知的按鈕種類：{kind}（可用：{', '.join(BUTTON_KINDS)}）")
    bg, fg, border = _button_colors(kind)
    height = BUTTON_HEIGHTS.get(size, BUTTON_HEIGHTS["md"])
    return ft.Button(
        text,
        icon=icon,
        on_click=on_click,
        height=height,
        disabled=disabled,
        **expand_kwargs(expand),
        tooltip=tooltip,
        style=ft.ButtonStyle(
            bgcolor=bg,
            color=fg,
            shape=ft.RoundedRectangleBorder(
                radius=12 if size == "lg" else design.RADIUS_CONTROL
            ),
            side=ft.BorderSide(1, border) if border else None,
            padding=ft.Padding.symmetric(
                horizontal=24 if size == "lg" else 14 if size == "sm" else 16
            ),
            text_style=ft.TextStyle(
                size=14 if size == "lg" else 12 if size == "sm" else 13,
                weight=ft.FontWeight.W_700
                if kind == "primary"
                else ft.FontWeight.W_500,
            ),
            elevation=0,
        ),
    )


def text_field(
    label: str | None = None,
    *,
    hint: str | None = None,
    value: str | None = None,
    icon: str | None = None,
    multiline: bool = False,
    min_lines: int | None = None,
    max_lines: int | None = None,
    read_only: bool = False,
    mono: bool = False,
    password: bool = False,
    on_change: Callable | None = None,
    on_submit: Callable | None = None,
    expand: bool | int = False,
    width: int | None = None,
    tooltip: str | None = None,
    suffix: ft.Control | None = None,
    dense: bool = True,
) -> ft.TextField:
    """統一外觀的輸入框（深 / 淺色皆適用）。``mono=True`` 用等寬字（路徑、key、JSON）。"""
    return ft.TextField(
        label=label,
        hint_text=hint,
        value=value if value is not None else "",
        prefix_icon=icon,
        multiline=multiline,
        min_lines=min_lines,
        max_lines=max_lines,
        read_only=read_only,
        password=password,
        can_reveal_password=password,
        on_change=on_change,
        on_submit=on_submit,
        width=width,
        tooltip=tooltip,
        suffix=suffix,
        filled=True,
        bgcolor=C.PANEL2,
        border_color=C.LINE2,
        focused_border_color=C.EM,
        border_radius=design.RADIUS_CONTROL,
        border_width=1,
        text_size=13,
        text_style=ft.TextStyle(font_family=design.FONT_MONO) if mono else None,
        label_style=ft.TextStyle(size=12, color=C.MUTED),
        hint_style=ft.TextStyle(size=13, color=C.DIM),
        cursor_color=C.EM,
        content_padding=ft.Padding.symmetric(
            horizontal=12, vertical=10 if dense else 14
        ),
        **expand_kwargs(expand),
    )


def pick_button(
    icon: str = ft.Icons.FOLDER_OPEN,
    tooltip: str | None = None,
    on_click: Callable | None = None,
) -> ft.IconButton:
    """輸入框旁的「瀏覽…」圖示按鈕（選資料夾 / 檔案）。"""
    return ft.IconButton(
        icon=icon,
        tooltip=tooltip,
        on_click=on_click,
        icon_color=C.EM,
        icon_size=20,
        style=ft.ButtonStyle(
            bgcolor=C.PANEL2,
            side=ft.BorderSide(1, C.LINE2),
            shape=ft.RoundedRectangleBorder(radius=design.RADIUS_CONTROL),
        ),
    )


class SwitchRow(ft.Container):
    """設定列：左側標題 / 說明，右側開關。``value`` 可讀寫。"""

    def __init__(
        self,
        label: str,
        sub: str | None = None,
        value: bool = False,
        *,
        on_change: Callable | None = None,
        divider: bool = True,
        disabled: bool = False,
    ) -> None:
        self.label = label
        self.switch = ft.Switch(value=value, on_change=on_change, disabled=disabled)
        text_col = ft.Column(
            [
                ft.Text(label, size=13, weight=ft.FontWeight.W_500, color=C.TEXT),
                *([ft.Text(sub, size=11.5, color=C.DIM)] if sub else []),
            ],
            spacing=2,
            tight=True,
            expand=True,
        )
        super().__init__(
            content=ft.Row(
                [text_col, self.switch],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(vertical=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)) if divider else None,
        )

    @property
    def value(self) -> bool:
        return bool(self.switch.value)

    @value.setter
    def value(self, new: bool) -> None:
        self.switch.value = bool(new)

    @property
    def disabled_state(self) -> bool:
        return bool(self.switch.disabled)

    def set_disabled(self, disabled: bool) -> None:
        self.switch.disabled = disabled


class Segmented(ft.Container):
    """分段切換（設計稿的 ``seg``）：``Segmented([("a", "列表", ft.Icons.X), ...], "a", on_change)``。"""

    def __init__(
        self,
        items: Sequence[tuple[str, str] | tuple[str, str, str | None]],
        value: str | None = None,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        if not items:
            raise ValueError("Segmented 至少需要一個選項")
        self.items = [(it[0], it[1], it[2] if len(it) > 2 else None) for it in items]
        self.on_change = on_change
        self._value = value if value is not None else self.items[0][0]
        self._cells: dict[str, ft.Container] = {}
        for key, label, icon in self.items:
            self._cells[key] = self._make_cell(key, label, icon)
        super().__init__(
            content=ft.Row(list(self._cells.values()), spacing=2, tight=True),
            padding=3,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=11,
        )
        self._paint()

    def _make_cell(self, key: str, label: str, icon: str | None) -> ft.Container:
        parts: list[ft.Control] = []
        if icon:
            parts.append(ft.Icon(icon, size=14))
        if label:
            parts.append(
                ft.Text(label, size=13, weight=ft.FontWeight.W_500, no_wrap=True)
            )
        return ft.Container(
            height=30,
            padding=ft.Padding.symmetric(horizontal=14),
            border_radius=8,
            alignment=ft.Alignment.CENTER,
            ink=True,
            on_click=lambda _e, k=key: self._click(k),
            content=ft.Row(
                parts,
                spacing=7,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _paint(self) -> None:
        for key, cell in self._cells.items():
            selected = key == self._value
            cell.bgcolor = C.RAISED if selected else None
            cell.border = (
                ft.Border.all(1, C.LINE2)
                if selected
                else ft.Border.all(1, ft.Colors.TRANSPARENT)
            )
            for control in cell.content.controls:
                control.color = C.TEXT if selected else C.MUTED

    @property
    def value(self) -> str:
        return self._value

    def select(self, value: str, *, notify: bool = False) -> None:
        if value not in self._cells:
            raise KeyError(value)
        self._value = value
        self._paint()
        try:
            self.update()
        except RuntimeError:
            pass
        if notify and self.on_change:
            self.on_change(value)

    def _click(self, key: str) -> None:
        if key == self._value:
            return
        self.select(key, notify=True)


def page_window(current: int, total_pages: int, size: int = 5) -> list[int | str]:
    """分頁按鈕要顯示哪些頁碼：``[1, "…", 4, 5, 6, "…", 26]``（頁碼從 1 開始）。"""
    if total_pages <= 0:
        return []
    current = min(max(current, 1), total_pages)
    if total_pages <= size + 2:
        return list(range(1, total_pages + 1))
    half = size // 2
    start = max(2, current - half)
    end = min(total_pages - 1, current + half)
    # 靠近兩端時補足中間的按鈕數
    if current <= half + 2:
        start, end = 2, size
    elif current >= total_pages - half - 1:
        start, end = total_pages - size + 1, total_pages - 1
    result: list[int | str] = [1]
    if start > 2:
        result.append("…")
    result.extend(range(start, end + 1))
    if end < total_pages - 1:
        result.append("…")
    result.append(total_pages)
    return result


class Pager(ft.Container):
    """分頁列：「第 a–b 條 / total」+ 頁碼按鈕。頁碼從 1 開始。"""

    def __init__(
        self,
        total_items: int,
        *,
        page: int = 1,
        page_size: int = 50,
        on_change: Callable[[int], None] | None = None,
        unit: str = "條",
    ) -> None:
        self.page_size = max(1, page_size)
        self.unit = unit
        self.on_change = on_change
        self.total_items = max(0, total_items)
        self._current = 1
        self.summary = ft.Text(size=12, color=C.MUTED)
        self.buttons = ft.Row(spacing=4, tight=True)
        super().__init__(
            content=ft.Row(
                [self.summary, self.buttons],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            border=ft.Border.only(top=ft.BorderSide(1, C.LINE)),
        )
        self.set_state(self.total_items, page)

    @property
    def total_pages(self) -> int:
        return max(1, -(-self.total_items // self.page_size))

    @property
    def current_page(self) -> int:
        return self._current

    def set_state(self, total_items: int, page: int | None = None) -> None:
        """更新總筆數 / 目前頁並重畫（不觸發 on_change）。"""
        self.total_items = max(0, total_items)
        if page is not None:
            self._current = page
        self._current = min(max(self._current, 1), self.total_pages)
        self._render()

    def _render(self) -> None:
        if self.total_items == 0:
            self.summary.value = "沒有資料"
        else:
            first = (self._current - 1) * self.page_size + 1
            last = min(self._current * self.page_size, self.total_items)
            self.summary.value = (
                f"第 {first:,}–{last:,} {self.unit} / {self.total_items:,}"
            )
        controls: list[ft.Control] = [
            self._cell(
                ft.Icons.CHEVRON_LEFT, self._current - 1, enabled=self._current > 1
            )
        ]
        for item in page_window(self._current, self.total_pages):
            if item == "…":
                controls.append(self._cell("…", None, enabled=False))
            else:
                controls.append(
                    self._cell(str(item), int(item), selected=item == self._current)
                )
        controls.append(
            self._cell(
                ft.Icons.CHEVRON_RIGHT,
                self._current + 1,
                enabled=self._current < self.total_pages,
            )
        )
        self.buttons.controls = controls

    def _cell(
        self,
        label: str,
        target: int | None,
        *,
        selected: bool = False,
        enabled: bool = True,
    ) -> ft.Container:
        is_icon = label in (ft.Icons.CHEVRON_LEFT, ft.Icons.CHEVRON_RIGHT)
        content: ft.Control = (
            ft.Icon(label, size=14, color=C.TEXT if enabled else C.DIM)
            if is_icon
            else ft.Text(
                label,
                size=12,
                weight=ft.FontWeight.BOLD if selected else ft.FontWeight.W_400,
                color=C.ON_EM if selected else (C.TEXT if enabled else C.DIM),
            )
        )
        return ft.Container(
            width=28,
            height=28,
            border_radius=7,
            alignment=ft.Alignment.CENTER,
            bgcolor=C.EM if selected else None,
            border=None if selected else ft.Border.all(1, C.LINE),
            content=content,
            ink=enabled and not selected,
            on_click=(lambda _e, t=target: self.goto(t))
            if enabled and target
            else None,
        )

    def goto(self, page: int) -> None:
        page = min(max(page, 1), self.total_pages)
        if page == self._current:
            return
        self._current = page
        self._render()
        try:
            self.update()
        except RuntimeError:
            pass
        if self.on_change:
            self.on_change(page)
