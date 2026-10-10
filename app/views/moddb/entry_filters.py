"""Filter controls and resolved entry query shared by list and batch actions."""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import EntryFilter
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.advanced_filters import AdvancedFilters
from app.views.moddb.batch_replace_dialog import BatchReplaceDialog
from app.views.moddb.formatting import STATE_LABELS, kind_label
from app.views.moddb.source_filter import ReviewStatusFilter, SourceFilter

ALL_REVIEW_STATES = "__all__"
ALL_MODS = "全部模組"
ALL_KINDS = "全部類型"


def build_filter_controls(panel) -> None:
    _build_controls(panel)
    _build_flagged_banner(panel)
    _build_filter_card(panel)


def apply_filter_options(panel, snapshot: dict) -> None:
    """Apply worker-loaded filter options and keep their selected values valid."""
    identity = snapshot.get("identity")
    panel._render_source_catalog = snapshot.get("source_catalog")
    if panel._db_identity is not None and identity != panel._db_identity:
        panel.source_filter.reset()
    panel._db_identity = identity
    panel.source_filter.refresh(
        snapshot.get("effective_source_codes", ()),
        catalog=snapshot.get("source_catalog"),
    )
    versions = snapshot.get("versions", [])
    kit.set_dropdown_options(panel.version_dd, [(v, v) for v in versions])
    panel.version = snapshot.get("version")
    panel.version_dd.value = panel.version
    mods = snapshot.get("mods", [])
    kit.set_dropdown_options(
        panel.mod_dd, [(ALL_MODS, ALL_MODS), *((m, m) for m in mods)]
    )
    panel.mod_id = snapshot.get("mod_id")
    panel.mod_dd.value = panel.mod_id or ALL_MODS
    kinds = snapshot.get("kinds", [])
    kit.set_dropdown_options(
        panel.kind_dd,
        [(ALL_KINDS, ALL_KINDS), *((k, kind_label(k)) for k in kinds)],
    )
    panel.kind = snapshot.get("kind")
    panel.kind_dd.value = panel.kind or ALL_KINDS


def _build_controls(panel) -> None:
    panel.version_dd = kit.dropdown(
        label="遊戲版本", dense=True, width=220, on_select=panel._on_version
    )
    panel.mod_dd = kit.dropdown(
        label="模組", dense=True, width=220, on_select=panel._on_mod
    )
    panel.kind_dd = kit.dropdown(
        label="類型", dense=True, width=160, on_select=panel._on_kind
    )
    panel.source_filter = SourceFilter(panel._on_source)
    panel.review_filter = ReviewStatusFilter(panel._on_review_status).dropdown
    panel.search = kit.text_field(
        "搜尋", hint="原文、譯文或鍵值", expand=True, on_submit=panel._on_search
    )
    panel.state_seg = kit.Segmented(
        [(key, label) for key, label in STATE_LABELS.items()],
        "all",
        panel._on_state,
    )
    panel.advanced_filters = AdvancedFilters(panel._on_advanced_change)
    panel.batch_replace_btn = kit.button(
        "批次取代",
        "secondary",
        icon=ft.Icons.FIND_REPLACE,
        size="sm",
        on_click=panel._open_batch_replace,
    )


def _build_flagged_banner(panel) -> None:
    panel.flagged_text = ft.Text("", size=12.5, color=C.TEXT, expand=True)
    panel.flagged_banner = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.WARNING_AMBER, size=16, color=C.GOLD),
                panel.flagged_text,
                kit.button(
                    "清除篩選",
                    "secondary",
                    size="sm",
                    on_click=lambda _e: panel.clear_flagged(),
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=14, vertical=8),
        bgcolor=C.GOLD_BG,
        border_radius=8,
        visible=False,
    )


def _build_filter_card(panel) -> None:
    panel.filter_card = kit.section_card(
        None,
        ft.Column(
            [
                ft.Row(
                    [
                        panel.version_dd,
                        panel.mod_dd,
                        panel.kind_dd,
                        panel.source_filter.dropdown,
                        panel.search,
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [panel.state_seg, panel.review_filter],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                ft.Row(
                    [
                        panel.advanced_filters.toggle_button,
                        panel.advanced_filters.active_text,
                        panel.batch_replace_btn,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                panel.advanced_filters.panel,
            ],
            spacing=10,
        ),
    )


def database_identity(db):
    path = getattr(db, "path", None)
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_dev, stat.st_ino)
    except (AttributeError, OSError):
        return (str(path), id(db))


def entry_filter(panel) -> EntryFilter:
    status = panel.review_filter.value
    return EntryFilter(
        version=panel.version or "",
        mod_id=panel.mod_id,
        kind=panel.kind,
        state=panel.state,
        query=panel.query,
        source=panel.source_filter.code,
        review_status=(
            None if status in (None, "", ALL_REVIEW_STATES) else str(status)
        ),
        entry_ids=tuple(panel.entry_ids) if panel.entry_ids is not None else None,
        time=panel.advanced_filters.time_filter(),
        quality=panel.advanced_filters.quality_filter(),
        sort_by=panel.advanced_filters.sort_by,
    )


def refresh_entry_filter(
    panel,
    *,
    page: int = 1,
    keep_selection: bool = True,
    full_refresh: bool = False,
    background: bool = True,
) -> None:
    """Refresh the requested result page, optionally delegating filter edits."""
    panel.pager.set_state(panel.total, page)
    if panel._on_filter_changed is not None:
        panel._on_filter_changed(
            page, keep_selection, full_refresh, background=background
        )
        return
    panel._load_list(page=page, keep_selection=keep_selection)
    panel._safe_update()


def on_advanced_change(panel) -> None:
    refresh_entry_filter(panel)


def open_batch_replace(panel, _e=None) -> None:
    if panel.drafts:
        show_snack(
            panel._page,
            "目前清單含尚未寫入的機翻草稿；請先離開草稿檢視再執行批次取代。",
            C.GOLD,
        )
        return
    if panel.db() is None or not panel.version:
        show_snack(panel._page, "目前沒有可用的資料庫版本。", C.GOLD)
        return
    try:
        criteria = entry_filter(panel)
    except ValueError as exc:
        show_snack(panel._page, f"無法套用目前篩選：{exc}", C.RED)
        return
    BatchReplaceDialog(
        panel._page,
        panel.db,
        criteria,
        panel._after_batch_replace,
    ).open()
