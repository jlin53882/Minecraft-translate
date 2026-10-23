"""Mod DB 編輯器儲存結果的資料一致性輔助函式。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from app.services_impl.moddb_source_service import source_label
from translation_tool.translation_db import ReviewPreviewItem, TranslationDB
from translation_tool.translation_db.schema import SRC_MANUAL


def shadowed_versions(
    db: TranslationDB,
    done: Iterable[ReviewPreviewItem],
    entry_id: int,
    saved_text: str,
) -> list[str]:
    """找出同步後仍因來源優先序而顯示舊譯文的其他版本。"""
    held_back: list[str] = []
    for impact in done:
        if impact.entry_id == entry_id:
            continue
        try:
            current = db.get_entry(impact.entry_id)
        except sqlite3.Error:
            continue
        if current is not None and current.zh_tw != saved_text:
            held_back.append(impact.mc_version)
    return held_back


def shadowing_source(
    db: TranslationDB,
    entry_id: int,
    saved_text: str,
) -> str | None:
    """若儲存後仍由其他來源生效，回傳該來源名稱。"""
    try:
        current = db.get_entry(entry_id)
    except sqlite3.Error:
        return None
    if current is None or current.source in (None, SRC_MANUAL):
        return None
    if current.zh_tw == saved_text:
        return None
    return source_label(current.source, db.source_catalog)
