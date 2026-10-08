"""快取操作包裝模組。

提供統一的操作執行框架，處理忙碌狀態、異常和 UI 更新。
"""

from __future__ import annotations

import asyncio
import inspect
import time
import traceback
from collections.abc import Callable

import flet as ft

from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    current_operation,
    get_page_operation_registry,
    reserve_page_operation,
)


def run_cache_action(
    view, reason: str, work_fn: Callable, success_msg: str, show_progress: bool = False
):
    """執行快取操作並更新 UI

    參數：
        view: CacheView 實例
        reason: 操作原因（如 RELOADING, SAVING）
        work_fn: 要執行的函數
        success_msg: 成功時的訊息
        show_progress: 是否顯示進度條
    """
    if view.ui_busy:
        view._notify("目前正在處理，請稍候", "warn")
        return

    progress_bar = None
    snack_bar = None
    action_id = int(time.time() * 1000) % 1000000
    view._append_log(f"[ACTION#{action_id}] start {reason}")
    if show_progress and hasattr(view, "page"):
        progress_bar = ft.ProgressBar(value=0, width=300)
        snack_bar = ft.SnackBar(
            content=ft.Row(
                [
                    ft.Text(f"{reason} 處理中..."),
                    progress_bar,
                ],
                spacing=10,
            ),
            duration=999999,  # 長時間顯示
        )
        view.page.snack_bar = snack_bar
        snack_bar.open = True
        view.page.update()

        def progress_callback(current: int, total: int, msg: str | None = None):
            progress_bar.value = current / total if total > 0 else 0
            _run_on_ui(view, lambda: None)
    else:
        progress_callback = None

    view._set_state(True, reason, f"trace: ACTION#{action_id} start {reason}")

    def execute_work():
        return _execute_cache_work(view, work_fn, progress_callback)

    def finish(data, error):
        _finish_cache_action(
            view, action_id, reason, success_msg, snack_bar, data, error
        )

    _schedule_cache_action(view, reason, execute_work, finish)


def _execute_cache_work(view, work_fn, progress_callback):
    """Run one cache action and warm the shard memo in the same owner worker."""
    try:
        if progress_callback is not None and _accepts_on_progress(work_fn):
            result = work_fn(on_progress=progress_callback)
        else:
            result = work_fn()
        warm = getattr(view, "_warm_shard_cache", None)
        if callable(warm) and isinstance(result, dict):
            warm(result)
        return result, None
    except Exception as ex:  # noqa: BLE001 - 錯誤顯示在 UI
        operation = current_operation()
        if operation is not None:
            operation.record_error(ex)
        return None, (ex, traceback.format_exc())


def _finish_cache_action(view, action_id, reason, success_msg, snack_bar, data, error):
    """Apply cache worker result on the UI loop and restore READY state."""
    if error:
        ex, tb = error
        view._append_log(f"[ACTION#{action_id}] error {reason}: {ex}")
        view._append_log(tb)
        view._notify(f"{reason} 失敗: {ex}", "error")
    else:
        view._refresh_overview_ui(data)
        view._refresh_query_type_options()
        view._render_query_type_shard_page()
        view._append_log(f"[ACTION#{action_id}] success {reason}")
        view._notify(success_msg, "info")
    view._append_log(f"[ACTION#{action_id}] finish READY")
    view._set_state(False, "READY", f"trace: ACTION#{action_id} ready")
    view._append_log(f"[STATE] {view.overview_status.value}")
    if snack_bar is not None:
        snack_bar.open = False
        view.page.update()


def _schedule_cache_action(view, reason, execute_work, finish):
    page = _get_page(view)
    run_task = getattr(page, "run_task", None)
    if run_task is None:
        finish(*execute_work())
        return
    if get_page_operation_registry(page) is None:

        async def _run_standalone():
            finish(*await asyncio.to_thread(execute_work))

        run_task(_run_standalone)
        return

    operation = reserve_page_operation(
        page,
        name=f"快取操作 {reason}",
        owner="cache-manager",
        cancellation=CancellationPolicy.NON_CANCELLABLE,
        commit=CommitPolicy.PARTIAL_ALLOWED,
        durability=(
            DurabilityPolicy.USER_ACTION
            if reason == "SAVING"
            else DurabilityPolicy.RECOMPUTABLE
        ),
        shutdown=ShutdownPolicy.DRAIN_ONLY,
    )
    if not operation.admitted:
        finish(None, (RuntimeError("應用程式正在關閉，未啟動快取操作"), ""))
        return
    result = {}

    def work():
        result["value"] = execute_work()

    if not operation.launch(work):
        operation.finish(error=RuntimeError("cache operation worker was not launched"))
        finish(None, (RuntimeError("無法啟動快取操作"), ""))
        return

    async def _run():
        # Registry owns the worker until the actual cache read/write returns;
        # canceling this UI coroutine cannot make the I/O look terminal.
        while not operation.done_event.is_set():
            await asyncio.sleep(0.02)
        data, error = result.get(
            "value", (None, (RuntimeError("快取操作未回傳結果"), ""))
        )
        finish(data, error)

    run_task(_run)


def _get_page(view):
    try:
        return view.page
    except (AttributeError, RuntimeError):
        return None


def _run_on_ui(view, fn: Callable[[], None]) -> None:
    """在 event loop 上執行 fn 並刷新畫面（供背景執行緒呼叫）。"""
    page = _get_page(view)
    run_task = getattr(page, "run_task", None)
    if run_task is None:
        return

    async def _apply():
        fn()
        page.update()

    run_task(_apply)


def _accepts_on_progress(fn: Callable) -> bool:
    try:
        return "on_progress" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
