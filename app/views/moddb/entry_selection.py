"""條目校對的「選取一筆」：清單立刻標示，詳情在背景載入，過期結果丟棄。

點選清單列時，畫面執行緒只做三件事：標示被點的列、暫時停用編輯區、把讀取工作丟到背景；
資料庫讀取（詳情、影響預覽）不在畫面執行緒。使用者很快又點了另一列（或清單被新快照取代）時，
``panel._select_ticket`` 已經前進，較舊的結果到達後直接丟棄，不會蓋掉新的選取。
沒有背景排程的頁面（單元測試）同步完成。
"""

from __future__ import annotations

from app.services_impl.moddb_service import EntryRow
from app.tasks.operation_registry import (
    CancellationPolicy,
    OperationPresentation,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui.design import C
from app.ui.page_post import has_background_scheduler, post_to_ui
from app.ui.snack import show_snack
from app.ui.update_scope import update_regions
from app.views.moddb.review_scope_dialog import compute_impact
from translation_tool.utils.log_unit import log_debug, log_exception


def select_entry(panel, entry_id: int | None) -> None:
    """選取一筆條目；背景可用時詳情與影響預覽在背景讀取。"""
    row = next((r for r in panel.rows if r.id == entry_id), None)
    db = panel.db()
    if not has_background_scheduler(panel._page) or db is None or row is None:
        select_now(panel, entry_id)
        render_selection_change(panel)
        return
    panel._sync_database_identity(panel._database_identity(db))
    panel._editor_drafts.remember(panel.selected, panel.tw_field.value)
    panel._select_ticket += 1
    ticket = panel._select_ticket
    panel._loading_id = entry_id
    panel.editor_card.disabled = True
    update_regions(*panel.tiles.set_selected(entry_id), panel.editor_card)
    text = text_to_show(panel, row)
    propagate = bool(panel.sync_row.value)

    def load() -> None:
        detail = impact = error = None
        try:
            detail = db.entry_detail(entry_id)
            if detail is not None:
                impact = compute_impact(db, detail.entry, text, propagate)
        except Exception as exc:  # noqa: BLE001 - worker 邊界：回報錯誤並恢復編輯區
            error = exc
            log_exception(f"Mod 資料庫讀取條目失敗：id={entry_id}")
        post_to_ui(
            panel._page,
            apply_selection,
            panel,
            ticket,
            detail,
            impact,
            error,
            label="Mod 資料庫條目詳情",
            on_failure=lambda: setattr(panel, "_loading_id", None),
        )

    try:
        launched = launch_page_operation(
            panel._page,
            load,
            name="Mod 資料庫讀取條目",
            owner="moddb-entry-detail",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
            presentation=OperationPresentation.MAINTENANCE,
        )
    except Exception as exc:  # noqa: BLE001 - 無法啟動時恢復編輯區
        log_debug(f"Mod 資料庫條目載入無法啟動：{exc!r}")
        launched = False
    if launched is False:
        panel._loading_id = None
        panel.editor_card.disabled = False
        update_regions(panel.editor_card)


def text_to_show(panel, row: EntryRow) -> str:
    """選取 ``row`` 後編輯框會顯示的內容（草稿 > 機翻預填 > 生效譯文）。"""
    draft = panel._editor_drafts.get(row.id)
    if draft is not None:
        return draft if draft.strip() else ""
    machine = panel.drafts.get(row.id) if not row.zh_tw else None
    text = machine if machine is not None else row.zh_tw
    return text if text.strip() else ""


def apply_selection(panel, ticket, detail, impact, error) -> None:
    """背景讀完詳情後在畫面執行緒套用；過期的結果直接丟棄。"""
    if ticket != panel._select_ticket:
        return  # 使用者已選了別筆，或清單已被新的快照取代
    panel._loading_id = None
    panel.editor_card.disabled = False
    if error is not None:
        show_snack(panel._page, f"讀取條目失敗：{error}；詳情見後台 log", C.RED)
        flush_after_selection(
            panel,
            panel.tiles.set_selected(panel.selected.id if panel.selected else None),
        )
        return
    panel.detail = detail
    panel.selected = detail.entry if detail else None
    panel._show_editor(detail, calculate_impact=False, impact=impact)
    render_selection_change(panel)


def select_now(panel, entry_id: int | None) -> None:
    """同步選取（沒有背景排程的頁面，以及 ``_load_list`` 內部）。"""
    db = panel.db()
    panel._sync_database_identity(
        panel._database_identity(db) if db is not None else None
    )
    panel._editor_drafts.remember(panel.selected, panel.tw_field.value)
    panel._select_ticket += 1
    panel._loading_id = None
    detail = db.entry_detail(entry_id) if (db and entry_id) else None
    panel.detail = detail
    panel.selected = detail.entry if detail else None
    panel._show_editor(detail)


def render_selection_change(panel) -> None:
    """選取改變後：更新清單標示並把編輯區與記錄送到畫面。"""
    flush_after_selection(
        panel, panel.tiles.set_selected(panel.selected.id if panel.selected else None)
    )


def flush_after_selection(panel, changed_tiles) -> None:
    update_regions(*changed_tiles, panel.editor_card, panel.history_card)
