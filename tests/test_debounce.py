"""Debouncer：callback 一律在 event loop（run_task）上執行，且只執行最後一次。"""

import asyncio
import inspect

from app.ui.debounce import Debouncer


class _Page:
    def __init__(self):
        self.tasks = []

    def run_task(self, handler, *args):
        assert inspect.iscoroutinefunction(handler)
        self.tasks.append(handler)

    def drain(self):
        tasks, self.tasks = self.tasks, []
        for handler in tasks:
            asyncio.run(handler())


def test_only_last_call_runs():
    page = _Page()
    d = Debouncer(lambda: page, 0)
    out = []
    d.call(out.append, 1)
    d.call(out.append, 2)
    page.drain()
    assert out == [2]


def test_cancel_drops_pending_call():
    page = _Page()
    d = Debouncer(lambda: page, 0)
    out = []
    d.call(out.append, 1)
    d.cancel()
    page.drain()
    assert out == []


def test_unmounted_control_is_ignored():
    def _raise():
        raise RuntimeError("not mounted")

    Debouncer(_raise, 0).call(lambda: None)
    Debouncer(lambda: None, 0).call(lambda: None)
