"""TaskManager：從 TaskSession 全域事件追蹤進行中 / 最近完成的任務。"""

from __future__ import annotations

import gc
from types import SimpleNamespace

import pytest

from app.shell.task_manager import (
    DEFAULT_TASK_NAME,
    STATUS_DONE,
    STATUS_ERROR,
    TaskManager,
)
from app.tasks.operation_registry import (
    CommitPolicy,
    DurabilityPolicy,
    OperationDescriptor,
)
from app.tasks.task_session import TaskSession, TaskSessionAdmissionError, tag_session


@pytest.fixture
def manager():
    m = TaskManager()
    m.attach()
    yield m
    m.detach()


def test_session_lifecycle_is_tracked(manager):
    session = TaskSession(name="機器翻譯", view_key="lm")
    assert manager.active() == []

    session.start()
    current = manager.current()
    assert current is not None
    assert (current.name, current.view_key, current.running) == ("機器翻譯", "lm", True)

    session.set_progress(0.42)
    assert manager.current().percent == 42

    session.finish()
    assert manager.active() == []
    done = manager.recent()[0]
    assert done.status == STATUS_DONE and done.percent == 100
    assert done.finished_at is not None


def test_unnamed_session_gets_default_name(manager):
    session = TaskSession()
    session.start()
    assert manager.current().name == DEFAULT_TASK_NAME
    session.finish()


def test_error_stays_error_after_finish(manager):
    session = TaskSession(name="翻譯")
    session.start()
    session.set_error()
    assert manager.current().status == STATUS_ERROR  # 仍算進行中，直到 finish
    session.finish()
    assert manager.active() == []
    assert manager.recent()[0].status == STATUS_ERROR
    assert manager.recent()[0].progress < 1.0  # 失敗不會被顯示成 100%


def test_events_without_start_are_ignored(manager):
    session = TaskSession(name="x")
    session.set_progress(0.5)
    session.finish()
    assert manager.active() == [] and manager.recent() == []


def test_current_is_most_recently_started(manager):
    first, second = TaskSession(name="甲"), TaskSession(name="乙")
    first.start()
    second.start()
    assert [t.name for t in manager.active()] == ["甲", "乙"]
    assert manager.current().name == "乙"
    second.finish()
    assert manager.current().name == "甲"
    first.finish()


def test_subscribers_are_notified_and_can_unsubscribe(manager):
    calls = []
    unsubscribe = manager.subscribe(lambda: calls.append(1))
    session = TaskSession()
    session.start()
    session.set_progress(0.1)
    session.finish()
    # The TaskSession projection and Registry terminal transition are distinct:
    # the latter refreshes subscribers after active membership is removed.
    assert len(calls) == 4
    unsubscribe()
    session.start()
    assert len(calls) == 4
    session.finish()


def test_registry_only_operation_is_projected_and_recorded_as_recent(manager):
    handle = manager.operation_registry.reserve(
        OperationDescriptor(
            name="索引更新",
            owner="cache",
            view_key="cache",
            commit=CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )
    )
    assert handle is not None
    assert handle.descriptor.commit == CommitPolicy.ATOMIC
    assert handle.descriptor.durability == DurabilityPolicy.USER_ACTION
    active = manager.active()
    assert len(active) == 1
    assert (active[0].name, active[0].view_key) == ("索引更新", "cache")

    handle.finish()
    assert manager.active() == []
    assert manager.recent()[0].name == "索引更新"
    assert manager.recent()[0].status == STATUS_DONE


def test_session_registry_terminal_notifies_subscribers_of_idle(manager):
    session = TaskSession(name="session-backed operation")
    handle = manager.operation_registry.reserve(
        OperationDescriptor(name="session-backed operation", owner="test"),
        task_session=session,
    )
    assert handle is not None
    observed_active_counts = []
    manager.subscribe(
        lambda: observed_active_counts.append(manager.operation_registry.active_count())
    )

    session.start()
    session.finish()

    assert observed_active_counts[-2:] == [1, 0]
    assert manager.operation_registry.active_count() == 0
    assert len(manager.recent()) == 1


@pytest.mark.parametrize(
    ("outcome", "expected_reason", "expected_status"),
    [
        ("success", "session_finished", STATUS_DONE),
        ("error", "failed", STATUS_ERROR),
        ("cancel", "cancelled", STATUS_DONE),
    ],
)
def test_legacy_session_terminal_result_matches_registry(
    manager, outcome, expected_reason, expected_status
):
    session = TaskSession(name=f"legacy {outcome}")
    session.start()
    handle = session.operation_handle

    if outcome == "error":
        session.set_error()
    elif outcome == "cancel":
        assert handle.request_cancel()

    session.finish()

    assert handle.done_event.is_set()
    assert handle.terminal_reason == expected_reason
    assert (handle.error is not None) == (outcome == "error")
    assert manager.recent()[0].status == expected_status


def test_cancel_before_task_session_start_still_records_recent_terminal(manager):
    session = TaskSession(name="cancel before start")
    handle = manager.operation_registry.reserve(
        OperationDescriptor(name="cancel before start", owner="test"),
        task_session=session,
    )
    assert handle is not None
    assert handle.request_cancel()
    assert handle.launch(lambda: pytest.fail("cancelled worker must not run"))

    assert handle.done_event.wait(2)
    assert handle.terminal_reason == "cancelled"
    assert manager.operation_registry.active_count() == 0
    assert len(manager.recent()) == 1
    assert manager.recent()[0].status == STATUS_DONE


def test_detach_keeps_admission_gate_until_last_operation_finishes(manager):
    page = SimpleNamespace(operation_registry=manager.operation_registry)
    session = tag_session(TaskSession(name="Web timeout drain"), "Web", page=page)
    session.start()
    manager.stop_accepting()
    manager.detach()

    rejected = tag_session(TaskSession(name="late session"), "late", page=page)
    with pytest.raises(TaskSessionAdmissionError):
        rejected.start()

    session.finish()
    assert manager.operation_registry.active_count() == 0
    assert manager._attached is False


def test_legacy_sessions_are_admitted_only_by_their_own_web_registry():
    from types import SimpleNamespace

    first = TaskManager()
    second = TaskManager()
    first.attach()
    second.attach()
    page_a = SimpleNamespace(operation_registry=first.operation_registry)
    page_b = SimpleNamespace(operation_registry=second.operation_registry)
    try:
        session_a = tag_session(
            TaskSession(name="A draining session"), "A", page=page_a
        )
        session_a.start()
        first.stop_accepting()
        first.detach()

        session_b = tag_session(TaskSession(name="B legacy session"), "B", page=page_b)
        session_b.start()

        assert first.operation_registry.active_count() == 1
        assert second.operation_registry.active_count() == 1
        assert [task.name for task in first.active()] == ["A"]
        assert [task.name for task in second.active()] == ["B"]

        session_b.finish()
        session_a.finish()
        assert first.operation_registry.active_count() == 0
        assert second.operation_registry.active_count() == 0
    finally:
        first.detach()
        second.detach()


def test_composite_parent_remains_visible_after_child_session_finishes(manager):
    import threading

    parent = manager.operation_registry.reserve(
        OperationDescriptor(name="一鍵流水線", owner="pipeline", view_key="pipeline")
    )
    assert parent is not None
    entered = threading.Event()
    release = threading.Event()
    assert parent.launch(lambda: (entered.set(), release.wait(timeout=2)))
    assert entered.wait(timeout=1)
    child = TaskSession(name="語系合併", view_key="pipeline")
    parent.bind_task_session(child)
    child.start()
    child.finish()

    # Child progress session is terminal, but sequence owner remains active.
    active = manager.active()
    assert len(active) == 1
    assert (active[0].name, active[0].view_key) == ("一鍵流水線", "pipeline")
    release.set()
    assert parent.done_event.wait(timeout=1)
    assert manager.active() == []


def test_a_failing_subscriber_does_not_break_the_task(manager):
    def boom():
        raise RuntimeError("ui bug")

    manager.subscribe(boom)
    session = TaskSession()
    session.start()  # 不可丟例外
    session.finish()
    assert manager.recent()


def test_detach_stops_tracking():
    m = TaskManager()
    m.attach()
    m.detach()
    session = TaskSession()
    session.start()
    assert m.active() == []
    session.finish()


def test_detached_task_session_cannot_bypass_closed_registry_admission():
    manager = TaskManager()
    manager.attach()
    page = SimpleNamespace(operation_registry=manager.operation_registry)
    session = tag_session(TaskSession(name="late callback"), "late", page=page)
    manager.stop_accepting()
    manager.operation_registry.mark_closed()
    manager.detach()

    with pytest.raises(TaskSessionAdmissionError):
        session.start()

    assert session.status == "IDLE"
    assert manager.operation_registry.active_count() == 0


def test_detached_task_manager_does_not_own_legacy_session_terminal():
    manager = TaskManager()
    manager.attach()
    page = SimpleNamespace(operation_registry=manager.operation_registry)
    session = tag_session(TaskSession(name="detached legacy"), "late", page=page)
    manager.detach()

    session.start()
    assert manager.operation_registry.active_count() == 1
    session.finish()

    assert manager.operation_registry.active_count() == 0
    assert manager.operation_registry.wait_for_idle(timeout=0)


def test_ownerless_legacy_session_is_rejected_by_multiple_workspaces():
    first = TaskManager()
    second = TaskManager()
    first.attach()
    second.attach()
    first.stop_accepting()

    session = TaskSession(name="ambiguous owner")
    with pytest.raises(TaskSessionAdmissionError):
        session.start()

    assert session.status == "IDLE"
    assert first.operation_registry.active_count() == 0
    assert second.operation_registry.active_count() == 0
    first.detach()
    second.detach()


def test_resume_accepting_reopens_registration_after_close_drain():
    m = TaskManager()
    m.attach()
    m.stop_accepting()
    ignored = TaskSession(name="被拒絕")
    with pytest.raises(TaskSessionAdmissionError):
        ignored.start()
    assert m.active() == []
    assert ignored.status == "IDLE"

    m.resume_accepting()
    accepted = TaskSession(name="恢復後")
    accepted.start()
    assert [task.name for task in m.active()] == ["恢復後"]
    accepted.finish()
    m.detach()


def test_abandoned_session_is_marked_interrupted(manager):
    session = TaskSession(name="被丟棄")
    session.start()
    assert manager.current() is not None
    del session
    gc.collect()
    assert manager.active() == []
    assert manager.recent()[0].status == STATUS_ERROR


def test_recent_is_bounded_and_newest_first(manager):
    for i in range(30):
        s = TaskSession(name=f"t{i}")
        s.start()
        s.finish()
    recent = manager.recent(limit=100)
    assert len(recent) == 20
    assert recent[0].name == "t29"
