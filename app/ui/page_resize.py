"""頁面 resize 的訂閱派送：頁面只有一個 ``Page.on_resize``，由 AppShell 持有並轉發。

各 view 不直接覆寫 ``page.on_resize``（會蓋掉 AppShell 與對話框的 handler），改用
``subscribe_page_resize`` 登記回呼；回傳的函式用來取消訂閱，view 卸載時必須呼叫。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from translation_tool.utils.log_unit import log_warning

_LISTENERS_ATTR = "_page_resize_listeners"


def subscribe_page_resize(
    page: Any, callback: Callable[[Any], None]
) -> Callable[[], None]:
    """登記 resize 回呼並回傳取消訂閱函式（可重複呼叫）。"""
    listeners = getattr(page, _LISTENERS_ATTR, None)
    if not isinstance(listeners, list):
        listeners = []
        try:
            setattr(page, _LISTENERS_ATTR, listeners)
        except Exception:  # noqa: BLE001 - 無法掛在頁面上時退化成空操作訂閱
            return lambda: None
    listeners.append(callback)

    def unsubscribe() -> None:
        if callback in listeners:
            listeners.remove(callback)

    return unsubscribe


def notify_page_resize(page: Any, event: Any = None) -> None:
    """依序通知所有訂閱者；單一回呼失敗只記錄，不影響其他訂閱者。"""
    for callback in list(getattr(page, _LISTENERS_ATTR, None) or ()):
        try:
            callback(event)
        except Exception as exc:  # noqa: BLE001 - 一個 view 的 resize 失敗不可拖垮其他 view
            log_warning(f"[PageResize] 回呼失敗：{exc!r}")
