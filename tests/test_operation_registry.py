from __future__ import annotations

import threading

from app.tasks.operation_registry import (
    OperationDescriptor,
    OperationRegistry,
    launch_task_thread,
)
from app.tasks.task_session import TaskSession


def _descriptor(name="work"):
    return OperationDescriptor(name=name, owner="test")


def test_registry_keeps_cancel_requested_work_active_until_worker_returns():
    registry = OperationRegistry()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def work():
        started.set()
        release.wait(2)
        finished.set()

    handle = registry.launch(work, _descriptor())
    assert handle is not None
    assert started.wait(2)

    registry.begin_shutdown()

    assert handle.cancel_requested
    assert not handle.done_event.is_set()
    assert registry.active() == [handle]
    assert not registry.wait_for_idle(timeout=0.01)

    release.set()
    assert registry.wait_for_idle(timeout=2)
    assert finished.is_set()
    assert handle.done_event.is_set()
    assert handle.terminal_reason == "cancelled"


def test_closed_admission_rejects_before_calling_launcher():
    registry = OperationRegistry()
    registry.begin_shutdown()
    launched = []

    handle = registry.launch(
        lambda: None,
        _descriptor(),
        launcher=lambda target: launched.append(target),
    )

    assert handle is None
    assert launched == []
    assert registry.active() == []


def test_cancel_while_reserved_skips_work_but_finishes_owned_session():
    registry = OperationRegistry()
    session = TaskSession(name="reserved")
    handle = registry.reserve(_descriptor())
    assert handle is not None
    handle.bind_task_session(session)

    registry.begin_shutdown()
    session.start()
    ran = []
    assert handle.launch(lambda: ran.append(True))

    assert ran == []
    assert session.cancel_requested
    assert session.is_finished
    assert registry.active() == []


def test_task_thread_reserves_before_launch_and_binds_session_cancellation():
    registry = OperationRegistry()
    session = TaskSession(name="bound")
    called = threading.Event()
    handle = launch_task_thread(
        registry,
        called.set,
        name="bound",
        owner="test",
        task_session=session,
    )

    assert handle is not None
    assert called.wait(2)
    assert session.operation_handle is handle
    assert registry.wait_for_idle(timeout=2)


def test_admission_can_only_be_reopened_explicitly_after_drain():
    registry = OperationRegistry()
    handle = registry.reserve(_descriptor())
    assert handle is not None
    registry.begin_shutdown()

    try:
        registry.reopen_admission()
    except RuntimeError:
        pass
    else:
        raise AssertionError("active operation must prevent reopening admission")

    handle.run(lambda: None)
    assert registry.active() == []
    registry.reopen_admission()
    assert registry.accepting
    assert registry.shutdown_state == registry.OPEN
