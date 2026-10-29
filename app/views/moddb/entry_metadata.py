"""Provenance details shown beneath the selected Mod DB translation."""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import EntryDetail, format_taipei_time
from app.ui import kit
from app.ui.design import C
from app.views.moddb.formatting import kind_label


def build_entry_metadata(detail: EntryDetail) -> list[ft.Control]:
    entry = detail.entry
    return [
        _key_row(entry.key),
        _mod_row(entry.mod_id, entry.kind),
        _version_row(entry.mc_version, detail.versions),
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


def _version_row(current: str, versions: list[str]) -> ft.Control:
    return ft.Row(
        [
            kit.section_label("出現於"),
            *(
                kit.chip(version, "em" if version == current else "neutral")
                for version in versions
            ),
        ],
        spacing=6,
        wrap=True,
    )


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
