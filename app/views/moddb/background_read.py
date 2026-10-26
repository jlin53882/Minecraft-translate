"""條目校對面板與對話框共用的背景唯讀工作。

資料庫讀取（審核範圍預覽、影響預覽…）一律在 worker 執行，結果才排回畫面執行緒；
畫面執行緒不碰資料庫。沒有背景排程的頁面（單元測試）依序同步執行，行為相同。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.tasks.operation_registry import (
    CancellationPolicy,
    OperationPresentation,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui.page_post import has_background_scheduler, post_to_ui
from translation_tool.utils.log_unit import log_warning


def run_read(
    page,
    work: Callable[[], Any],
    apply: Callable[[Any, BaseException | None], None],
    *,
    name: str,
    owner: str,
) -> bool:
    """在背景執行唯讀 ``work()``，把 ``(結果, 例外)`` 排回畫面執行緒交給 ``apply``。

    回傳是否已受理；應用程式正在關閉（或無法啟動）時回傳 False，``apply`` 不會被呼叫。
    例外不會往外丟：由 ``apply`` 依型別顯示錯誤。
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
        post_to_ui(page, apply, value, error, label=name)

    try:
        launched = launch_page_operation(
            page,
            in_worker,
            name=name,
            owner=owner,
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
            presentation=OperationPresentation.MAINTENANCE,
        )
    except Exception as exc:  # noqa: BLE001 - 無法啟動：讓呼叫端恢復畫面
        log_warning(f"{name}無法啟動：{exc!r}")
        return False
    return launched is not False
