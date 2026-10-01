"""app/shell/palette.py：快速跳轉面板（Ctrl+P）。

- ``PaletteItem`` / ``build_items`` / ``filter_items``：純邏輯（頁面 + 動作、搜尋與排序），可單元測試。
- ``CommandPalette``：畫面。支援 ↑ ↓ 選取、Enter 執行、Esc 關閉；由 ``handle_key`` 接收全域鍵盤事件。
- ``show_palette`` / ``close_palette``：以 ``page.overlay`` 顯示 / 移除。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from app.view_registry import VIEW_SPECS, get_group

KIND_PAGE = "page"
KIND_ACTION = "action"
KIND_LABELS = {KIND_PAGE: "頁面", KIND_ACTION: "動作"}

PALETTE_WIDTH = 640
MAX_VISIBLE = 9
ROW_STEP = 50  # 一列 48 + 間距 2
HEADER_HEIGHT = 30


@dataclass(frozen=True)
class PaletteItem:
    id: str
    label: str
    kind: str = KIND_PAGE
    sub: str = ""
    icon: str = ft.Icons.ARROW_FORWARD
    shortcut: str | None = None  # 顯示用，例如 "Ctrl+1"
    keywords: tuple[str, ...] = ()
    run: Callable[[], None] | None = field(default=None, compare=False)


def page_items(on_open: Callable[[str], None]) -> list[PaletteItem]:
    """每個頁面一個項目；副標題顯示所屬分組。"""
    items = []
    for spec in VIEW_SPECS:
        group = get_group(spec.group)
        items.append(
            PaletteItem(
                id=f"page:{spec.key}",
                label=spec.label,
                kind=KIND_PAGE,
                sub=group.label,
                icon=spec.icon,
                shortcut=f"Ctrl+{spec.shortcut}" if spec.shortcut else None,
                keywords=(spec.key, *spec.keywords),
                run=lambda key=spec.key: on_open(key),
            )
        )
    return items


def score(item: PaletteItem, query: str) -> int:
    """符合程度；0 表示不符合。越高越前面：名稱開頭 > 名稱包含 > 關鍵字開頭 > 關鍵字 / 副標包含。"""
    q = query.strip().lower()
    if not q:
        return 1
    label = item.label.lower()
    if label.startswith(q):
        return 100
    if q in label:
        return 80
    keywords = [k.lower() for k in item.keywords]
    if any(k.startswith(q) for k in keywords):
        return 60
    if any(q in k for k in keywords) or q in item.sub.lower():
        return 40
    # 所有以空白分隔的詞都要出現
    parts = q.split()
    haystack = " ".join([label, item.sub.lower(), *keywords])
    if len(parts) > 1 and all(p in haystack for p in parts):
        return 20
    return 0


def filter_items(items: Sequence[PaletteItem], query: str) -> list[PaletteItem]:
    """依符合程度排序（同分維持原順序）；沒有輸入時回傳全部。"""
    scored = [(score(item, query), index, item) for index, item in enumerate(items)]
    matched = [t for t in scored if t[0] > 0]
    matched.sort(key=lambda t: (-t[0], t[1]))
    return [item for _s, _i, item in matched]


class CommandPalette(ft.Container):
    """面板本體（不含遮罩）。"""

    def __init__(
        self,
        items: Sequence[PaletteItem],
        on_close: Callable[[], None],
        on_run: Callable[[PaletteItem], None] | None = None,
    ) -> None:
        self.all_items = list(items)
        self.results: list[PaletteItem] = list(self.all_items)
        self.index = 0
        self._on_close = on_close
        self._on_run = on_run
        self.search = ft.TextField(
            hint_text="搜尋頁面或動作…",
            autofocus=True,
            border=ft.NoInputBorder(),
            text_size=15,
            content_padding=ft.Padding.symmetric(vertical=10),
            on_change=self._on_change,
            on_submit=lambda _e: self.run_selected(),
            expand=True,
        )
        self.list = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, tight=True)
        self.list_box = ft.Container(content=self.list, padding=8)
        super().__init__(
            width=PALETTE_WIDTH,
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE2),
            border_radius=design.RADIUS_DIALOG,
            shadow=ft.BoxShadow(
                blur_radius=40,
                color=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
                offset=ft.Offset(0, 16),
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            on_click=lambda _e: None,  # 點面板本身不要穿透到遮罩而關閉
            content=ft.Column(
                [
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=18),
                        border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.SEARCH, size=20, color=C.MUTED),
                                self.search,
                                kit.kbd("Esc"),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ),
                    self.list_box,
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=18, vertical=10),
                        bgcolor=C.PANEL2,
                        border=ft.Border.only(top=ft.BorderSide(1, C.LINE)),
                        content=ft.Row(
                            [
                                kit.kbd("↑"),
                                kit.kbd("↓"),
                                ft.Text("選取", size=11.5, color=C.DIM),
                                kit.kbd("Enter"),
                                ft.Text("前往", size=11.5, color=C.DIM),
                                kit.kbd("Esc"),
                                ft.Text("關閉", size=11.5, color=C.DIM),
                            ],
                            spacing=8,
                        ),
                    ),
                ],
                spacing=0,
                tight=True,
            ),
        )
        self._render()

    # -- 搜尋 / 選取 ---------------------------------------------------------

    def _on_change(self, e) -> None:
        self.set_query(e.control.value or "")
        try:
            self.update()
        except RuntimeError:
            pass

    def set_query(self, query: str) -> None:
        self.results = filter_items(self.all_items, query)
        self.index = 0
        self._render()

    def move(self, delta: int) -> None:
        if not self.results:
            return
        self.index = (self.index + delta) % len(self.results)
        self._render()
        try:
            self.update()
        except RuntimeError:
            pass

    @property
    def selected(self) -> PaletteItem | None:
        if 0 <= self.index < len(self.results):
            return self.results[self.index]
        return None

    def run_selected(self) -> None:
        self.run_item(self.selected)

    def run_item(self, item: PaletteItem | None) -> None:
        if item is None:
            return
        self._on_close()
        if item.run is not None:
            item.run()
        if self._on_run is not None:
            self._on_run(item)

    def handle_key(self, key: str) -> bool:
        """處理鍵盤；回傳是否已處理。"""
        name = key.lower()
        if name == "escape":
            self._on_close()
            return True
        if name == "arrow down":
            self.move(1)
            return True
        if name == "arrow up":
            self.move(-1)
            return True
        return False

    # -- 畫面 ---------------------------------------------------------------

    def _render(self) -> None:
        controls: list[ft.Control] = []
        if not self.results:
            controls.append(
                kit.empty_state(
                    "找不到符合的項目", "試試其他關鍵字，例如「翻譯」或「快取」"
                )
            )
        last_kind: str | None = None
        for position, item in enumerate(self.results):
            if item.kind != last_kind:
                last_kind = item.kind
                controls.append(
                    ft.Container(
                        padding=ft.Padding(left=12, top=8, right=0, bottom=4),
                        content=ft.Text(
                            KIND_LABELS.get(item.kind, item.kind),
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=C.DIM,
                        ),
                    )
                )
            controls.append(self._row(item, position == self.index))
        self.list.controls = controls
        # 高度隨結果數量變化（最多 MAX_VISIBLE 列，再多就捲動）
        rows = len(self.results) or 3  # 空狀態大約佔 3 列
        headers = len({item.kind for item in self.results})
        self.list_box.height = (
            min(rows, MAX_VISIBLE) * ROW_STEP + headers * HEADER_HEIGHT + 16
        )

    def _row(self, item: PaletteItem, active: bool) -> ft.Container:
        tone = "em" if active else "neutral"
        trailing: list[ft.Control] = []
        if item.shortcut:
            trailing = [kit.kbd(part) for part in item.shortcut.split("+")]
        return ft.Container(
            height=48,
            padding=ft.Padding.symmetric(horizontal=12),
            border_radius=design.RADIUS_CONTROL,
            bgcolor=C.EM_BG if active else None,
            ink=True,
            on_click=lambda _e, it=item: self.run_item(it),
            content=ft.Row(
                [
                    kit.tone_icon(item.icon, tone, size=16, box=30),
                    ft.Column(
                        [
                            ft.Text(
                                item.label,
                                size=13.5,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT,
                            ),
                            *(
                                [ft.Text(item.sub, size=11, color=C.DIM)]
                                if item.sub
                                else []
                            ),
                        ],
                        spacing=0,
                        tight=True,
                        expand=True,
                    ),
                    ft.Row(trailing, spacing=3, tight=True),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def focus(self, page: ft.Page) -> None:
        """聚焦搜尋框（Flet 1.0 的 ``focus`` 是 coroutine，要交給 ``run_task``）。"""
        page.run_task(self.search.focus)


def show_palette(
    page: ft.Page,
    items: Sequence[PaletteItem],
    on_run: Callable[[PaletteItem], None] | None = None,
) -> CommandPalette:
    """在 ``page.overlay`` 顯示面板並回傳它；已開著時直接回傳現有的那個。"""
    existing = current_palette(page)
    if existing is not None:
        return existing
    palette = CommandPalette(items, on_close=lambda: close_palette(page), on_run=on_run)
    scrim = ft.Container(
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.55, ft.Colors.BLACK),
        alignment=ft.Alignment.TOP_CENTER,
        padding=ft.Padding(left=0, top=96, right=0, bottom=0),
        on_click=lambda _e: close_palette(page),
        content=palette,
        data="command_palette",
    )
    page.overlay.append(scrim)
    page.update()
    palette.focus(page)
    return palette


def current_palette(page: ft.Page) -> CommandPalette | None:
    for control in page.overlay:
        if isinstance(getattr(control, "content", None), CommandPalette):
            return control.content
    return None


def close_palette(page: ft.Page) -> None:
    """移除面板遮罩（沒開著時什麼都不做）。"""
    for control in list(page.overlay):
        if isinstance(getattr(control, "content", None), CommandPalette):
            page.overlay.remove(control)
    page.update()
