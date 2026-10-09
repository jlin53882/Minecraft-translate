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


def _source_change(new_en: str) -> ft.Control:
    return ft.Column(
        [
            kit.chip("掃描到原文已變動（尚未採用）", "gold"),
            kit.mono_text(f"新原文：{new_en}", size=12),
        ],
        spacing=4,
    )
