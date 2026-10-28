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
    view,
    reason: str,
    work_fn: Callable,
    success_msg: str,
    show_progress: bool = False,
    on_success: Callable[[object], None] | None = None,
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
    mount_generation = _view_generation(view)
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
            value = current / total if total > 0 else 0
            _run_on_ui(
                view,
                lambda: setattr(progress_bar, "value", value),
                mount_generation,
            )
    else:
        progress_callback = None

    view._set_state(True, reason, f"trace: ACTION#{action_id} start {reason}")

    def execute_work():
        return _execute_cache_work(view, work_fn, progress_callback)

    def finish(data, error):
        _finish_cache_action(
            view,
            action_id,
            reason,
            success_msg,
            snack_bar,
            data,
            error,
            on_success=on_success,
            mount_generation=mount_generation,
        )

    _schedule_cache_action(view, reason, execute_work, finish)


def _execute_cache_work(view, work_fn, progress_callback, *, warm: bool = True):
    """Run one cache action and warm the shard memo in the same owner worker."""
    try:
        if progress_callback is not None and _accepts_on_progress(work_fn):
            result = work_fn(on_progress=progress_callback)
        else:
            result = work_fn()
        warm_fn = getattr(view, "_warm_shard_cache", None)
        if warm and callable(warm_fn) and isinstance(result, dict):
            warm_fn(result)
        return result, None
    except Exception as ex:  # noqa: BLE001 - 錯誤顯示在 UI
        operation = current_operation()
        if operation is not None:
            operation.record_error(ex)
        return None, (ex, traceback.format_exc())


def _finish_cache_action(
    view,
    action_id,
    reason,
    success_msg,
    snack_bar,
    data,
    error,
    *,
    on_success: Callable[[object], None] | None = None,
    mount_generation=None,
):
    """Apply cache worker result on the UI loop and restore READY state."""
    if not _view_is_current(view, mount_generation):
        return

    if error:
        ex, tb = error
        view._append_log(f"[ACTION#{action_id}] error {reason}: {ex}")
        view._append_log(tb)
        failed_overview = getattr(ex, "overview", None)
        if isinstance(failed_overview, dict):
            view._refresh_overview_ui(failed_overview)
        view._notify(f"{reason} 失敗: {ex}", "error")
    else:
        try:
            view._refresh_overview_ui(data)
            view._refresh_query_type_options()
            view._render_query_type_shard_page()
            if on_success is not None:
                on_success(data)
        except Exception as ex:  # noqa: BLE001 - UI callback boundary
            view._append_log(f"[ACTION#{action_id}] UI completion error {reason}: {ex}")
            view._append_log(traceback.format_exc())
            view._notify(f"{reason} 失敗：{ex}", "error")
            error = (ex, traceback.format_exc())
        else:
            view._append_log(f"[ACTION#{action_id}] success {reason}")
            view._notify(success_msg, "info")
    view._append_log(f"[ACTION#{action_id}] finish READY")
    view._set_state(False, "READY", f"trace: ACTION#{action_id} ready")
    view._append_log(f"[STATE] {view.overview_status.value}")
    if snack_bar is not None:
        snack_bar.open = False
        _safe_page_update(view)


def run_cache_background(
    view,
    name: str,
    work_fn: Callable,
    on_success: Callable[[object], None],
    on_error: Callable[[BaseException], None] | None = None,
    on_discard: Callable[[], None] | None = None,
):
    """Run a cache read in an owned worker and apply its result on the UI loop.

    This is intentionally separate from ``run_cache_action``: cold reads such
    as a shard entry or a history index must not lock every cache control while
    they are loading, but they still need OperationRegistry ownership and the
    same mount-generation guard.
    """
    page = _get_page(view)
    run_task = getattr(page, "run_task", None)
    mount_generation = _view_generation(view)

    def finish(data, error):
        if not _view_is_current(view, mount_generation):
            if on_discard is not None:
                on_discard()
            return
        if error is not None:
            if on_error is not None:
                on_error(error[0])
            return
        on_success(data)
        _safe_page_update(view)

    def execute():
        return _execute_cache_work(view, work_fn, None, warm=False)

    if run_task is None:
        finish(*execute())
        return

    registry = get_page_operation_registry(page)
    if registry is None:

        async def _run_standalone():
            finish(*await asyncio.to_thread(execute))

        try:
            run_task(_run_standalone)
        except RuntimeError:
            return
        return

    operation = reserve_page_operation(
        page,
        name=name,
        owner="cache-manager",
        cancellation=CancellationPolicy.NON_CANCELLABLE,
        commit=CommitPolicy.EPHEMERAL,
        durability=DurabilityPolicy.RECOMPUTABLE,
        shutdown=ShutdownPolicy.DRAIN_ONLY,
    )
    if not operation.admitted:
        finish(None, (RuntimeError("應用程式正在關閉，未啟動背景讀取"), ""))
        return

    result = {}

    def work():
        result["value"] = execute()

    if not operation.launch(work):
        operation.finish(error=RuntimeError("無法啟動背景讀取"))
        finish(None, (RuntimeError("無法啟動背景讀取"), ""))
        return

    async def _wait():
        while not operation.done_event.is_set():
            await asyncio.sleep(0.02)
        finish(*result.get("value", (None, (RuntimeError("背景讀取未回傳結果"), ""))))

    try:
        run_task(_wait)
    except RuntimeError:
        return


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


def _view_generation(view):
    return getattr(view, "_mount_generation", None)


def _view_is_current(view, generation) -> bool:
    if generation is None:
        return True
    current_generation = getattr(view, "_mount_generation", generation)
    if current_generation != generation:
        return False
    # CacheView starts unmounted at generation 0; test doubles also commonly
    # omit did_mount.  Once a real mount has happened, False means teardown.
    return not (generation > 0 and getattr(view, "_is_mounted", True) is False)


def _safe_page_update(view) -> None:
    page = _get_page(view)
    if page is None:
        return
    try:
        page.update()
    except RuntimeError:
        # A late worker result must not surface Flet's destroyed-session error.
        return


def _run_on_ui(view, fn: Callable[[], None], mount_generation=None) -> None:
    """在 event loop 上執行 fn 並刷新畫面（供背景執行緒呼叫）。"""
    page = _get_page(view)
    run_task = getattr(page, "run_task", None)
    if run_task is None:
        return

    async def _apply():
        if not _view_is_current(view, mount_generation):
            return
        fn()
        _safe_page_update(view)

    try:
        run_task(_apply)
    except RuntimeError:
        return


def _accepts_on_progress(fn: Callable) -> bool:
    try:
        return "on_progress" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
