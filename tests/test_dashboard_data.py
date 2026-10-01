"""工作台資料整理（純邏輯）。"""

from __future__ import annotations

import datetime as dt

from app.shell.task_manager import STATUS_DONE, STATUS_ERROR, STATUS_RUNNING, TaskInfo
from app.views.dashboard.dashboard_data import (
    STEP_DONE,
    STEP_FAILED,
    STEP_PENDING,
    STEP_RUNNING,
    build_cache_bars,
    build_dashboard_data,
    build_step_statuses,
    format_ago,
    format_count,
    greeting,
)
from translation_tool.core.lm_key_health import STATUS_COOLING, STATUS_OK, KeyHealth


def _task(view_key, status=STATUS_DONE, progress=1.0, started=0.0, name="任務"):
    return TaskInfo(
        id=id(object()),
        name=name,
        view_key=view_key,
        status=status,
        progress=progress,
        started_at=started,
    )


def _key(i, status=STATUS_OK):
    return KeyHealth(i, "AIza••••abcd", status, None, 0.0, 0)


def test_greeting_by_hour():
    assert greeting(dt.datetime(2026, 1, 1, 8, tzinfo=dt.UTC)) == "早安"
    assert greeting(dt.datetime(2026, 1, 1, 13, tzinfo=dt.UTC)) == "午安"
    assert greeting(dt.datetime(2026, 1, 1, 20, tzinfo=dt.UTC)) == "晚安"
    assert greeting(dt.datetime(2026, 1, 1, 2, tzinfo=dt.UTC)) == "晚安"


def test_format_count_and_ago():
    assert format_count(None) == "—"
    assert format_count(186204) == "186,204"
    assert format_ago(5) == "剛剛"
    assert format_ago(300) == "5 分鐘前"
    assert format_ago(7300) == "2 小時前"
    assert format_ago(200000) == "2 天前"


def test_cache_bars_sorted_with_relative_share():
    overview = {
        "types": {
            "lang": {"entries_count": 100},
            "md": {"entries_count": 50},
            "kubejs": {"entries_count": 0},
        }
    }
    bars = build_cache_bars(overview)
    assert [b.label for b in bars] == ["Lang 語言檔", "Markdown", "KubeJS"]
    assert [b.share for b in bars] == [1.0, 0.5, 0.0]


def test_cache_bars_handle_missing_or_empty_overview():
    assert build_cache_bars(None) == []
    assert build_cache_bars({"types": {"lang": {"entries_count": 0}}})[0].share == 0.0


def test_steps_default_to_pending():
    steps = build_step_statuses([], [])
    assert [s.status for s in steps] == [STEP_PENDING] * 5
    assert [s.key for s in steps] == ["extractor", "merge", "lm", "qc", "bundler"]


def test_steps_follow_task_history():
    running = _task("lm", STATUS_RUNNING, 0.42)
    done = _task("extractor")
    failed = _task("qc", STATUS_ERROR, 0.3)
    steps = {s.key: s for s in build_step_statuses([running], [done, failed])}
    assert steps["lm"].status == STEP_RUNNING and steps["lm"].detail == "42%"
    assert steps["extractor"].status == STEP_DONE
    assert steps["qc"].status == STEP_FAILED
    assert steps["merge"].status == STEP_PENDING


def test_running_beats_previous_result():
    steps = build_step_statuses([_task("lm", STATUS_RUNNING, 0.1)], [_task("lm")])
    assert next(s for s in steps if s.key == "lm").status == STEP_RUNNING


def test_tasks_not_tied_to_a_step_do_not_affect_steps():
    steps = build_step_statuses([], [_task("pipeline"), _task(None)])
    assert all(s.status == STEP_PENDING for s in steps)


def test_dashboard_data_assembles_everything():
    data = build_dashboard_data(
        cache_overview={
            "total_entries": 150,
            "types": {"lang": {"entries_count": 150}},
        },
        rules_count=1325,
        key_snapshot=[_key(0), _key(1, STATUS_COOLING)],
        active=[_task("lm", STATUS_RUNNING, 0.5, started=30)],
        recent=[_task("extractor", started=10), _task("qc", STATUS_ERROR, started=20)],
    )
    assert data.total_entries == 150 and data.rules_count == 1325
    assert data.keys.text == "1/2 Key" and len(data.key_rows) == 2
    assert (data.tasks_running, data.tasks_done, data.tasks_failed) == (1, 1, 1)
    # 活動由新到舊
    assert [t.view_key for t in data.activity] == ["lm", "qc", "extractor"]


def test_dashboard_data_activity_limit_and_empty_inputs():
    recent = [_task("qc", started=float(i)) for i in range(10)]
    data = build_dashboard_data(
        cache_overview=None, rules_count=None, key_snapshot=[], active=[], recent=recent
    )
    assert len(data.activity) == 6 and data.total_entries == 0
    assert data.keys.text == "未設定 Key" and data.rules_count is None
