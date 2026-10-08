import asyncio
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app.tasks.operation_registry import OperationRegistry, reserve_page_operation
from app.views.icon_preview.load_operation import (
    load_icon_preview,
    observe_async_owner,
)


def _reserve(registry):
    page = SimpleNamespace(operation_registry=registry)
    return reserve_page_operation(page, name="IconPreview", owner="icon-preview")


def test_cancelled_ui_task_before_coroutine_start_releases_reservation():
    registry = OperationRegistry()
    operation = _reserve(registry)
    future = Future()
    future.cancel()

    observe_async_owner(future, operation)

    assert operation.done_event.is_set()
    assert registry.active() == []


def test_cancelled_icon_load_drains_its_blocking_thread_before_terminal():
    registry = OperationRegistry()
    operation = _reserve(registry)
    entered = threading.Event()
    release = threading.Event()

    class View:
        _load_generation = 1
        page = None

        def _detect_source_mode(self):
            entered.set()
            release.wait(timeout=2)
            return "jar_directory"

        def _update_load_state(self):
            pass

    async def scenario():
        task = asyncio.create_task(load_icon_preview(View(), 1, operation))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert registry.active_count() == 1
        assert not operation.done_event.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert operation.done_event.is_set()
        assert registry.active() == []

    asyncio.run(scenario())
