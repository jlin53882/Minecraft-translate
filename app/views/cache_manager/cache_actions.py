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

    # 顯示 ProgressBar（直接用簡單的 ProgressBar，避免自定義類的渲染問題）
    if show_progress and hasattr(view, 'page'):
        # 直接用 ft.ProgressBar，不使用自定義 ProgressCard 類
        progress_bar = ft.ProgressBar(width=300, value=0.3)
        progress_text = ft.Text(f"{reason} 處理中...", size=14)
        progress_content = ft.Container(
            content=ft.Column([progress_text, progress_bar], spacing=5),
            padding=20,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border_radius=10,
        )
        view.page.overlay.append(progress_content)
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

        # 移除 ProgressBar overlay
        if show_progress and hasattr(view, 'page') and 'progress_content' in locals():
            try:
                view.page.overlay.remove(progress_content)
                view.page.update()
            except:
                pass
