"""頁面 resize 派送：Flet 1.0 只有 ``Page.on_resize``，各 view 經 AppShell 訂閱而不覆寫 handler。"""

from __future__ import annotations

import flet as ft

from app.ui.page_resize import notify_page_resize, subscribe_page_resize
from app.views.cache_view import CacheView
from tests.conftest import mock_page


def test_flet_page_has_on_resize_but_no_on_resized():
    """鎖定的 Flet 版本沒有 ``on_resized``：設了也不會有任何 resize 事件。"""
    assert hasattr(ft.Page, "on_resize")
    assert not hasattr(ft.Page, "on_resized")


def test_subscribe_notify_and_unsubscribe():
    page = mock_page()
    calls = []
    unsubscribe = subscribe_page_resize(page, calls.append)
    notify_page_resize(page, "e1")
    unsubscribe()
    unsubscribe()  # 重複取消不出錯
    notify_page_resize(page, "e2")
    assert calls == ["e1"]


def test_failing_listener_does_not_block_others():
    page = mock_page()
    calls = []

    def bad(_e):
        raise RuntimeError("boom")

    subscribe_page_resize(page, bad)
    subscribe_page_resize(page, calls.append)
    notify_page_resize(page, "x")
    assert calls == ["x"]


def test_cache_view_subscribes_via_dispatcher_and_unmount_leaves_no_handler():
    page = mock_page()
    view = CacheView(page)
    resized = []
    view._on_page_resized = resized.append  # type: ignore[method-assign]

    view._bind_page_resize(page)
    view._bind_page_resize(page)  # 重複掛載只保留一個訂閱
    notify_page_resize(page, "e")
    assert resized == ["e"]
    assert not hasattr(page, "on_resized")

    view.will_unmount()
    notify_page_resize(page, "e2")
    assert resized == ["e"]
    assert page._page_resize_listeners == []
