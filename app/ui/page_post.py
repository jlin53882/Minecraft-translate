"""把背景執行緒的結果排回畫面執行緒的薄 helper。

頁面已卸載或排程失敗時，結果回呼不會執行；呼叫端可用 ``on_failure`` 回收純背景狀態。
這個 recovery callback 一定在 worker thread 執行，因此不得讀寫 Flet controls；頁面已卸載時
也不應再嘗試觸碰 UI。回呼本身稍後在畫面執行緒拋錯不屬於排程失敗，由事件迴圈記錄。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from translation_tool.utils.log_unit import log_warning


def post_to_ui(
    page: Any,
    callback: Callable[..., None],
    *args: Any,
    label: str,
    on_failure: Callable[[], None] | None = None,
) -> bool:
    """把 ``callback(*args)`` 排回畫面執行緒；成功排程回傳 True。

    沒有 ``run_task`` 的頁面（測試用）直接同步呼叫。排程失敗時記錄警告並在 worker
    thread 呼叫 ``on_failure``（所以它只能回收非 UI 狀態），回傳 False。
    """
    run_task = getattr(page, "run_task", None)
    try:
        if callable(run_task):

            async def runner() -> None:
                callback(*args)

            run_task(runner)
        else:
            callback(*args)
    except Exception as exc:  # noqa: BLE001 - a disposed page may reject the callback
        log_warning(f"{label}無法排回畫面：{exc!r}")
        if on_failure is not None:
            try:
                on_failure()
            except Exception as recover_exc:  # noqa: BLE001 - recovery must not raise into the worker
                log_warning(f"{label}排程失敗後回收狀態也失敗：{recover_exc!r}")
        return False
    return True
