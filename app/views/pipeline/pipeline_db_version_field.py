"""Mod DB version input for the one-click pipeline merge step."""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import summarize_database
from app.ui.design import C
from app.views.moddb.version_picker import (
    refresh_target_version_options,
    target_version_dropdown,
    target_version_value,
)
from translation_tool.utils.log_unit import log_warning


def build_pipeline_db_version_field(state: dict, show_snack_bar) -> ft.Dropdown:
    """Build the editable version field and keep it synced with wizard state."""

    def on_change(e) -> None:
        state["translation_db_version"] = target_version_value(e.control) or ""

    def on_focus(e) -> None:
        refresh_target_version_options(e.control)
        try:
            info = summarize_database()
        except Exception as exc:  # noqa: BLE001 - manual input remains available
            log_warning(f"讀取 Mod 資料庫狀態失敗：{exc!r}")
            return
        if info is None:
            show_snack_bar(
                "尚未建立 Mod 資料庫，請先到「Mod 資料庫」頁掃描 JAR 建立資料庫。",
                C.GOLD,
            )

    return target_version_dropdown(
        value=state["translation_db_version"],
        label="目標版本",
        hint="選擇版本或手動輸入（留空則使用設定）",
        on_change=on_change,
        on_focus=on_focus,
    )
