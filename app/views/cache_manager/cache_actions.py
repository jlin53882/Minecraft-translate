"""快取操作包裝模組。

提供統一的操作執行框架，處理忙碌狀態、異常和 UI 更新。
"""

from __future__ import annotations

import asyncio
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import flet as ft


def run_cache_action(view, reason: str, work_fn: Callable, success_msg: str, show_progress: bool = False):
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
    status_text = None
    banner = None
    action_id = int(time.time() * 1000) % 1000000
    view._append_log(f"[ACTION#{action_id}] start {reason}")

    # 顯示進度條（使用 Banner，不會自動消失）
    if show_progress and hasattr(view, 'page'):
        progress_bar = ft.ProgressBar(value=0, width=300)
        status_text = ft.Text(f"{reason} 處理中...", size=14)
        banner = ft.Banner(
            content=ft.Column([
                status_text,
                progress_bar,
            ], spacing=5),
            actions=[
                ft.TextButton(text="處理中...", disabled=True),
            ],
        )
        view.page.banner = banner
        view.page.banner.open = True
        view.page.update()

        # 進度回調函式
        def progress_callback(current: int, total: int, msg: str = None):
            if not hasattr(view, 'page') or view.page is None:
                return
            progress_bar.value = current / total if total > 0 else 0
            if msg:
                status_text.value = msg
            view.page.update()
    else:
        progress_callback = None

    view._set_state(True, reason, f"trace: ACTION#{action_id} start {reason}")

    # 使用线程池执行，避免阻塞 UI
    def execute_work():
        try:
            # 執行操作，傳入進度回調
            # 注意：service 函數目前不支持 on_progress，會被忽略
            try:
                data = work_fn(on_progress=progress_callback)
            except TypeError:
                try:
                    data = work_fn(progress_callback)
                except (TypeError, Exception):
                    data = work_fn()
            return data, None
        except Exception as ex:
            return None, ex

    # 在線程池中執行
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(execute_work)

    # 等待結果
    data, error = future.result()
    executor.shutdown(wait=True)

    # 處理結果
    if error:
        view._append_log(f"[ACTION#{action_id}] error {reason}: {error}")
        view._append_log(traceback.format_exc())
        view._notify(f"{reason} 失敗: {error}", "error")
    else:
        view._refresh_overview_ui(data)
        view._refresh_query_type_options()
        view._render_query_type_shard_page()
        view._append_log(f"[ACTION#{action_id}] success {reason}")
        view._notify(success_msg, "info")

    # 清理
    view._append_log(f"[ACTION#{action_id}] finish READY")
    view._set_state(False, "READY", f"trace: ACTION#{action_id} ready")
    view._append_log(f"[STATE] {view.overview_status.value}")

    # 關閉 Banner
    if banner and hasattr(view, 'page') and view.page:
        view.page.banner.open = False
        view.page.update()
