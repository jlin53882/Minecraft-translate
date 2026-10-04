"""TaskManager：從 TaskSession 全域事件追蹤進行中 / 最近完成的任務。"""

from __future__ import annotations

import gc

import pytest

from app.shell.task_manager import (
    DEFAULT_TASK_NAME,
    STATUS_DONE,
    STATUS_ERROR,
    TaskManager,
)
from app.tasks.task_session import TaskSession


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
    assert len(calls) == 3
    unsubscribe()
    session.start()
    assert len(calls) == 3
    session.finish()


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


def test_resume_accepting_reopens_registration_after_close_drain():
    m = TaskManager()
    m.attach()
    m.stop_accepting()
    ignored = TaskSession(name="被拒絕")
    ignored.start()
    assert m.active() == []

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
