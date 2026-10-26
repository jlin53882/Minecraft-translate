"""本次任務的 KPI：TaskManager 全場次統計、工作台步驟完成數、任務清單與頂列膠囊。"""

from __future__ import annotations

import pytest

from app.shell import task_manager as task_manager_module
from app.shell.task_list_dialog import (
    FILTER_ALL,
    FILTER_CANCELLED,
    FILTER_DONE,
    FILTER_FAILED,
    FILTER_RUNNING,
    build_task_rows,
    format_duration,
    status_counts,
)
from app.shell.task_manager import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_RUNNING,
    TaskInfo,
    TaskManager,
)
from app.shell.topbar import TaskPill, TopBar
from app.tasks.task_session import TaskSession
from app.views.dashboard.dashboard_data import (
    STEP_PENDING,
    STEP_RUNNING,
    build_dashboard_data,
)


@pytest.fixture
def manager():
    m = TaskManager()
    m.attach()
    yield m
    m.detach()


def _run(name: str, view_key: str | None = "lm", *, error: bool = False) -> None:
    session = TaskSession(name=name, view_key=view_key)
    session.start()
    if error:
        session.set_error()
    session.finish()


def _task(view_key, status=STATUS_DONE, progress=1.0, started=0.0, name="任務"):
    return TaskInfo(
        id=id(object()),
        name=name,
        view_key=view_key,
        status=status,
        progress=progress,
        started_at=started,
    )


def test_finished_counts_cover_the_whole_session_not_the_recent_list(manager):
    for i in range(25):
        _run(f"翻譯 {i}")
    _run("失敗", error=True)
    assert len(manager.recent()) == 8  # 通知選單仍只列最近幾筆
    assert len(manager.history()) == 26
    assert manager.finished_counts() == {STATUS_DONE: 25, STATUS_ERROR: 1}


def test_counts_keep_tasks_pushed_out_of_the_history(monkeypatch):
    monkeypatch.setattr(task_manager_module, "HISTORY_LIMIT", 3)
    small = TaskManager()
    small.attach()
    try:
        for i in range(5):
            _run(f"任務 {i}")
        _run("壞掉", error=True)
        assert [t.name for t in small.history()] == ["壞掉", "任務 4", "任務 3"]
        assert small.finished_counts() == {STATUS_DONE: 5, STATUS_ERROR: 1}
    finally:
        small.detach()


def test_dashboard_counts_use_finished_counts_and_steps_count_completed_runs():
    history = [_task("extractor"), _task("extractor"), _task("qc", STATUS_ERROR)]
    data = build_dashboard_data(
        cache_overview=None,
        rules_count=None,
        key_snapshot=[],
        active=[_task("lm", STATUS_RUNNING, 0.3, started=5)],
        recent=history,
        finished_counts={STATUS_DONE: 40, STATUS_ERROR: 2, STATUS_CANCELLED: 1},
    )
    assert (data.tasks_done, data.tasks_failed, data.tasks_cancelled) == (40, 2, 1)
    assert data.tasks_total == 44
    steps = {s.key: s for s in data.steps}
    assert steps["extractor"].status == STEP_PENDING
    assert steps["extractor"].runs_done == 2
    assert steps["qc"].runs_done == 0  # 失敗不算完成
    assert steps["lm"].status == STEP_RUNNING
    assert data.steps_done == 1  # 「本次完成 1 / 5」


def test_dashboard_marks_cache_unknown_when_cache_failed_to_load():
    failed = build_dashboard_data(
        cache_overview={"total_entries": 0, "types": {}, "initialized": False},
        rules_count=None,
        key_snapshot=[],
        active=[],
        recent=[],
    )
    assert failed.cache_ready is False
    loaded = build_dashboard_data(
        cache_overview={"total_entries": 0, "types": {}, "initialized": True},
        rules_count=None,
        key_snapshot=[],
        active=[],
        recent=[],
    )
    assert loaded.cache_ready is True


def test_task_rows_list_running_first_and_filter_by_status():
    active = [
        _task("lm", STATUS_RUNNING, 0.0, started=10, name="準備"),
        _task("moddb", STATUS_RUNNING, 0.5, started=20, name="校對"),
    ]
    history = [
        _task("extractor", STATUS_DONE, started=5, name="提取"),
        _task("qc", STATUS_ERROR, started=3, name="QC"),
        _task("merge", STATUS_CANCELLED, started=1, name="合併"),
    ]
    rows = build_task_rows(active, history, FILTER_ALL, now=30)
    assert [r.name for r in rows] == ["校對", "準備", "提取", "QC", "合併"]
    assert rows[0].status_text == "進行中 50%"
    assert rows[1].status_text == "準備中"
    assert rows[2].page_label == "JAR 提取"
    assert [r.name for r in build_task_rows(active, history, FILTER_RUNNING)] == [
        "校對",
        "準備",
    ]
    assert [r.name for r in build_task_rows(active, history, FILTER_DONE)] == ["提取"]
    assert [r.name for r in build_task_rows(active, history, FILTER_FAILED)] == ["QC"]
    assert [r.name for r in build_task_rows(active, history, FILTER_CANCELLED)] == [
        "合併"
    ]
    counts = status_counts(active, {STATUS_DONE: 7, STATUS_ERROR: 1})
    assert counts[FILTER_RUNNING] == 2 and counts[FILTER_ALL] == 10


def test_format_duration():
    assert format_duration(12) == "12 秒"
    assert format_duration(185) == "3 分 05 秒"
    assert format_duration(3720) == "1 小時 02 分"


def test_task_pill_shows_other_running_tasks_and_opens_the_list(manager):
    opened: list = []
    lists: list = []
    pill = TaskPill(on_click=opened.append, on_list_click=lambda: lists.append(1))
    first = TaskSession(name="Mod 資料庫智慧校對", view_key="moddb")
    first.start()
    pill.set_task(manager.current())
    # 還沒回報進度：不定進度條配「準備中」，不顯示 0%
    assert pill.percent.value == "準備中" and pill.bar.value is None
    pill.on_click(None)
    assert opened == ["moddb"] and lists == []

    second = TaskSession(name="機器翻譯", view_key="lm")
    second.start()
    active = manager.active()
    pill.set_task(active[-1], others=len(active) - 1)
    assert pill.name.value == "機器翻譯　＋1"
    pill.on_click(None)
    assert lists == [1] and opened == ["moddb"]
    first.finish()
    second.finish()


def test_topbar_notification_menu_links_to_the_full_task_list():
    lists: list = []
    bar = TopBar(on_task_list_click=lambda: lists.append(1))
    bar.set_recent([])
    last = bar.bell_menu.items[-1]
    last.on_click(None)
    assert lists == [1]


class _DialogPage:
    width, height = 1280, 900

    def __init__(self):
        self.shown: list = []
        self.overlay: list = []

    def show_dialog(self, dialog):
        dialog.open = True
        self.shown.append(dialog)

    def pop_dialog(self):
        return None

    def update(self):
        return None


def test_task_list_dialog_lists_session_tasks_and_navigates(manager):
    from app.shell.task_list_dialog import TaskListDialog

    _run("提取資源", "extractor")
    _run("QC", "qc", error=True)
    page = _DialogPage()
    went: list = []
    dialog = TaskListDialog(page, manager, on_navigate=went.append)
    dialog.open()
    assert dialog.is_open and page.shown
    assert dialog.summary.value == "進行中 0・完成 1・失敗 1・已取消 0"
    assert len(dialog.list_view.controls) == 2
    dialog.filter_bar.select(FILTER_FAILED, notify=True)
    assert len(dialog.list_view.controls) == 1
    dialog.list_view.controls[0].on_click(None)
    assert went == ["qc"] and not dialog.is_open


def test_dashboard_task_card_opens_the_task_list():
    from app.views.dashboard_view import DashboardView

    class _Shell:
        def __init__(self):
            self.tasks = TaskManager()
            self.opened = 0

        def navigate(self, _key):
            return None

        def open_task_list(self):
            self.opened += 1

    shell = _Shell()
    view = DashboardView(
        _DialogPage(),
        cache_overview_loader=lambda: {},
        rules_count_loader=lambda: 0,
        key_snapshot_loader=lambda: [],
        model_quota_loader=lambda: [],
        moddb_loader=lambda: None,
    )
    view.set_shell(shell)
    view.stat_tasks.on_click(None)
    assert shell.opened == 1
