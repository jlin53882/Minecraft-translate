"""ModDB 條目頁的篩選控制項與背景載入流程。"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import EntryFilter
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.advanced_filters import AdvancedFilters
from app.views.moddb.batch_replace_dialog import BatchReplaceDialog
from app.views.moddb.bulk_review_dialog import BulkReviewDialog
from app.views.moddb.editor_drafts import reset_editor_for_database_change
from app.views.moddb.formatting import STATE_LABELS, kind_label
from app.views.moddb.smart_review_dialog import SmartReviewDialog
from app.views.moddb.smart_review_marks import (
    ALL_SMART_REVIEW,
    SMART_REVIEW_FILTER_OPTIONS,
)
from app.views.moddb.source_filter import ALL_SOURCES, ReviewStatusFilter, SourceFilter
from app.views.moddb.species_dialog import SpeciesCandidateDialog

ALL_REVIEW_STATES = "__all__"
ALL_MODS = "全部模組"
ALL_KINDS = "全部類型"
SEARCH_FIELD_OPTIONS = (
    ("all", "全部內容"),
    ("source", "原文"),
    ("translation", "譯文"),
    ("key", "鍵值"),
)


def build_filter_controls(panel) -> None:
    _build_controls(panel)
    _build_flagged_banner(panel)
    _build_filter_card(panel)


def apply_filter_options(panel, snapshot: dict) -> None:
    """套用背景載入的篩選選項，並保持目前選取值有效。"""
    identity = snapshot.get("identity")
    panel._render_source_catalog = snapshot.get("source_catalog")
    if "identity" in snapshot:
        sync_identity = getattr(panel, "_sync_database_identity", None)
        if callable(sync_identity):
            sync_identity(identity)
        elif identity != panel._db_identity:
            reset_editor_for_database_change(panel)
            panel.source_filter.reset()
            panel._db_identity = identity
    panel.source_filter.refresh(
        snapshot.get("effective_source_codes", ()),
        catalog=snapshot.get("source_catalog"),
        available_only=bool(snapshot.get("filter_options_are_filtered")),
    )
    if "source" in snapshot:
        source = snapshot.get("source")
        source_value = ALL_SOURCES if source is None else str(source)
        available_values = {
            option.key for option in panel.source_filter.dropdown.options
        }
        panel.source_filter.dropdown.value = (
            source_value if source_value in available_values else ALL_SOURCES
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


def refresh_after_database_change(panel) -> None:
    """資料庫身分改變後，以清除過舊 scope 的目前狀態重新查詢。"""
    if panel._on_filter_changed is None:
        panel.refresh(keep_selection=False)
        return
    refresh_entry_filter(
        panel,
        page=1,
        keep_selection=False,
        full_refresh=True,
    )


def prepare_entries_snapshot(panel, snapshot: dict) -> tuple[dict, bool]:
    """同步快照的資料庫身分，必要時丟棄舊 scope 並重新查詢。"""
    if "identity" not in snapshot:
        return snapshot, False
    previous_identity = panel._db_identity
    had_database_scope = bool(
        panel.source_filter.code is not None
        or panel.entry_ids is not None
        or panel.drafts
    )
    snapshot_identity = snapshot.get("identity")
    identity_changed = panel._sync_database_identity(snapshot_identity)
    if (
        identity_changed
        and snapshot_identity is not None
        and (previous_identity is not None or had_database_scope)
    ):
        # 舊資料庫的 source／entry_ids 可能已進入背景查詢，不能只重設下拉選單。
        refresh_after_database_change(panel)
        return snapshot, True
    if identity_changed:
        # 初次接上資料庫時也不接受快照重新帶回舊資料庫的來源選取。
        snapshot = {**snapshot, "source": None}
    return snapshot, False


def _build_controls(panel) -> None:
    """建立目前面板的操作控制項。"""
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
    panel.smart_review_filter = kit.dropdown(
        label="智慧校對（最近批次）",
        dense=True,
        width=210,
        value=ALL_SMART_REVIEW,
        on_select=lambda _e: panel._on_smart_review_filter(),
    )
    kit.set_dropdown_options(
        panel.smart_review_filter, list(SMART_REVIEW_FILTER_OPTIONS)
    )
    panel.search = kit.text_field(
        "搜尋",
        hint="原文、譯文或鍵值（輸入後自動搜尋）",
        expand=True,
        on_change=panel._on_search_input,
        on_submit=panel._on_search,
    )
    panel.search_field_dd = kit.dropdown(
        label="搜尋內容",
        dense=True,
        width=140,
        on_select=panel._on_search_field,
    )
    kit.set_dropdown_options(panel.search_field_dd, list(SEARCH_FIELD_OPTIONS))
    panel.search_field_dd.value = "all"
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
    panel.bulk_review_btn = kit.button(
        "批量審核",
        "secondary",
        icon=ft.Icons.VERIFIED_OUTLINED,
        size="sm",
        on_click=panel._open_bulk_review,
    )
    panel.smart_review_btn = kit.button(
        "智慧校對",
        "secondary",
        icon=ft.Icons.AUTO_AWESOME,
        size="sm",
        on_click=lambda event: open_smart_review(panel, event),
        tooltip="以本地 AI 產生未審核譯文的人工校對候選",
    )
    panel.species_scan_btn = kit.button(
        "掃描學名",
        "secondary",
        icon=ft.Icons.SCIENCE_OUTLINED,
        size="sm",
        on_click=lambda event: open_species_scan(panel, event),
        tooltip="掃描目前版本／模組／類型範圍中的英文原文，找出可能的學名",
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
    """建立 ModDB 篩選條件卡片並放入相關控制項。"""
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
                        panel.search_field_dd,
                        panel.search,
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [panel.state_seg, panel.review_filter, panel.smart_review_filter],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                ft.Row(
                    [
                        panel.advanced_filters.toggle_button,
                        panel.advanced_filters.active_text,
                        panel.advanced_filters.clear_button,
                        panel.batch_replace_btn,
                        panel.bulk_review_btn,
                        panel.smart_review_btn,
                        panel.species_scan_btn,
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
    smart = panel.smart_review_filter.value
    return EntryFilter(
        version=panel.version or "",
        mod_id=panel.mod_id,
        kind=panel.kind,
        state=panel.state,
        query=panel.query,
        query_field=panel.query_field,
        source=panel.source_filter.code,
        review_status=(
            None if status in (None, "", ALL_REVIEW_STATES) else str(status)
        ),
        entry_ids=tuple(panel.entry_ids) if panel.entry_ids is not None else None,
        smart_review=(None if smart in (None, "", ALL_SMART_REVIEW) else str(smart)),
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
    """重新載入指定結果頁，必要時把篩選變更交給上層處理。"""
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


def open_bulk_review(panel, _e=None) -> None:
    """開啟批量審核，預填目前頁面支援的範圍但不自動產生預覽。"""
    if panel.drafts:
        show_snack(
            panel._page,
            "目前清單含尚未寫入的機翻草稿；請先離開草稿檢視再執行批量審核。",
            C.GOLD,
        )
        return
    if panel.db() is None or not panel.version:
        show_snack(panel._page, "目前沒有可用的資料庫版本。", C.GOLD)
        return
    BulkReviewDialog(
        panel._page,
        panel.db,
        panel.version,
        panel._after_batch_replace,
        mod_id=panel.mod_id,
        effective_source=panel.source_filter.code,
    ).open()


def open_species_scan(panel, _e=None) -> None:
    """開啟學名候選掃描對話框，並預填目前版本、模組與來源範圍。"""
    if panel.db() is None or not panel.version:
        show_snack(panel._page, "目前沒有可用的資料庫版本。", C.GOLD)
        return
    SpeciesCandidateDialog(
        panel._page,
        panel.db,
        panel.version,
        panel.mod_id,
        panel.kind,
        on_open_entry=panel.open_species_entry,
        effective_source=panel.source_filter.code,
    ).open()


def open_smart_review(panel, _e=None) -> None:
    """開啟智慧校對對話框並傳入目前資料庫與篩選範圍。"""
    if panel.drafts:
        show_snack(
            panel._page,
            "目前清單含尚未寫入的機翻草稿；請先離開草稿檢視再執行智慧校對。",
            C.GOLD,
        )
        return
    if panel.db() is None or not panel.version:
        show_snack(panel._page, "目前沒有可用的資料庫版本。", C.GOLD)
        return

    def after_complete(_result=None) -> None:
        """智慧校對套用完成後重載目前條目頁，再通知頁籤外層。"""
        refresh_entry_filter(panel, keep_selection=True)
        if panel._on_changed:
            panel._on_changed()

    SmartReviewDialog(
        panel._page,
        panel.db,
        panel.version,
        after_complete,
        panel.mod_id,
        panel.kind,
        panel.source_filter.code,
        panel.query,
    ).open()
