"""app/views/moddb/suggestions.py：條目校對右側的「同鍵值・其他版本／相同原文」建議清單。"""

from __future__ import annotations

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.views.moddb.formatting import shorten, source_label
from translation_tool.translation_db.source_catalog import (
    DEFAULT_SOURCE_CATALOG,
    SourceCatalog,
)


def build_suggestions(
    detail,
    tab: str,
    on_apply,
    *,
    catalog: SourceCatalog = DEFAULT_SOURCE_CATALOG,
) -> list[ft.Control]:
    """「同鍵值・其他版本／相同原文」建議清單；``on_apply(text)`` 在按「套用」時呼叫。"""
    entry = detail.entry
    controls: list[ft.Control] = []
    if tab == "key":
        rows = detail.same_key
        for r in rows:
            controls.append(
                _sug_item(
                    r.zh_tw,
                    [
                        r.mc_version,
                        source_label(r.source, catalog) if r.zh_tw else "",
                        _source_tag(r),
                    ],
                    apply=r.zh_tw if (r.zh_tw and r.zh_tw != entry.zh_tw) else None,
                    same=r.zh_tw == entry.zh_tw,
                    on_apply=on_apply,
                    original=_original_line(r),
                )
            )
        hint = "可按「套用」把該版本的譯文帶入輸入框（不會自動儲存）。原文相同的版本，手動儲存時會一併被取代；標示「原文不同」或「原文未知」者不會被動到（沒有原文就無法確認是同一句），套用前請先對照上方顯示的原文。"
    else:
        groups: dict[str, list] = {}
        for r in detail.same_text:
            groups.setdefault(r.zh_tw, []).append(r)
        rows = detail.same_text
        for tw, items in groups.items():
            tags = [
                f"{x.mod_id}・{x.key.split('.')[-1]}・{x.mc_version}" for x in items[:6]
            ]
            if len(items) > 6:
                tags.append(f"…共 {len(items)} 筆")
            controls.append(
                _sug_item(
                    tw,
                    tags,
                    apply=tw if (tw and tw != entry.zh_tw) else None,
                    same=tw == entry.zh_tw,
                    count=len(items),
                    on_apply=on_apply,
                )
            )
        hint = f"原文「{shorten(entry.en_us, 24)}」在其他鍵值／模組的譯法，用來檢查用詞一致性；只供參考。"
    if not rows:
        controls.append(kit.hint_text("沒有符合的資料"))
    else:
        controls.append(kit.hint_text(hint))
    return controls


def _source_tag(r) -> str:
    """其他版本原文與目前條目的關係標籤：原文相同不標、沒有原文標「原文未知」、有但不同標「原文不同」。"""
    if r.same_text:
        return ""
    return "原文不同" if r.en_us else "原文未知"


def _original_line(r) -> str:
    """其他版本條目的原文（讓使用者直接對照）；ZIP 匯入的條目沒有原文時標明未知。"""
    return (
        f"原文：{shorten(r.en_us, 90)}"
        if r.en_us
        else "原文：（未知，例如由翻譯 ZIP 匯入）"
    )


def _sug_item(
    text: str,
    tags: list[str],
    *,
    apply: str | None,
    same: bool,
    on_apply,
    count: int = 0,
    original: str = "",
) -> ft.Control:
    title = text if text else "（未翻譯）"
    head = ft.Text(
        title + (f"  × {count}" if count else ""),
        size=14,
        color=C.MUTED if same or not text else C.TEXT,
        selectable=True,
        expand=True,
    )
    row: list[ft.Control] = [head]
    if apply:
        row.append(
            kit.button(
                "套用",
                "secondary",
                size="sm",
                on_click=lambda _e, t=apply: on_apply(t),
            )
        )
    return ft.Container(
        padding=ft.Padding.symmetric(vertical=8),
        border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
        content=ft.Column(
            [
                ft.Row(row, vertical_alignment=ft.CrossAxisAlignment.START),
                *(
                    [ft.Text(original, size=12, color=C.DIM, selectable=True)]
                    if original
                    else []
                ),
                ft.Row(
                    [kit.chip(t, "neutral") for t in tags if t]
                    + ([kit.chip("與目前相同", "em")] if same and text else []),
                    spacing=6,
                    wrap=True,
                ),
            ],
            spacing=4,
        ),
    )
