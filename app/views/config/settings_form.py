"""由 ``settings_schema`` 產生設定頁的控制項與版面（#134）。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import flet as ft

from app.config_apply import timing_note
from app.ui import kit
from app.ui.design import C
from app.views.config.settings_schema import (
    NAV_PAGES,
    SETTINGS_BY_PATH,
    Card,
    Cols,
    Custom,
    Div,
    F,
    Gap,
    Head,
    Labeled,
    Note,
    R,
    Setting,
    Side,
    editable_settings,
    resolved_layout,
)


def helper_text(setting: Setting) -> str | None:
    """說明文字：套用時機（若有登記）＋ 設定本身的說明。"""
    parts = [timing_note(setting.path), setting.help]
    text = " ".join(p for p in parts if p)
    return text or None


_PATH_WORDS = ("path", "dir", "directory", "folder")


def _is_path_setting(setting_path: str) -> bool:
    """設定鍵的最後一段含 path／dir／directory／folder 就視為路徑欄位。"""
    leaf = setting_path.rsplit(".", 1)[-1].lower()
    return any(word in leaf for word in _PATH_WORDS)


def make_control(setting: Setting) -> ft.Control:
    """依 ``Setting.kind`` 建立對應的 Flet 控制項。"""
    helper = helper_text(setting)
    kind = setting.kind
    if kind == "bool":
        kwargs: dict[str, Any] = {"label": setting.label, "value": False}
        if setting.help:
            kwargs["tooltip"] = setting.help
        return ft.Checkbox(**kwargs)
    if kind == "choice":
        return kit.dropdown(
            label=setting.label,
            options=[ft.dropdown.Option(c) for c in setting.choices],
            dense=True,
            helper_text=helper,
        )
    if kind in ("text", "lines"):
        return kit.field(
            label=setting.label,
            multiline=True,
            expand=True,
            text_size=13,
            helper=helper,
            helper_max_lines=3,  # 說明含「套用時機」，單行會被截成「…」
        )
    if kind in ("int", "float"):
        return kit.field(
            label=setting.label,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            helper=helper,
            helper_max_lines=3,
        )
    return kit.field(
        label=setting.label,
        dense=True,
        helper=helper,
        helper_max_lines=3,
        # 路徑／資料夾類設定：貼上帶引號的路徑（檔案總管「複製為路徑」）自動去引號
        path_input=_is_path_setting(setting.path),
    )


def build_controls(controls_map: dict[str, Any]) -> None:
    """為所有一般設定建立控制項（專用元件 keys / models 由 ConfigView 另外處理）。"""
    for setting in editable_settings():
        controls_map[setting.path] = make_control(setting)


def _weighted(controls_map: dict[str, Any], path: str) -> ft.Column:
    return ft.Column([controls_map[path]], expand=SETTINGS_BY_PATH[path].weight)


def _render_block(
    block: Any,
    controls_map: dict[str, Any],
    custom: dict[str, Callable[[], ft.Control]],
) -> ft.Control:
    if isinstance(block, F):
        return controls_map[block.path]
    if isinstance(block, R):
        columns = [_weighted(controls_map, p) for p in block.paths]
        if block.pad:
            columns.append(ft.Column([], expand=block.pad))
        return ft.Row(columns)
    if isinstance(block, Side):
        items: list[ft.Control] = []
        for i, path in enumerate(block.paths):
            if i:
                items.append(ft.VerticalDivider(width=1))
            items.append(ft.Column([controls_map[path]], expand=1))
        return ft.Container(
            height=block.height, content=ft.Row(items, spacing=block.spacing)
        )
    if isinstance(block, Note):
        return ft.Text(block.text, size=12, color=C.DIM)
    if isinstance(block, Head):
        return ft.Text(block.text, weight=ft.FontWeight.W_600, size=14)
    if isinstance(block, Gap):
        return ft.Container(height=block.height)
    if isinstance(block, Div):
        return ft.Divider()
    if isinstance(block, Cols):
        return ft.Row(
            [_render_labeled(item, controls_map) for item in block.items],
            spacing=block.spacing,
        )
    if isinstance(block, Custom):
        return custom[block.name]()
    raise TypeError(f"未知的版面區塊：{block!r}")


def _render_labeled(item: Labeled, controls_map: dict[str, Any]) -> ft.Column:
    controls: list[ft.Control] = [
        ft.Text(item.title, weight=ft.FontWeight.W_500, size=13),
        controls_map[item.path],
    ]
    if item.note:
        controls.append(ft.Text(item.note, size=11, color=C.MUTED))
    return ft.Column(controls, expand=1)


def build_pages(
    controls_map: dict[str, Any],
    build_card: Callable[[str, Iterable[ft.Control]], ft.Control],
    custom: dict[str, Callable[[], ft.Control]],
) -> dict[str, ft.Column]:
    """依 NAV_PAGES 與 resolved_layout() 產生每一頁的內容。"""
    layout = resolved_layout()
    pages: dict[str, ft.Column] = {}
    for nav in NAV_PAGES:
        cards: list[ft.Control] = []
        for card in layout.get(nav["id"], ()):
            blocks = [_render_block(b, controls_map, custom) for b in card.blocks]
            cards.append(build_card(card.title, blocks))
        pages[nav["id"]] = ft.Column(spacing=15, controls=cards)
    return pages


def card_titles(layout: dict[str, tuple[Card, ...]] | None = None) -> list[str]:
    layout = layout or resolved_layout()
    return [card.title for cards in layout.values() for card in cards]
