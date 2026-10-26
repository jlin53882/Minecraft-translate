"""條目校對的寫入流程：手動儲存、還原，以及寫入後的畫面重整。

資料庫寫入一律在背景執行緒做，結果才排回畫面執行緒套用；畫面執行緒不碰資料庫。
沒有背景排程的頁面（單元測試）則依序同步執行，行為相同。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui.design import C
from app.ui.page_post import has_background_scheduler, post_to_ui
from app.ui.snack import show_snack
from app.ui.update_scope import update_regions
from app.views.moddb.editor_save_state import shadowed_versions, shadowing_source
from translation_tool.utils.log_unit import log_exception, log_info

ACTOR = "使用者"


def run_write(
    page,
    work: Callable[[], Any],
    apply: Callable[[Any, BaseException | None], None],
    *,
    name: str,
    owner: str,
    label: str,
    on_failure: Callable[[], None] | None = None,
) -> bool:
    """在背景執行寫入 ``work()``，把 ``(結果, 例外)`` 排回畫面執行緒交給 ``apply``。

    回傳是否已受理；應用程式正在關閉（或無法啟動）時回傳 False，``apply`` 不會被呼叫。
    沒有背景排程的頁面直接同步執行。例外不會往外丟：由 ``apply`` 依型別顯示錯誤。
    """

    def call() -> tuple[Any, BaseException | None]:
        try:
            return work(), None
        except Exception as exc:  # noqa: BLE001 - worker 邊界：交給 apply 顯示
            return None, exc

    if not has_background_scheduler(page):
        value, error = call()
        apply(value, error)
        return True

    def in_worker() -> None:
        value, error = call()
        post_to_ui(page, apply, value, error, label=label, on_failure=on_failure)

    try:
        launched = launch_page_operation(
            page,
            in_worker,
            name=name,
            owner=owner,
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            commit=CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
            shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
        )
    except Exception:  # noqa: BLE001 - 無法啟動：讓呼叫端恢復畫面
        log_exception(f"{name}無法啟動")
        return False
    return launched is not False


def after_data_change(panel, message: str | None = None) -> bool:
    """寫入完成後重整畫面：維持頁碼、捲動位置與選取位置，並通知上層使其他快照失效。

    有上層（``on_changed``）時，清單只由上層的快照重載一次：有背景排程時交給背景快照
    （``on_changed`` 會觸發並顯示載入中提示），``message`` 會在新快照套用後顯示在編輯區；
    沒有背景排程時上層同步刷新，目前條目若已不在清單中就改選同位置的下一筆。
    沒有上層的面板自己重載目前頁。
    回傳是否已交給背景快照（True 時編輯區會由快照重畫，呼叫端不必再更新）。
    """
    background = has_background_scheduler(panel._page) and panel._on_changed is not None
    if panel._on_changed is None:
        panel.reload_page_in_place()
        _show_message(panel, message)
    elif background:
        panel.prepare_reload_after_change(message)
        panel._on_changed()
    else:
        selected_index = panel._selected_index()
        entry_id = panel.selected.id if panel.selected is not None else None
        panel._notify_changed(keep_scroll=True)
        _select_next_if_gone(panel, entry_id, selected_index)
        _show_message(panel, message)
    if not background:
        panel._safe_update()
    return background


def _show_message(panel, message: str | None) -> None:
    if message and panel.selected is not None:
        panel.saved_text.value = message


def _select_next_if_gone(panel, entry_id, selected_index) -> None:
    if (
        entry_id is None
        or selected_index is None
        or not panel.rows
        or any(row.id == entry_id for row in panel.rows)
    ):
        return
    panel.select(panel.rows[min(selected_index, len(panel.rows) - 1)].id)


# ---------------------------------------------------------------- 手動儲存
def save_entry(panel) -> None:
    entry, db = panel.selected, panel.db()
    text = panel.pending_text()
    if entry is None or db is None or not text or panel._save_running:
        return
    flagged_batch = panel._flagged_batch
    propagate = bool(panel.sync_row.value)
    panel._save_running = True
    panel.save_btn.disabled = True
    panel.saved_text.value = "正在儲存並同步其他版本…" if propagate else "正在儲存…"
    update_regions(panel.save_btn, panel.saved_text)

    def work():
        done = db.save_manual(entry.id, text, actor=ACTOR, propagate=propagate)
        # 來源優先序檢查也要讀資料庫，一併在背景做完
        shadow = shadowing_source(db, entry.id, text)
        held_back = shadowed_versions(db, done, entry.id, text)
        return done, shadow, held_back

    def apply(value, error) -> None:
        done, shadow, held_back = value if value is not None else (None, None, [])
        panel._apply_save_result(
            db,
            entry,
            text,
            done,
            error,
            flagged_batch,
            source_info=(shadow, held_back),
        )

    launched = run_write(
        panel._page,
        work,
        apply,
        name="Mod 資料庫手動儲存",
        owner="moddb-manual-save",
        label="Mod 資料庫儲存結果",
        on_failure=lambda: setattr(panel, "_save_running", False),
    )
    if not launched:
        panel._save_running = False
        panel.saved_text.value = ""
        show_snack(panel._page, "儲存無法啟動，或應用程式正在關閉。", C.GOLD)
        panel._update_impact()
        panel._flush_editor()


# ---------------------------------------------------------------- 還原
def revert_history(panel, history_id: int) -> None:
    db = panel.db()
    if db is None:
        return
    update_regions(panel.history_card)

    def apply(count, error) -> None:
        if error is not None:
            if isinstance(error, sqlite3.Error):
                log_exception(f"Mod 資料庫還原失敗：history_id={history_id}")
                show_snack(
                    panel._page,
                    f"還原失敗（資料庫錯誤：{error}）；詳情見後台 log",
                    C.RED,
                )
                return
            raise error
        log_info(f"Mod 資料庫還原：history_id={history_id}，還原 {count} 筆")
        show_snack(
            panel._page,
            f"已還原 {count} 筆" if count else "沒有可還原的內容（之後已被再次修改）",
            C.EM if count else C.GOLD,
        )
        after_data_change(panel)

    run_write(
        panel._page,
        lambda: db.revert(history_id),
        apply,
        name="Mod 資料庫還原",
        owner="moddb-revert",
        label="Mod 資料庫還原結果",
    )


def revert_batch(panel, batch_id: str) -> None:
    db = panel.db()
    if db is None:
        return

    def apply(result, error) -> None:
        if error is not None:
            if isinstance(error, sqlite3.Error):
                log_exception(f"Mod 資料庫批次取代還原失敗：batch={batch_id}")
                show_snack(panel._page, f"整批還原失敗（資料庫錯誤：{error}）", C.RED)
                return
            raise error
        show_snack(
            panel._page,
            f"已還原 {result.reverted:,} 筆；因後續修改略過 {result.skipped:,} 筆。",
            C.EM if result.reverted else C.GOLD,
        )
        after_data_change(panel)

    run_write(
        panel._page,
        lambda: db.revert_batch_replace(batch_id),
        apply,
        name="Mod 資料庫批次取代還原",
        owner="moddb-revert-batch",
        label="Mod 資料庫批次還原結果",
    )
