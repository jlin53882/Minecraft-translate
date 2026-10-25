"""Mod DB 編輯器儲存結果的畫面協調。"""

from __future__ import annotations

from app.services_impl.moddb_format_diff_service import analyze_format_diagnostics
from app.ui.design import C
from app.ui.snack import show_snack
from translation_tool.utils.log_unit import log_exception, log_info, log_warning


def apply_save_result(
    panel,
    db,
    entry,
    text: str,
    done,
    error,
    flagged_batch: int,
) -> None:
    """套用背景儲存結果，並讓面板只保留事件接線責任。"""
    panel._save_running = False
    if error is not None:
        _handle_save_error(panel, entry, error)
        return
    if done is None:
        return

    message, shadow, held_back = _build_success_message(panel, db, entry, text, done)
    _show_success_snack(panel, message, shadow, held_back)
    if panel.db() is db:
        _apply_current_panel_result(
            panel,
            db,
            entry,
            text,
            message,
            shadow,
            done,
            flagged_batch,
        )
    panel._update_impact()
    panel._safe_update()


def _handle_save_error(panel, entry, error) -> None:
    kind, exc = error
    if kind == "validation":
        log_warning(
            f"Mod 資料庫儲存被拒絕：{entry.mc_version} {entry.mod_id} "
            f"{entry.key}（{exc!r}）"
        )
        show_snack(panel._page, str(exc), C.RED)
    elif kind == "database":
        log_exception(
            f"Mod 資料庫儲存失敗：{entry.mc_version} {entry.mod_id} {entry.key}"
        )
        show_snack(
            panel._page,
            f"儲存失敗（資料庫錯誤：{exc}）。可能是資料庫被其他程式鎖住，請稍後再試；詳情見後台 log",
            C.RED,
        )
    else:
        show_snack(panel._page, f"儲存失敗：{exc}；詳情見後台 log", C.RED)
    if panel.selected is not None and panel.selected.id == entry.id:
        panel.saved_text.value = ""
    panel._update_impact()
    panel._safe_update()


def _build_success_message(panel, db, entry, text, done):
    others = [item.mc_version for item in done if not item.is_self]
    log_info(
        f"Mod 資料庫手動儲存：{entry.mc_version} {entry.mod_id} {entry.key}"
        f"（同步 {len(others)} 個版本：{'、'.join(others) or '無'}）"
    )
    message = (
        f"已儲存，並同步 {len(others)} 個版本：{'、'.join(others)}"
        if others
        else "已儲存"
    )
    shadow = panel._shadowing_source(db, entry.id, text)
    held_back = panel._shadowed_versions(db, done, entry.id, text)
    if shadow:
        message += f"；但目前來源優先序讓「{shadow}」的譯文仍然生效"
    if held_back:
        message += (
            f"；{'、'.join(held_back)} 因來源優先序仍顯示原本的譯文，"
            "同步並未在這些版本生效"
        )
    return message, shadow, held_back


def _show_success_snack(panel, message, shadow, held_back) -> None:
    if shadow or held_back:
        show_snack(panel._page, message, C.GOLD)
    else:
        show_snack(panel._page, message, C.EM, text_color=C.ON_EM)


def _apply_current_panel_result(
    panel,
    db,
    entry,
    text: str,
    message: str,
    shadow,
    done,
    flagged_batch: int,
) -> None:
    show_saved_message = False
    selected_index = _selected_row_index(panel)
    if (
        not shadow
        and not analyze_format_diagnostics(
            entry.en_us, text, kind=entry.kind
        ).has_issues
    ):
        panel._resolve_flagged(entry.id, flagged_batch)
    same_entry = panel.selected is not None and panel.selected.id == entry.id
    if same_entry:
        panel._editor_drafts.remember(panel.selected, panel.tw_field.value)
    current_editor_text = (panel.tw_field.value or "") if same_entry else None
    unchanged_editor = same_entry and current_editor_text == text
    if unchanged_editor:
        panel._editor_drafts.discard(entry.id)
        show_saved_message = True
        if panel._on_changed is None:
            panel._load_list(page=panel.pager.current_page, keep_scroll=True)
    elif same_entry:
        panel.saved_text.value = f"{message}；目前輸入的變更尚未儲存。"
    panel._notify_changed(keep_scroll=True)
    _select_next_after_refresh(panel, entry.id, selected_index, show_saved_message)
    if (
        show_saved_message
        and panel.selected is not None
        and panel.selected.id == entry.id
    ):
        panel.saved_text.value = message


def _selected_row_index(panel) -> int | None:
    selected_id = panel.selected.id if panel.selected is not None else None
    if selected_id is None:
        return None
    return next(
        (index for index, row in enumerate(panel.rows) if row.id == selected_id),
        None,
    )


def _select_next_after_refresh(
    panel, entry_id: int, selected_index, show_saved: bool
) -> None:
    if (
        not show_saved
        or panel._on_changed is None
        or selected_index is None
        or not panel.rows
        or any(row.id == entry_id for row in panel.rows)
    ):
        return
    # 父層單一路徑刷新時仍保留「目前條目消失就選下一筆」的操作語意。
    panel.select(panel.rows[min(selected_index, len(panel.rows) - 1)].id)
