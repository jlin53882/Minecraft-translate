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

    # 進度條組件
    progress_bar = None
    snack_bar = None
    action_id = int(time.time() * 1000) % 1000000
    view._append_log(f"[ACTION#{action_id}] start {reason}")

    # 顯示 SnackBar 進度條
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

        # 進度回調函式
        def progress_callback(current: int, total: int, msg: str | None = None):
            # 由背景執行緒呼叫：只改數值，畫面由 event loop 刷新
            progress_bar.value = current / total if total > 0 else 0
            _run_on_ui(view, lambda: None)
    else:
        progress_callback = None

    view._set_state(True, reason, f"trace: ACTION#{action_id} start {reason}")

    def execute_work():
        """在背景執行緒執行工作；只呼叫 work_fn 一次（原本 TypeError 時會重跑）。"""
        try:
            if progress_callback is not None and _accepts_on_progress(work_fn):
                return work_fn(on_progress=progress_callback), None
            return work_fn(), None
        except Exception as ex:  # noqa: BLE001 - 錯誤顯示在 UI
            return None, (ex, traceback.format_exc())

    def finish(data, error):
        """（event loop 上）套用結果並恢復就緒狀態。"""
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

    run_task = getattr(_get_page(view), "run_task", None)
    if run_task is None:
        # 沒有 event loop（測試用假 page）：同步執行
        finish(*execute_work())
        return

    async def _run():
        # 重新載入 / 重建索引可能要數十秒，改在執行緒執行，
        # 不再以 future.result() 在 event loop 上同步等待（原本整個 UI 凍結）
        data, error = await asyncio.to_thread(execute_work)
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
