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


def test_icon_load_cancel_scope_reaches_asyncio_to_thread_worker():
    from translation_tool.utils.cancellation import is_cancelled, raise_if_cancelled

    registry = OperationRegistry()
    operation = _reserve(registry)
    entered = threading.Event()
    release = threading.Event()
    worker_saw_cancel = []
    later_stages = []

    class View:
        _load_generation = 1
        page = None

        def _detect_source_mode(self):
            entered.set()
            assert release.wait(timeout=2)
            worker_saw_cancel.append(is_cancelled())
            raise_if_cancelled()

        def _lookup_cached_entries(self, _mode):
            later_stages.append("lookup")

        def _update_load_state(self):
            pass

    async def scenario():
        task = asyncio.create_task(load_icon_preview(View(), 1, operation))
        assert await asyncio.to_thread(entered.wait, 1)
        assert operation.handle.request_cancel()
        await asyncio.sleep(0)
        assert registry.active_count() == 1
        assert not operation.done_event.is_set()
        release.set()
        await task
        assert worker_saw_cancel == [True]
        assert later_stages == []
        assert operation.done_event.is_set()
        assert operation.handle.terminal_reason == "cancelled"
        assert registry.active() == []

    asyncio.run(scenario())


def test_icon_load_unmount_invalidates_worker_and_finishes_cancelled():
    from translation_tool.utils.cancellation import is_cancelled, raise_if_cancelled

    registry = OperationRegistry()
    operation = _reserve(registry)
    entered = threading.Event()
    release = threading.Event()
    worker_saw_cancel = []

    class View:
        _load_generation = 1
        page = None

        def _detect_source_mode(self):
            entered.set()
            assert release.wait(timeout=2)
            worker_saw_cancel.append(is_cancelled())
            raise_if_cancelled()

        def _update_load_state(self):
            pass

    async def scenario():
        view = View()
        task = asyncio.create_task(load_icon_preview(view, 1, operation))
        assert await asyncio.to_thread(entered.wait, 1)
        view._load_generation += 1
        release.set()
        await task
        assert worker_saw_cancel == [True]
        assert operation.done_event.is_set()
        assert operation.handle.terminal_reason == "cancelled"
        assert registry.active() == []

    asyncio.run(scenario())


def test_icon_load_handled_error_finishes_registry_as_failed(monkeypatch):
    registry = OperationRegistry()
    operation = _reserve(registry)
    failure = OSError("cache read failed")

    class View:
        _load_generation = 1
        _loading = True
        page = None

        def _detect_source_mode(self):
            return "jar_directory"

        def _lookup_cached_entries(self, _mode):
            raise failure

        def _update_load_state(self):
            pass

        def _finish_load(self, _entries, _mode):
            pass

    monkeypatch.setattr(
        "app.views.icon_preview.load_operation.show_snack", lambda *_a, **_k: None
    )

    asyncio.run(load_icon_preview(View(), 1, operation))

    assert operation.done_event.is_set()
    assert operation.handle.terminal_reason == "failed"
    assert operation.handle.error is failure
    assert registry.active() == []


def test_icon_row_preparation_checks_cancel_between_rows(monkeypatch, tmp_path):
    from app.views.icon_preview import detail_mixin
    from translation_tool.utils.cancellation import TaskCancelled, cancel_scope

    cancelled = threading.Event()
    prepared = []

    def prepare(key, *_args):
        prepared.append(key)
        cancelled.set()
        return key

    monkeypatch.setattr(detail_mixin, "prepare_row_icon", prepare)
    entries = [SimpleNamespace(key="first"), SimpleNamespace(key="second")]

    with cancel_scope(cancelled.is_set), pytest.raises(TaskCancelled):
        detail_mixin.IconPreviewDetailMixin._prepare_row_icons(
            entries, (tmp_path, tmp_path)
        )

    assert prepared == ["first"]
