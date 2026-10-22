"""取消／中止的任務不得顯示成「完成」：TaskManager 終態、Dashboard KPI 與標籤一致。"""

from __future__ import annotations

import pytest

from app.services_impl import moddb_translate_report
from app.shell.task_manager import (
    STATUS_ABORTED,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    TaskInfo,
    TaskManager,
)
from app.tasks import task_session as task_session_module
from app.tasks.operation_registry import OperationDescriptor, current_operation
from app.tasks.task_session import TaskSession
from app.views.dashboard.dashboard_data import (
    build_dashboard_data,
    task_status_style,
)


@pytest.fixture
def manager():
    m = TaskManager()
    m.attach()
    yield m
    m.detach()


# ---------------------------------------------------------------- TaskManager 終態


def test_cancel_requested_session_is_recorded_as_cancelled(manager):
    session = TaskSession(name="翻譯", view_key="translation")
    session.start()
    session.request_cancel()
    session.finish()

    done = manager.recent()[0]
    assert done.status == STATUS_CANCELLED
    assert done.progress < 1.0  # 取消不會被顯示成 100%


def test_session_summary_status_maps_cancelled_and_aborted(manager):
    cancelled = TaskSession(name="甲")
    cancelled.start()
    cancelled.set_summary({"status": "CANCELLED"})
    cancelled.finish()

    aborted = TaskSession(name="乙")
    aborted.start()
    aborted.set_summary({"status": "ABORTED"})
    aborted.finish()

    by_name = {t.name: t.status for t in manager.recent()}
    assert by_name == {"甲": STATUS_CANCELLED, "乙": STATUS_ABORTED}


def test_failure_wins_over_cancel(manager):
    session = TaskSession(name="翻譯")
    session.start()
    session.request_cancel()
    session.set_error()
    session.finish()

    assert manager.recent()[0].status == STATUS_ERROR


def test_done_with_warnings_still_counts_as_done(manager):
    session = TaskSession(name="翻譯")
    session.start()
    session.set_summary({"status": "DONE_WITH_WARNINGS"})
    session.finish()

    assert manager.recent()[0].status == STATUS_DONE


def test_registry_terminal_reason_reconciles_recent_status(manager):
    """worker 正常返回但 owner 已被要求取消：registry 的 terminal_reason 是 cancelled。"""
    registry = manager.operation_registry
    session = TaskSession(name="翻譯", view_key="translation")
    session.operation_registry = registry

    def worker():
        session.start()
        current_operation().request_cancel()
        # 模擬服務吞掉取消、正常收尾
        session.finish()

    handle = registry.launch(
        worker,
        OperationDescriptor(name="翻譯", owner="translation", view_key="translation"),
        launcher=lambda target: target(),
        task_session=session,
    )

    assert handle is not None and handle.terminal_reason == "cancelled"
    recent = [t for t in manager.recent() if t.operation_id == handle.id]
    assert len(recent) == 1
    assert recent[0].status == STATUS_CANCELLED


def test_session_without_registry_uses_session_cancel_flag():
    """沒有 operation handle 的舊 session：以 session 自己的取消旗標為準。"""
    manager = TaskManager()
    manager.attach()
    try:
        session = TaskSession(name="舊流程")
        session.start()
        session.request_cancel()
        session.finish()
        assert manager.recent()[0].status == STATUS_CANCELLED
    finally:
        manager.detach()


# ---------------------------------------------------------------- Dashboard KPI


def _task(status, name="任務", started=0.0):
    return TaskInfo(
        id=id(object()), name=name, view_key=None, status=status, started_at=started
    )


def test_dashboard_kpi_separates_done_failed_and_cancelled():
    recent = [
        _task(STATUS_DONE, "完成", 1.0),
        _task(STATUS_ERROR, "失敗", 2.0),
        _task(STATUS_CANCELLED, "取消", 3.0),
        _task(STATUS_ABORTED, "中止", 4.0),
    ]

    data = build_dashboard_data(
        cache_overview=None,
        rules_count=None,
        key_snapshot=[],
        active=[],
        recent=recent,
    )

    assert data.tasks_done == 1
    assert data.tasks_failed == 1
    assert data.tasks_cancelled == 2
    assert [t.status for t in data.activity] == [
        STATUS_ABORTED,
        STATUS_CANCELLED,
        STATUS_ERROR,
        STATUS_DONE,
    ]


def test_task_status_style_never_maps_cancelled_to_done():
    """活動列與通知清單用的文字／色調：取消、中止不可落入「完成」。"""
    assert task_status_style(_task(STATUS_DONE)) == ("完成", "em")
    assert task_status_style(_task(STATUS_ERROR)) == ("失敗", "red")
    assert task_status_style(_task(STATUS_CANCELLED)) == ("已取消", "gold")
    assert task_status_style(_task(STATUS_ABORTED)) == ("已中止", "gold")


# ---------------------------------------------------------------- 標籤一致性（G-F5）


def test_task_session_and_report_status_labels_agree():
    session_labels = task_session_module.STATUS_LABELS
    report_labels = moddb_translate_report.STATUS_LABELS

    assert set(session_labels) <= set(report_labels)
    for status, label in session_labels.items():
        # 報告可在同義標籤後附上原因細節，但不能換成不同說法
        assert report_labels[status].startswith(label), status
    # 只有報告有的狀態：預覽
    assert "DRY_RUN" in report_labels and "DRY_RUN" not in session_labels
    assert "連續失敗" in report_labels["ABORTED"]
