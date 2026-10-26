"""智慧校對「執行」分頁的預估：輸入時只重算受影響的部分，更新只碰預估文字，過期查詢被中斷。

不測牆鐘時間，只測：哪些背景查詢被觸發、畫面更新的範圍、舊請求是否被中斷。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.views.moddb import smart_review_run_panel as run_panel_module
from app.views.moddb import smart_review_run_planning as planning_module
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_dialog import SmartReviewDialog
from tests.test_smart_review_ui import _sync_page, db  # noqa: F401 - fixture
from translation_tool.translation_db import ScanItem, TranslationDB

_PLAN_RESULT = {
    "selected_count": 28,
    "reused_count": 0,
    "estimated_to_process_count": 28,
    "pending_review_count": 0,
    "history_runs_consulted": 0,
}


class _Scheduler:
    """取代 Debouncer：只記錄排程，由測試決定何時執行最後一個。"""

    def __init__(self):
        self.scheduled = []

    def call(self, fn, *args):
        self.scheduled.append(lambda: fn(*args))

    def cancel(self):
        self.scheduled.clear()

    def fire(self):
        self.scheduled.pop()()


@pytest.fixture
def panel(db, monkeypatch):  # noqa: F811
    counts: list[dict] = []
    plans: list[dict] = []
    monkeypatch.setattr(
        run_panel_module,
        "count_moddb_smart_review_candidates_from_path",
        lambda _path, _priority, **kwargs: counts.append(kwargs) or 28,
    )
    monkeypatch.setattr(
        planning_module,
        "preview_moddb_smart_review_plan_from_path",
        lambda _path, _priority, **kwargs: plans.append(kwargs) or dict(_PLAN_RESULT),
    )
    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    result = dialog.run_panel
    result.counts, result.plans = counts, plans
    result._debounce = _Scheduler()
    result._plan_debounce = _Scheduler()
    result._scope_debounce = _Scheduler()
    result.refresh_estimate()  # 先有一份候選筆數與規劃
    counts.clear()
    plans.clear()
    return result


def test_estimate_updates_redraw_only_the_estimate_texts(panel, monkeypatch):
    regions: list[tuple] = []
    monkeypatch.setattr(
        run_panel_module, "update_regions", lambda *controls: regions.append(controls)
    )
    dialog_updates: list[int] = []
    monkeypatch.setattr(panel.ctx, "update", lambda: dialog_updates.append(1))

    panel.refresh_estimate()

    assert panel.counts and panel.plans
    assert regions and all(
        set(controls)
        == {panel.estimate_text, panel.plan_preview_text, panel.plan_progress_bar}
        for controls in regions
    )
    # 預估文字更新不再重畫整個視窗（它含數千個模組選項，差異計算要數十毫秒）
    assert dialog_updates == []


def test_typing_in_the_search_box_is_debounced_and_recounts_once(panel, monkeypatch):
    suppressed: list[int] = []
    monkeypatch.setattr(
        planning_module, "suppress_auto_update", lambda: suppressed.append(1)
    )
    for text in ("a", "al", "alt"):
        panel.query_field.value = text
        panel._query_changed()
    assert len(suppressed) == 3  # 每個字元都不觸發 Flet 的自動整窗更新
    assert panel.counts == [] and panel.plans == []  # 停筆前沒有任何查詢

    panel._debounce.fire()
    assert [c["query"] for c in panel.counts] == ["alt"]
    assert [p["query"] for p in panel.plans] == ["alt"]


def test_limit_and_model_changes_only_refresh_the_plan(panel):
    """上限與模型不影響候選範圍：不重數候選，只重讀歷史規劃預估。"""
    panel.limit_field.value = "10"
    panel._limit_changed()
    assert "本次上限 10" in panel.estimate_text.value  # 立刻用既有筆數更新文字
    assert panel._total == 28
    assert panel.counts == [] and panel.plans == []

    panel.model_dd.value = "other-model"
    panel._model_changed()
    panel._plan_debounce.fire()

    assert panel.counts == []  # 候選筆數沒有重算
    assert [p["limit"] for p in panel.plans] == [10]
    assert panel.plans[0]["model"] == "other-model"


def test_scope_changes_still_recount(panel):
    panel._scope_changed()
    panel._scope_debounce.fire()
    assert len(panel.counts) == 1 and len(panel.plans) == 1


def test_new_count_request_interrupts_the_previous_one(panel, monkeypatch):
    seen = []
    monkeypatch.setattr(
        run_panel_module,
        "count_moddb_smart_review_candidates_from_path",
        lambda _path, _priority, **kwargs: seen.append(kwargs["interrupt"]) or 5,
    )
    panel.refresh_estimate()
    first = seen[-1]
    assert first.cancelled is False

    panel.query_field.value = "x"
    panel._query_changed()  # 範圍又變了：舊計數的結果不會顯示，中斷它
    assert first.cancelled is True

    panel.refresh_estimate()
    assert seen[-1] is not first and seen[-1].cancelled is False


def test_plan_inputs_change_interrupts_the_running_plan(panel, monkeypatch):
    seen = []
    monkeypatch.setattr(
        planning_module,
        "preview_moddb_smart_review_plan_from_path",
        lambda _path, _priority, **kwargs: (
            seen.append(kwargs["interrupt"]) or dict(_PLAN_RESULT)
        ),
    )
    panel._refresh_plan_preview()
    running = seen[-1]
    panel._model_changed()
    assert running.cancelled is True


def test_hiding_the_dialog_interrupts_running_estimates(panel, monkeypatch):
    seen = []
    monkeypatch.setattr(
        run_panel_module,
        "count_moddb_smart_review_candidates_from_path",
        lambda _path, _priority, **kwargs: seen.append(kwargs["interrupt"]) or 5,
    )
    panel.refresh_estimate()
    panel.detach()
    assert seen[-1].cancelled is True


# ------------------------------------------------------------------ 畫面執行緒不等資料庫鎖
def test_cache_generation_nowait_matches_the_locked_version(tmp_path):
    database = TranslationDB(tmp_path / "gen.db")
    database.ingest("1.21.1", [ScanItem("lang", "m", "k.a", "A", "甲")])
    try:
        before = database.cache_generation_nowait()
        assert before == database._cache_generation()
        database.ingest("1.21.1", [ScanItem("lang", "m", "k.b", "B", "乙")])
        assert database.cache_generation_nowait() == database._cache_generation()
        assert database.cache_generation_nowait() != before
    finally:
        database.close()


def test_db_identity_does_not_wait_for_the_shared_connection_lock(db):  # noqa: F811
    """寫入或長查詢佔著共用連線的鎖時，畫面執行緒取得資料庫身分不能被擋住。"""
    context = SmartReviewContext(_sync_page(), lambda: db, None)
    expected = context.db_identity()
    holding = threading.Event()
    release = threading.Event()

    def hold_lock():
        with db._lock:
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert holding.wait(5)
    result: list[str | None] = []
    caller = threading.Thread(target=lambda: result.append(context.db_identity()))
    caller.start()
    caller.join(timeout=3)
    blocked = caller.is_alive()
    release.set()
    holder.join(5)
    caller.join(5)

    assert not blocked, "db_identity 不該等主連線的鎖"
    assert result == [expected]


def test_context_prefers_the_lock_free_generation(tmp_path):
    path = tmp_path / "x.db"
    path.write_text("", encoding="utf-8")

    class DB:
        def __init__(self):
            self.path = path
            self.calls: list[str] = []

        def cache_generation_nowait(self):
            self.calls.append("nowait")
            return "7"

        def _cache_generation(self):
            self.calls.append("locked")
            return "7"

    database = DB()
    context = SmartReviewContext(SimpleNamespace(), lambda: database, None)
    assert context.db_identity().endswith("::7")
    assert database.calls == ["nowait"]


def test_estimate_services_skip_work_for_a_cancelled_request(db):  # noqa: F811
    from app.services_impl import moddb_smart_review_service as service
    from translation_tool.translation_db.query_interrupt import (
        QueryCancelled,
        QueryInterrupt,
    )

    interrupt = QueryInterrupt()
    interrupt.cancel()
    with pytest.raises(QueryCancelled):
        service.count_moddb_smart_review_candidates_from_path(
            db.path, db.priority, version="1.21.1", interrupt=interrupt
        )
    with pytest.raises(QueryCancelled):
        service.preview_moddb_smart_review_plan_from_path(
            db.path, db.priority, version="1.21.1", interrupt=interrupt
        )


def test_estimate_services_detach_the_connection_when_done(db):  # noqa: F811
    from app.services_impl import moddb_smart_review_service as service
    from translation_tool.translation_db.query_interrupt import QueryInterrupt

    interrupt = QueryInterrupt()
    total = service.count_moddb_smart_review_candidates_from_path(
        db.path, db.priority, version="1.21.1", interrupt=interrupt
    )
    assert total >= 0
    assert (
        interrupt._conn is None
    )  # 連線關閉前已解除登記，之後的 cancel() 不碰已關閉的連線
    interrupt.cancel()
