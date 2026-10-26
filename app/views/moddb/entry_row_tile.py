"""條目清單列：固定數量、原地更新內容的 tile 池。

翻頁與換篩選時只改 tile 的文字與顏色，不重建控制項。Flet 對「新增 / 移除控制項」要做
註冊、序列化與配對，50 列 × 十幾個子控制項的成本遠高於「同一批控制項改幾個屬性」，
所以清單的列由 ``EntryTilePool`` 一次建好重複使用；沒有用到的列用 ``visible`` 隱藏。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import flet as ft

from app.services_impl.moddb_source_service import (
    REVIEW_BADGE_LABELS,
    source_label,
    source_tone,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.update_scope import isolate
from app.views.moddb.format_issue_rendering import quality_issue_summary
from app.views.moddb.formatting import STATE_TONES, shorten
from app.views.moddb.smart_review_marks import MARK_STYLES

_QUALITY_WARNING_STATES = frozenset(
    {"mismatch", "whitespace", "unknown_source", "missing_translation"}
)


class _Chip:
    """可重複使用的狀態膠囊（與 ``kit.chip`` 同樣式）；內容沒變就不重設屬性。"""

    def __init__(self) -> None:
        self.text = ft.Text(
            "", size=11.5, weight=ft.FontWeight.W_500, no_wrap=True, color=C.TEXT
        )
        self.box = ft.Container(
            height=22,
            padding=ft.Padding.symmetric(horizontal=9),
            border_radius=11,
            alignment=ft.Alignment.CENTER,
            content=self.text,
            visible=False,
        )
        self._state: tuple | None = None

    def show(self, label: str, tone: str, tooltip: str | None = None) -> None:
        state = (label, tone, tooltip)
        if state == self._state:
            return
        palette = design.tone(tone)
        self.text.value = label
        self.text.color = palette.fg
        self.box.bgcolor = palette.bg
        self.box.border = ft.Border.all(1, palette.line)
        self.box.tooltip = tooltip
        self.box.visible = True
        self._state = state

    def hide(self) -> None:
        if self._state is None:
            return
        self.box.visible = False
        self._state = None


@dataclass(slots=True)
class _Tile:
    """一列的控制項與目前顯示的內容（用來略過沒變的屬性）。"""

    box: ft.Container
    dot: ft.Container
    title: ft.Text
    zh: ft.Text
    key_text: ft.Text
    source: _Chip
    review: _Chip
    smart: _Chip
    warning: ft.Icon
    entry_id: int | None = None
    selected: bool = False
    shown: tuple | None = None


class EntryTilePool:
    """固定列數的條目清單；``set_rows`` 原地更新，``set_selected`` 只改選取底色。"""

    def __init__(
        self,
        size: int,
        on_select: Callable[[int], None],
        *,
        on_scroll=None,
        scroll_interval: int = 100,
    ) -> None:
        self._on_select = on_select
        self._tiles = [self._build_tile(index) for index in range(size)]
        self._by_id: dict[int, _Tile] = {}
        self._showing_empty = False
        self.list_view = isolate(
            ft.ListView(
                [tile.box for tile in self._tiles],
                spacing=0,
                expand=True,
                on_scroll=on_scroll,
                scroll_interval=scroll_interval,
            )
        )

    # ------------------------------------------------------------------ 建構
    def _build_tile(self, index: int) -> _Tile:
        dot = ft.Container(
            width=8, height=8, border_radius=4, margin=ft.Margin.only(top=5)
        )
        title = ft.Text("", size=13, weight=ft.FontWeight.W_500, color=C.TEXT)
        zh = ft.Text("", size=12, color=C.MUTED, expand=True)
        key_text = kit.mono_text("", size=10.5, color=C.DIM)
        source, review, smart = _Chip(), _Chip(), _Chip()
        warning = ft.Icon(ft.Icons.WARNING_AMBER, size=15, color=C.GOLD, visible=False)
        body = ft.Column(
            [
                title,
                ft.Row(
                    [zh, source.box, review.box, smart.box, warning],
                    spacing=6,
                ),
                key_text,
            ],
            spacing=1,
            tight=True,
            expand=True,
        )
        box = ft.Container(
            key=f"entry-slot-{index}",
            on_click=self._clicked,
            ink=True,
            padding=ft.Padding.symmetric(horizontal=14, vertical=9),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
            content=ft.Row(
                [dot, body],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            visible=False,
        )
        return _Tile(box, dot, title, zh, key_text, source, review, smart, warning)

    def _clicked(self, event) -> None:
        entry_id = getattr(event.control, "data", None)
        if entry_id is not None:
            self._on_select(entry_id)

    # ------------------------------------------------------------------ 內容
    def set_rows(
        self,
        rows,
        *,
        selected_id: int | None,
        marks: dict | None,
        catalog,
        empty: ft.Control | None = None,
    ) -> None:
        """把 ``rows`` 填進前 N 列、隱藏其餘；沒有資料時顯示 ``empty``。"""
        marks = marks or {}
        self._by_id = {}
        if not rows and empty is not None:
            self.list_view.controls = [empty]
            self._showing_empty = True
            return
        if self._showing_empty:
            self.list_view.controls = [tile.box for tile in self._tiles]
            self._showing_empty = False
        for tile, row in zip(self._tiles, rows, strict=False):
            self._fill(tile, row, selected_id, marks.get(row.id), catalog)
            self._by_id[row.id] = tile
        for tile in self._tiles[len(rows) :]:
            if tile.box.visible:
                tile.box.visible = False
                tile.entry_id = None
                tile.shown = None

    def _fill(self, tile: _Tile, row, selected_id, mark, catalog) -> None:
        tile.box.visible = True
        if tile.entry_id != row.id:
            tile.box.data = row.id
            tile.entry_id = row.id
        selected = row.id == selected_id
        self._paint_selected(tile, selected)
        shown = (
            row.en_us,
            row.zh_tw,
            row.key,
            row.state,
            row.source,
            row.review_status,
            row.quality_state,
            tuple(row.quality_issues),
            row.whitespace_note,
            getattr(mark, "status", None),
            getattr(mark, "run_id", None),
            getattr(mark, "validation_error", None),
        )
        if tile.shown == shown:
            return
        tile.shown = shown
        tile.dot.bgcolor = design.tone(STATE_TONES[row.state]).fg
        tile.title.value = shorten(row.en_us, 48) if row.en_us else "（原文未知）"
        tile.title.color = C.TEXT if row.en_us else C.DIM
        tile.zh.value = shorten(row.zh_tw, 48) if row.zh_tw else "（未翻譯）"
        tile.key_text.value = shorten(row.key, 46)
        self._fill_chips(tile, row, mark, catalog)

    @staticmethod
    def _fill_chips(tile: _Tile, row, mark, catalog) -> None:
        if row.source is None:
            tile.source.hide()
            tile.review.hide()
        else:
            tile.source.show(source_label(row.source, catalog), source_tone(row.source))
            badge = REVIEW_BADGE_LABELS.get(row.review_status or "")
            if badge:
                tile.review.show(
                    badge, "em" if row.review_status == "reviewed" else "gold"
                )
            else:
                tile.review.hide()
        style = MARK_STYLES.get(mark.status) if mark is not None else None
        if mark is None or style is None:
            tile.smart.hide()
        else:
            label, tone = style
            tooltip = (
                f"最近一次智慧校對批次 {mark.run_id[:8]}：{label.split('：', 1)[-1]}"
            )
            if mark.validation_error:
                tooltip += f"（{mark.validation_error}）"
            tile.smart.show(label, tone, tooltip)
        if row.quality_state in _QUALITY_WARNING_STATES:
            tile.warning.tooltip = (
                "；".join(
                    quality_issue_summary(row.quality_issues, row.whitespace_note)
                )
                or "原文或譯文尚未完整，無法檢查特殊字元"
            )
            tile.warning.visible = True
        else:
            tile.warning.visible = False

    # ------------------------------------------------------------------ 選取
    @staticmethod
    def _paint_selected(tile: _Tile, selected: bool) -> None:
        if tile.selected == selected:
            return
        tile.box.bgcolor = C.EM_BG if selected else None
        tile.selected = selected

    def set_selected(self, selected_id: int | None) -> list[ft.Control]:
        """只重畫選取狀態改變的列，回傳這些列（呼叫端逐列 update）。"""
        changed: list[ft.Control] = []
        for tile in self._tiles:
            wanted = tile.entry_id is not None and tile.entry_id == selected_id
            if wanted != tile.selected:
                self._paint_selected(tile, wanted)
                changed.append(tile.box)
        return changed

    def tile_for(self, entry_id: int | None) -> ft.Control | None:
        tile = self._by_id.get(entry_id) if entry_id is not None else None
        return tile.box if tile else None
