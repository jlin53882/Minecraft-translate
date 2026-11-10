"""Provenance details shown beneath the selected Mod DB translation."""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import EntryDetail, format_taipei_time, pack_label
from app.ui import kit
from app.ui.design import C
from app.views.moddb.formatting import kind_label


def build_entry_metadata(detail: EntryDetail) -> list[ft.Control]:
    entry = detail.entry
    return [
        _key_row(entry.key),
        _mod_row(entry.mod_id, entry.kind),
        _version_row(entry, detail),
        *(_origin_rows(detail.origins) if detail.origins else []),
        *(_reference_rows(detail.references) if detail.references else []),
        _time_row(entry),
        *(_source_change(change.new_en) for change in detail.src_changes[:1]),
    ]


def _key_row(key: str) -> ft.Control:
    return ft.Row(
        [kit.section_label("鍵值"), kit.mono_text(key, size=12)],
        spacing=10,
        wrap=True,
    )


def _mod_row(mod_id: str, kind: str) -> ft.Control:
    return ft.Row(
        [
            kit.section_label("模組"),
            ft.Text(f"{mod_id}（{kind_label(kind)}）", size=12.5, color=C.TEXT),
        ],
        spacing=10,
    )


MAX_VERSION_CHIPS = 6


def _version_row(entry, detail: EntryDetail) -> ft.Control:
    """遊戲版本標籤；KubeJS／FTB 另標整合包資料夾，且只有目前這一筆高亮。

    同一個遊戲版本可以有多個整合包版本（例如 8.1～8.5 都是 1.21.1），只寫遊戲版本會變成
    好幾顆一樣的標籤，看不出各是哪一個。超過 ``MAX_VERSION_CHIPS`` 顆時，目前這一筆排最前面，
    其餘收合成「另 N 個」，完整清單放在提示文字。
    """
    here = (entry.mc_version, pack_label(entry.kind, entry.mod_id))
    packs = detail.version_packs or [""] * len(detail.versions)
    labels = [
        (version, pack) for version, pack in zip(detail.versions, packs, strict=False)
    ]
    labels.sort(key=lambda label: label != here)  # 穩定排序：目前這一筆排最前面
    shown, hidden = labels[:MAX_VERSION_CHIPS], labels[MAX_VERSION_CHIPS:]
    chips = [
        kit.chip(
            f"{version} · {pack}" if pack else version,
            "em" if (version, pack) == here else "neutral",
        )
        for version, pack in shown
    ]
    if hidden:
        more = kit.chip(f"另 {len(hidden)} 個", "neutral")
        more.tooltip = "、".join(
            f"{version} · {pack}" if pack else version for version, pack in hidden
        )
        chips.append(more)
    return ft.Row([kit.section_label("出現於"), *chips], spacing=6, wrap=True)


def _time_row(entry) -> ft.Control:
    return ft.Row(
        [
            kit.section_label("時間"),
            ft.Text(
                f"條目匯入 {format_taipei_time(entry.created_at)}　"
                f"目前譯文首次匯入 {format_taipei_time(entry.translation_created_at)}　"
                f"最近更新 {format_taipei_time(entry.effective_updated_at)}　"
                f"人工操作 {format_taipei_time(entry.last_manual_at)}",
                size=11,
                color=C.DIM,
                selectable=True,
            ),
        ],
        spacing=8,
        wrap=True,
    )


def _origin_rows(origins) -> list[ft.Control]:
    """顯示可回寫來源；大量引用只展開前幾筆，避免拖慢條目編輯器。"""
    rows = [
        ft.Row(
            [
                kit.section_label("來源"),
                ft.Column(
                    [
                        kit.mono_text(
                            f"{origin.source_path}  ·  {origin.selector}", size=11
                        )
                        for origin in origins[:5]
                    ],
                    spacing=2,
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
    ]
    if len(origins) > 5:
        rows.append(kit.hint_text(f"另有 {len(origins) - 5:,} 個來源引用未展開"))
    return rows


def _reference_rows(references) -> list[ft.Control]:
    """顯示引用此語言定義的 FTB／其他格式來源。"""
    return [
        ft.Row(
            [
                kit.section_label("引用"),
                ft.Column(
                    [
                        kit.mono_text(
                            f"{reference.source_path}  ·  {reference.selector}", size=11
                        )
                        for reference in references[:5]
                    ],
                    spacing=2,
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
    ]


def _source_change(new_en: str) -> ft.Control:
    return ft.Column(
        [
            kit.chip("掃描到原文已變動（尚未採用）", "gold"),
            kit.mono_text(f"新原文：{new_en}", size=12),
        ],
        spacing=4,
    )
