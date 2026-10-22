"""快取查詢序號：舊請求失敗不得蓋掉新請求的狀態，序號在建構時就初始化。"""

from __future__ import annotations

import asyncio

from app.views.cache_view import CacheView
from tests.conftest import mock_page


def _view_with_captured_tasks():
    page = mock_page()
    tasks = []
    page.run_task = lambda coro, *args: tasks.append(coro)
    view = CacheView(page)
    notes = []
    view._notify = lambda message, kind="info": notes.append((message, kind))
    view._apply_query_results = lambda results: notes.append(("results", results))
    view.update = lambda *a, **k: None
    view.query_search_hint.update = lambda *a, **k: None
    view.tf_query_input.value = "iron"
    view.dd_query_type.value = "lang"
    view._last_overview_data = {}
    return view, tasks, notes


def test_query_seq_is_initialised_in_constructor():
    view = CacheView(mock_page())
    assert view._query_seq == 0


def test_stale_search_failure_does_not_override_newer_search():
    view, tasks, notes = _view_with_captured_tasks()
    outcomes = iter([RuntimeError("old failed"), [{"key": "new"}]])

    def compute(query, mode, targets):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    view._compute_query_results = compute
    view._on_query_search(None)  # seq 1：會失敗
    view._on_query_search(None)  # seq 2：成功
    assert len(tasks) == 2

    # 舊請求失敗：已被新請求取代，不通知也不改狀態
    asyncio.run(tasks[0]())
    assert notes == []
    asyncio.run(tasks[1]())
    assert notes == [("results", [{"key": "new"}])]


def test_current_search_failure_is_reported_and_clears_searching_hint():
    view, tasks, notes = _view_with_captured_tasks()

    def compute(query, mode, targets):
        raise RuntimeError("disk gone")

    view._compute_query_results = compute
    view._on_query_search(None)
    asyncio.run(tasks[0]())

    assert notes == [("搜尋失敗：disk gone", "error")]
    assert "搜尋中" not in view.query_search_hint.value
