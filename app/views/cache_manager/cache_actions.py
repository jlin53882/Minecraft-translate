"""快取操作包裝模組。

提供統一的操作執行框架，處理忙碌狀態、異常和 UI 更新。
"""

from __future__ import annotations

import time
import traceback
from typing import Callable

import flet as ft
from app.ui.components import ProgressCard


def run_cache_action(view, reason: str, work_fn: Callable, success_msg: str, show_progress: bool = False):
    """執行快取操作並更新 UI

    參數：
        view: CacheView 實例
        reason: 操作原因（如 RELOADING, SAVING）
        work_fn: 要執行的函數
        success_msg: 成功時的訊息
        show_progress: 是否顯示 ProgressCard（長時間操作）
    """
    if view.ui_busy:
        view._notify("目前正在處理，請稍候", "warn")
        return

    # ProgressCard 實例（如果需要顯示進度）
    progress_card = None
    progress_dialog = None

    action_id = int(time.time() * 1000) % 1000000
    view._append_log(f"[ACTION#{action_id}] start {reason}")

    # 顯示 ProgressCard（使用 Dialog 方式）
    if show_progress and hasattr(view, 'page'):
        progress_card = ProgressCard(
            title=f"操作進行中: {reason}",
            current=0,
            total=100,
        )
        progress_card.start()

        # 使用 Banner 顯示進度（比 Dialog 更輕量）
        progress_dialog = ft.Banner(
            leading=ft.Icon(ft.Icons.HOURGLASS_TOP, color=ft.Colors.BLUE),
            content=progress_card,
            actions=[],
        )
        view.page.add(progress_dialog)
        progress_dialog.open = True
        view.page.update()

    view._set_state(True, reason, f"trace: ACTION#{action_id} start {reason}")

    try:
        data = work_fn()

        view._refresh_overview_ui(data)
        view._refresh_query_type_options()
        view._render_query_type_shard_page()
        view._append_log(f"[ACTION#{action_id}] success {reason}")
        view._notify(success_msg, "info")
    except Exception as ex:
        view._append_log(f"[ACTION#{action_id}] error {reason}: {ex}")
        view._append_log(traceback.format_exc())
        view._notify(f"{reason} 失敗: {ex}", "error")
    finally:
        view._append_log(f"[ACTION#{action_id}] finish READY")
        view._set_state(False, "READY", f"trace: ACTION#{action_id} ready")
        view._append_log(f"[STATE] {view.overview_status.value}")

        # 移除 ProgressCard Banner
        if progress_dialog and hasattr(view, 'page'):
            progress_dialog.open = False
            view.page.remove(progress_dialog)
            view.page.update()
