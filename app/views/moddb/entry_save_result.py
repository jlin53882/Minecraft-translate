"""Mod DB 編輯器儲存結果的畫面協調。

儲存本身在背景執行緒完成（見 ``entry_writes.save_entry``），來源優先序資訊也在背景算好；
這裡只在畫面執行緒套用結果。沒有預先算好的資訊時（同步呼叫）才就地補算。
"""

from __future__ import annotations

import sqlite3

from app.services_impl.moddb_format_diff_service import analyze_format_diagnostics
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb import entry_writes
from translation_tool.utils.log_unit import log_exception, log_info, log_warning


def apply_save_result(
    panel,
    db,
    entry,
    text: str,
    done,
    error,
    flagged_batch: int,
    *,
    source_info: tuple[str | None, list[str]] | None = None,
) -> None:
    """套用儲存結果，並讓面板只保留事件接線責任。

    ``error`` 可以是例外，或 ``(kind, 例外)``；``source_info`` 是背景已算好的
    ``(生效來源提示, 被來源優先序擋下的版本)``，省略時就地查詢。
    """
    panel._save_running = False
    if error is not None:
        _handle_save_error(panel, entry, error)
        return
    if done is None:
        return

    if source_info is None:
        source_info = (
            panel._shadowing_source(db, entry.id, text),
            panel._shadowed_versions(db, done, entry.id, text),
        )
    shadow, held_back = source_info
    message = _build_success_message(entry, done, shadow, held_back)
    _show_success_snack(panel, message, shadow, held_back)
    if panel.db() is db:
        _apply_current_panel_result(panel, entry, text, message, shadow, flagged_batch)
    panel._update_impact()
    panel._flush_editor()


def _handle_save_error(panel, entry, error) -> None:
    exc = error[1] if isinstance(error, tuple) else error
    where = f"{entry.mc_version} {entry.mod_id} {entry.key}"
    kind = error[0] if isinstance(error, tuple) else _error_kind(exc)
    if kind == "validation":
        log_warning(f"Mod 資料庫儲存被拒絕：{where}（{exc!r}）")
        show_snack(panel._page, str(exc), C.RED)
    elif kind == "database":
        log_exception(f"Mod 資料庫儲存失敗：{where}")
        show_snack(
            panel._page,
            f"儲存失敗（資料庫錯誤：{exc}）。可能是資料庫被其他程式鎖住，請稍後再試；詳情見後台 log",
            C.RED,
        )
    else:
        log_exception(f"Mod 資料庫手動儲存發生未預期錯誤：{where}")
        show_snack(panel._page, f"儲存失敗：{exc}；詳情見後台 log", C.RED)
    if panel.selected is not None and panel.selected.id == entry.id:
        panel.saved_text.value = ""
    panel._update_impact()
    panel._flush_editor()


def _error_kind(exc: BaseException) -> str:
    if isinstance(exc, ValueError):
        return "validation"
    if isinstance(exc, sqlite3.Error):
        return "database"
    return "other"


def _build_success_message(entry, done, shadow, held_back) -> str:
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
    if shadow:
        # 自訂的來源優先序把別的來源排在人工之前：譯文已存進人工來源，但畫面仍是別的
        message += f"；但目前來源優先序讓「{shadow}」的譯文仍然生效"
    if held_back:
        message += (
            f"；{'、'.join(held_back)} 因來源優先序仍顯示原本的譯文，"
            "同步並未在這些版本生效"
        )
    return message


def _show_success_snack(panel, message, shadow, held_back) -> None:
    if shadow or held_back:
        show_snack(panel._page, message, C.GOLD)
    else:
        show_snack(panel._page, message, C.EM, text_color=C.ON_EM)


def _apply_current_panel_result(
    panel, entry, text: str, message: str, shadow, flagged_batch: int
) -> None:
    if (
        not shadow
        and not analyze_format_diagnostics(
            entry.en_us, text, kind=entry.kind
        ).has_issues
    ):
        # 新譯文沒生效、或仍與原文的特殊字元不一致時，問題還在，不能移出待處理清單
        panel._resolve_flagged(entry.id, flagged_batch)
    same_entry = panel.selected is not None and panel.selected.id == entry.id
    if same_entry:
        # 儲存期間可能又輸入了新內容；只有仍停在已儲存文字時才清掉草稿。
        panel._editor_drafts.remember(panel.selected, panel.tw_field.value)
    if same_entry and (panel.tw_field.value or "") == text:
        panel._editor_drafts.discard(entry.id)
        entry_writes.after_data_change(panel, message)
        return
    if same_entry:
        panel.saved_text.value = f"{message}；目前輸入的變更尚未儲存。"
    panel._notify_changed(keep_scroll=True)
