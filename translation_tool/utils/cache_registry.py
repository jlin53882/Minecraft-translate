"""模組級快取的集中登錄表。

各模組把自己的「清除函式」以名稱登錄在這裡，測試或需要重置程序內狀態時
呼叫 :func:`clear_all_caches` 一次清除。登錄表只負責收集與呼叫，不持有快取內容，
也不 import 任何會產生快取的模組（智慧校對的快取經由
:func:`register_smart_review_caches` 在呼叫時才延遲 import）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

_lock = threading.RLock()
_registry: dict[str, Callable[[], object]] = {}


def register_cache(name: str, clear_fn: Callable[[], object]) -> None:
    """登錄一個快取的清除函式；同名重複登錄以最後一次為準（不會重複呼叫）。"""
    if not name or not isinstance(name, str):
        raise ValueError("快取名稱必須是非空字串")
    if not callable(clear_fn):
        raise TypeError("clear_fn 必須可呼叫")
    with _lock:
        _registry[name] = clear_fn


def registered_cache_names() -> list[str]:
    """回傳目前已登錄的快取名稱（依登錄順序）。"""
    with _lock:
        return list(_registry)


def clear_all_caches() -> list[str]:
    """依登錄順序呼叫每個清除函式，回傳清除失敗的快取名稱。

    單一清除函式失敗不阻斷其他快取；失敗只記錄警告並列在回傳值。
    """
    register_smart_review_caches()
    with _lock:
        items = list(_registry.items())
    failed: list[str] = []
    for name, clear_fn in items:
        try:
            clear_fn()
        except Exception:
            log.warning("清除快取失敗：%s", name, exc_info=True)
            failed.append(name)
    return failed


def register_smart_review_caches() -> list[str]:
    """登錄智慧校對的快取清除函式；呼叫時才 import，缺少的函式會略過。

    回傳實際登錄的名稱。
    """
    registered: list[str] = []
    try:
        from translation_tool.translation_db import smart_review
    except ImportError:
        smart_review = None
    fn = getattr(smart_review, "clear_smart_review_caches", None)
    if callable(fn):
        register_cache("smart_review.caches", fn)
        registered.append("smart_review.caches")
    try:
        from translation_tool.translation_db import smart_review_history
    except ImportError:
        smart_review_history = None
    store = getattr(smart_review_history, "SmartReviewHistoryStore", None)
    fn = getattr(store, "clear_caches", None)
    if callable(fn):
        register_cache("smart_review_history.caches", fn)
        registered.append("smart_review_history.caches")
    return registered
