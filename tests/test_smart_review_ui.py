"""智慧校對視窗（執行／審查／歷史三分頁）的 UI 行為測試。"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.views.moddb import smart_review_reviewer as reviewer_module
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_history import SmartReviewHistory, run_status_label
from app.views.moddb.smart_review_job import (
    SmartReviewJob,
    SmartReviewJobState,
    SmartReviewRequest,
    format_duration,
    job_for_page,
)
from app.views.moddb.smart_review_reviewer import (
    SmartReviewReviewer,
    candidate_evidence_text,
    not_applicable_reason,
)
from tests.conftest import mock_page
from tests.test_smart_review import _seed
from translation_tool.translation_db import SmartReviewCandidate, TranslationDB


def _sync_page():
    """建立背景工作與 UI 回呼都同步執行的測試頁面。"""
    page = mock_page()
    page.run_thread = lambda target: target()
    page.run_task = lambda coro, *args: asyncio.run(coro(*args))
    page.snacks = []
    return page


def _candidate(ordinal: int = 1, status: str = "ready", **overrides):
    """建立測試用候選。"""
    values = {
        "run_id": "run-1",
        "ordinal": ordinal,
        "entry_id": ordinal,
        "kind": "lang",
        "mc_version": "1.21.1",
        "mod_id": "demo",
        "key": f"key{ordinal}",
        "en_us": "Hello",
        "current_zh_tw": "哈囉",
        "effective_source": 0,
        "review_status": "unreviewed",
        "effective_revision": 1,
        "context": (),
        "decision": "suggest_revision",
        "suggested_zh_tw": "你好",
        "reason": "原文是問候語",
        "status": status,
    }
    values.update(overrides)
    return SmartReviewCandidate(**values)


@pytest.fixture
def db(tmp_path):
    """建立含兩筆翻譯的資料庫。"""
    database = TranslationDB(tmp_path / "ui.db")
    _seed(database)
    yield database
    database.close()


@pytest.fixture
def ctx(db):
    """建立所有背景工作都同步執行的上下文。"""
    context = SmartReviewContext(_sync_page(), lambda: db, lambda *_a: None)

    def run_now(work, on_done, *, still_valid=None, **_kwargs):
        """同步執行背景工作並維持過期結果的丟棄語意。"""
        try:
            result, error = work(), None
        except Exception as exc:  # noqa: BLE001 - mirrors production error capture
            result, error = None, str(exc)
        if context.closed or (still_valid is not None and not still_valid()):
            return True
        on_done(result, error)
        return True

    context.run_background = run_now
    return context


def test_dialog_builds_three_panels_with_defaults(db, monkeypatch):
    """驗證對話框建立三個分頁，且預設只顯示「執行」。"""
    monkeypatch.setattr(
        "app.services_impl.moddb_smart_review_service.load_config",
        lambda: {
            "lm_translator": {
                "provider": "gemini",
                "models": {"gemini-3.1-flash-lite": {"enabled": True}},
            }
        },
    )
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    assert dialog.run_panel.model_dd.value == "gemini-3.1-flash-lite"
    assert dialog.run_panel.limit_field.value == "1000"
    assert dialog.run_panel.start_btn.disabled is False
    assert dialog.nav.value == "run"
    visible = {name: control.visible for name, control in dialog._panels.items()}
    assert visible == {"run": True, "review": False, "history": False}


def test_dialog_open_review_switches_tab_and_loads_run(db, monkeypatch):
    """驗證從執行結果前往審查會切分頁並載入該批次。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    loaded = []
    dialog.reviewer.load_run = loaded.append
    dialog.open_review("run-9")
    assert loaded == ["run-9"]
    assert dialog.nav.value == "review"
    assert dialog._panels["review"].visible is True
    assert dialog._panels["run"].visible is False


def test_dialog_close_keeps_background_job_running(db):
    """驗證關閉視窗只讓 UI 結果失效，不取消背景校對。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    page = _sync_page()
    page.run_task = lambda *_a: None  # 不實際執行輪詢迴圈
    dialog = SmartReviewDialog(page, lambda: db, "1.21.1", None)
    dialog.job.state = SmartReviewJobState(phase="running")
    dialog.run_panel.attach()
    dialog._teardown()
    assert dialog.ctx.closed is True
    assert dialog.job.running is True
    assert not dialog.job._cancel_event.is_set()
    assert dialog.run_panel._on_finished not in dialog.job._finish_listeners


def test_context_rejects_stale_generation_and_old_database(tmp_path):
    """驗證世代改變或資料庫被換掉後，舊結果不會更新 UI。"""
    old_path, new_path = tmp_path / "old.db", tmp_path / "new.db"
    old_path.write_text("", encoding="utf-8")
    new_path.write_text("", encoding="utf-8")
    holder = {"db": type("DB", (), {"path": old_path})()}
    context = SmartReviewContext(_sync_page(), lambda: holder["db"], None)
    identity = context.db_identity()
    assert context.is_current(context.generation, identity)
    holder["db"] = type("DB", (), {"path": new_path})()
    assert not context.is_current(context.generation, identity)
    context.close()
    assert not context.is_current(0)


def test_context_write_callback_allows_own_generation_change(tmp_path):
    """驗證自己寫入造成的 cache generation 變更不會卡住 UI 回呼。"""
    path = tmp_path / "smart-review.db"
    path.write_text("", encoding="utf-8")

    class DB:
        """提供可被模擬遞增的 cache generation。"""

        def __init__(self):
            self.path = path
            self.generation = "1"

        def _cache_generation(self):
            """回傳目前 cache generation。"""
            return self.generation

    database = DB()
    context = SmartReviewContext(_sync_page(), lambda: database, None)
    full = context.db_identity()
    stable = context.db_identity(include_generation=False)
    database.generation = "2"
    assert not context.is_current(context.generation, full)
    assert context.is_current(context.generation, stable)


def test_context_run_background_discards_result_after_close(db):
    """驗證視窗關閉後，背景工作的結果會被丟棄。"""
    context = SmartReviewContext(_sync_page(), lambda: db, None)
    delivered = []
    started = threading.Event()
    release = threading.Event()
    context.page.run_thread = lambda target: threading.Thread(target=target).start()

    def work():
        """等待測試先關閉視窗再回傳。"""
        started.set()
        release.wait(2)
        return "late"

    context.run_background(work, lambda *a: delivered.append(a), name="t")
    started.wait(2)
    context.close()
    release.set()
    threading.Event().wait(0.3)
    assert delivered == []


def test_format_duration_and_state_estimates():
    """驗證時間格式與剩餘時間估算。"""
    assert format_duration(None) == "--:--"
    assert format_duration(75) == "01:15"
    assert format_duration(3725) == "1:02:05"
    state = SmartReviewJobState(phase="running", selected=100, processed=25)
    state.started_at = 10.0
    assert state.fraction == 0.25
    assert state.eta_seconds(now=60.0) == pytest.approx(150.0)
    assert SmartReviewJobState(phase="running").eta_seconds() is None
    assert (
        SmartReviewJobState(phase="done", selected=1, processed=1).eta_seconds() is None
    )


def _request(tmp_path) -> SmartReviewRequest:
    """建立測試用校對請求。"""
    return SmartReviewRequest(
        path=tmp_path / "x.db",
        priority=(),
        model="m",
        version="1.21.1",
        mod_id=None,
        kind=None,
        effective_source=0,
        query="",
        limit=10,
        batch_size=5,
    )


class _Result:
    """模擬核心回傳的校對結果。"""

    run_id = "run-ok"
    status = "completed"
    selected_count = 4
    processed_count = 4
    valid_count = 3
    invalid_count = 1
    request_error_count = 0
    suggestion_count = 1
    no_change_count = 2
    needs_context_count = 0


def test_job_runs_in_background_and_derives_logs(tmp_path, monkeypatch):
    """驗證任務把核心進度轉成逐批 log，結束時通知監聽者與上層。"""
    import app.views.moddb.smart_review_job as job_module

    def fake_run(*_args, progress=None, **_kwargs):
        """模擬兩批進度回報後完成。"""
        progress({"run_id": "run-ok", "selected": 4, "processed": 2, "valid": 2})
        progress(
            {
                "run_id": "run-ok",
                "selected": 4,
                "processed": 4,
                "valid": 3,
                "invalid": 1,
                "request_errors": 0,
            }
        )
        return _Result()

    monkeypatch.setattr(job_module, "run_moddb_smart_review_from_path", fake_run)
    monkeypatch.setattr(
        job_module,
        "get_moddb_smart_review_run_breakdown_from_path",
        lambda *_a, **_k: {"status": {}, "errors": [("reason 不可為空", 1)]},
    )
    page = _sync_page()
    job = SmartReviewJob(page)
    finished, parent = [], []
    job.add_finish_listener(finished.append)
    assert job.start(_request(tmp_path), parent_callback=parent.append) is None

    state = job.state
    assert state.phase == "done"
    assert (state.run_id, state.processed, state.valid, state.invalid) == (
        "run-ok",
        4,
        3,
        1,
    )
    assert finished == [state]
    assert parent == [state.result]
    texts = [entry.text for entry in job.session.snapshot()["logs"]]
    assert any("已處理 2 / 4 筆" in text for text in texts)
    assert any("已處理 4 / 4 筆" in text for text in texts)
    assert any("無效原因：reason 不可為空 × 1" in text for text in texts)
    assert job.session.progress == 1.0


def test_job_rejects_second_start_and_reports_failure(tmp_path, monkeypatch):
    """驗證執行中不能再啟動，失敗時狀態為 failed 並寫入錯誤 log。"""
    import app.views.moddb.smart_review_job as job_module

    def boom(*_a, **_k):
        """模擬核心丟出 provider 錯誤。"""
        raise RuntimeError("provider 403")

    monkeypatch.setattr(job_module, "run_moddb_smart_review_from_path", boom)
    job = SmartReviewJob(_sync_page())
    job.state = SmartReviewJobState(phase="running")
    assert job.start(_request(tmp_path)) == "智慧校對正在執行中。"
    job.state = SmartReviewJobState()
    assert job.start(_request(tmp_path)) is None
    assert job.state.phase == "failed"
    assert job.state.error == "provider 403"
    levels = [(e.level, e.text) for e in job.session.snapshot()["logs"]]
    assert ("error", "智慧校對失敗：provider 403") in levels


def test_job_cancel_sets_flags_and_core_checks_them(tmp_path, monkeypatch):
    """驗證停止要求會同時設定取消旗標，並傳給核心的 cancel_check。"""
    import app.views.moddb.smart_review_job as job_module

    seen = {}

    def fake_run(*_a, cancel_check=None, **_k):
        """在核心裡確認取消檢查函式可用。"""
        seen["before"] = cancel_check()
        job.cancel()
        seen["after"] = cancel_check()
        return _Result()

    monkeypatch.setattr(job_module, "run_moddb_smart_review_from_path", fake_run)
    monkeypatch.setattr(
        job_module,
        "get_moddb_smart_review_run_breakdown_from_path",
        lambda *_a, **_k: {},
    )
    job = SmartReviewJob(_sync_page())
    job.start(_request(tmp_path))
    assert seen == {"before": False, "after": True}
    assert job.state.cancel_requested is True


def test_job_for_page_is_shared_per_page():
    """驗證同一頁面共用同一個任務，所以重開視窗能接續狀態。"""
    page = mock_page()
    assert job_for_page(page) is job_for_page(page)
    assert job_for_page(page) is not job_for_page(mock_page())


class _FakeService:
    """可記錄呼叫的 service 替身，回傳預先指定的候選。"""

    def __init__(self, rows):
        """保存候選並初始化呼叫紀錄。"""
        self.rows = rows
        self.calls = []

    def count(self, _path, _priority, _run_id, *, status=None):
        """回傳篩選後的候選數。"""
        return len(self._filtered(status))

    def _filtered(self, status):
        """依狀態篩選候選。"""
        return [r for r in self.rows if status is None or r.status == status]

    def list(self, _path, _priority, _run_id, *, status=None, limit=None, offset=0):
        """記錄分頁參數並回傳該頁候選。"""
        self.calls.append(("list", status, limit, offset))
        rows = self._filtered(status)
        return rows[offset : offset + limit]

    def get_run(self, _path, _priority, run_id):
        """回傳批次資訊。"""
        return {"id": run_id, "provider": "gemini", "model": "m", "started_at": "t"}

    def breakdown(self, *_a, **_k):
        """回傳各狀態筆數。"""
        counts = {}
        for row in self.rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {"status": counts, "errors": []}


@pytest.fixture
def reviewer(ctx, monkeypatch):
    """建立使用替身 service 的審查面板。"""
    rows = [
        _candidate(
            1, "invalid", suggested_zh_tw=None, validation_error="reason 不可為空"
        ),
        _candidate(2, "ready"),
        _candidate(3, "ready"),
        _candidate(4, "applied"),
    ]
    fake = _FakeService(rows)
    monkeypatch.setattr(
        reviewer_module,
        "count_moddb_smart_review_candidates_for_run_from_path",
        fake.count,
    )
    monkeypatch.setattr(
        reviewer_module, "list_moddb_smart_review_candidates_from_path", fake.list
    )
    monkeypatch.setattr(
        reviewer_module, "get_moddb_smart_review_run_from_path", fake.get_run
    )
    monkeypatch.setattr(
        reviewer_module,
        "get_moddb_smart_review_run_breakdown_from_path",
        fake.breakdown,
    )
    monkeypatch.setattr(
        reviewer_module,
        "show_snack",
        lambda page, msg, *_a, **_k: page.snacks.append(msg),
    )
    panel = SmartReviewReviewer(ctx, on_changed=lambda: None)
    panel.fake = fake
    return panel


def test_reviewer_defaults_to_ready_queue_with_one_item_per_page(reviewer):
    """驗證預設只看可套用，且每頁只載入一筆（limit=1）。"""
    reviewer.load_run("run-1")
    assert reviewer.status_filter == "ready"
    assert reviewer.total == 2
    assert reviewer.row.ordinal == 2
    assert reviewer.position_text.value == "第 1 / 2 筆（可套用）"
    assert reviewer.fake.calls[-1] == ("list", "ready", 1, 0)
    labels = [option.text for option in reviewer.filter_dd.options]
    assert labels[0] == "可套用（2）"
    assert "全部（4）" in labels


def test_reviewer_next_and_previous_move_one_item_at_a_time(reviewer):
    """驗證「下一筆」就是換到下一頁，到頭時提示而不是沒反應。"""
    reviewer.load_run("run-1")
    reviewer._move(1)
    assert reviewer.row.ordinal == 3
    assert reviewer.fake.calls[-1] == ("list", "ready", 1, 1)
    assert reviewer.next_btn.disabled is True
    reviewer._move(1)
    assert reviewer.ctx.page.snacks[-1] == "已經是最後一筆。"
    reviewer._move(-1)
    assert reviewer.row.ordinal == 2
    reviewer._move(-1)
    assert reviewer.ctx.page.snacks[-1] == "已經是第一筆。"


def test_reviewer_falls_back_to_all_when_nothing_is_ready(ctx, reviewer):
    """驗證沒有可套用候選時改顯示全部，並說明不能操作的原因。"""
    reviewer.fake.rows = [_candidate(1, "invalid", validation_error="reason 不可為空")]
    reviewer.load_run("run-1")
    assert reviewer.status_filter == "all"
    assert reviewer.row.status == "invalid"
    assert reviewer.apply_btn.disabled is True
    assert reviewer.save_btn.disabled is True
    assert reviewer.reject_btn.disabled is True
    assert "AI 的回應沒有通過驗證" in reviewer.hint_text.value
    assert "reason 不可為空" in reviewer.hint_text.value


def test_reviewer_blocks_apply_when_draft_has_format_issue(reviewer, monkeypatch):
    """驗證草稿有格式問題時不能套用，避免 service 把候選永久鎖成 invalid_format。"""
    applied = []
    monkeypatch.setattr(
        reviewer_module,
        "apply_moddb_smart_review_candidate_from_path",
        lambda *a, **k: applied.append(a) or "applied",
    )

    class Diagnostics:
        """模擬格式診斷結果。"""

        def __init__(self, issues):
            self.summary_lines = issues
            self.has_issues = bool(issues)

    monkeypatch.setattr(
        reviewer_module,
        "smart_review_format_diagnostics",
        lambda _en, value, kind="lang": Diagnostics(
            ["缺少佔位符 %s"] if "%s" not in value else []
        ),
    )
    reviewer.fake.rows = [
        _candidate(1, "ready", en_us="Hello %s", suggested_zh_tw="你好 %s")
    ]
    reviewer.load_run("run-1")
    assert reviewer.apply_btn.disabled is False
    reviewer.draft.value = "你好"
    reviewer._on_draft_change()
    assert reviewer.apply_btn.disabled is True
    assert reviewer.save_btn.disabled is True
    assert "格式問題" in reviewer.hint_text.value
    reviewer._apply()
    assert applied == []
    reviewer.draft.value = "你好 %s"
    reviewer._on_draft_change()
    assert reviewer.apply_btn.disabled is False


def test_reviewer_apply_notifies_parent_and_advances_queue(reviewer, monkeypatch):
    """驗證套用成功後通知上層、更新統計並顯示佇列中的下一筆。"""
    changed = []
    reviewer.on_changed = lambda: changed.append(True)

    def fake_apply(_path, _priority, _run_id, ordinal, *, draft_zh_tw=None):
        """把候選標為已套用，模擬 service 行為。"""
        for index, row in enumerate(reviewer.fake.rows):
            if row.ordinal == ordinal:
                reviewer.fake.rows[index] = _candidate(ordinal, "applied")
        return "applied"

    monkeypatch.setattr(
        reviewer_module, "apply_moddb_smart_review_candidate_from_path", fake_apply
    )
    reviewer.load_run("run-1")
    assert reviewer.row.ordinal == 2
    reviewer._apply()
    assert changed == [True]
    assert reviewer.row.ordinal == 3
    assert reviewer.total == 1
    assert reviewer.counts["applied"] == 2
    assert "已套用 1 筆" in reviewer.ctx.page.snacks[-1]


def test_reviewer_stale_and_reject_messages(reviewer, monkeypatch):
    """驗證 stale 只提示不通知上層，保留原譯文會更新統計。"""
    changed = []
    reviewer.on_changed = lambda: changed.append(True)
    monkeypatch.setattr(
        reviewer_module,
        "apply_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "stale",
    )
    monkeypatch.setattr(
        reviewer_module,
        "reject_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "rejected",
    )
    reviewer.load_run("run-1")
    reviewer._apply()
    assert "已過期" in reviewer.ctx.page.snacks[-1]
    assert changed == []
    reviewer._reject()
    assert reviewer.ctx.page.snacks[-1] == "已保留原譯文，這筆不會套用。"


def test_reviewer_discards_stale_position_results(ctx, reviewer):
    """驗證快速連按時只有最新的位置結果會更新畫面。"""
    pending = []
    ctx.run_background = lambda work, done, *, still_valid=None, **_k: pending.append(
        (work, done, still_valid)
    )
    reviewer.run_id = "run-1"
    reviewer.status_filter = "ready"
    reviewer.position = 0
    reviewer.load_position()
    reviewer.position = 1
    reviewer.load_position()
    (_work0, _done0, valid0), (work1, done1, valid1) = pending
    assert valid0() is False
    assert valid1() is True
    done1(work1(), None)
    assert reviewer.row.ordinal == 3


def test_not_applicable_reason_explains_each_blocked_status():
    """驗證每個不能套用的狀態都有人看得懂的原因。"""
    assert not_applicable_reason(_candidate(1, "ready")) is None
    assert "已經套用" in not_applicable_reason(_candidate(1, "applied"))
    assert "已過期" in not_applicable_reason(_candidate(1, "stale"))
    invalid = _candidate(1, "invalid", validation_error="reason 不可為空")
    assert "reason 不可為空" in not_applicable_reason(invalid)


def test_candidate_evidence_text_separates_cited_from_reference():
    """驗證沒有引用證據時明說，並把參考資料另外列出而不當成 AI 證據。"""
    context = ({"id": "db:1:source:1:revision:2", "en_us": "A", "zh_tw": "甲"},)
    text = candidate_evidence_text(_candidate(1, context=context, evidence_ids=()))
    assert text.startswith("AI 未引用任何本地證據。")
    assert "非本次 AI 證據" in text
    cited = candidate_evidence_text(
        _candidate(1, context=context, evidence_ids=("db:1:source:1:revision:2",))
    )
    assert "非本次 AI 證據" not in cited
    legacy = ({"id": "db:1:source:1:revision:2", "legacy_id": "db:1"},)
    resolved = candidate_evidence_text(
        _candidate(1, context=legacy, evidence_ids=("db:1",))
    )
    assert "db:1 → db:1:source:1:revision:2" in resolved
    missing = candidate_evidence_text(
        _candidate(1, context=legacy, evidence_ids=("db:9",))
    )
    assert "找不到本地證據" in missing


def test_history_shows_interrupted_status_and_opens_run(ctx, monkeypatch):
    """驗證歷史清單把沒在跑的 running 標為可能中斷，並能開啟批次。"""
    assert run_status_label("running", active=False) == ("可能已中斷", "red")
    assert run_status_label("running", active=True) == ("執行中", "gold")
    assert run_status_label("completed", active=False)[0] == "完成"
    import app.views.moddb.smart_review_history as history_module

    runs = [
        {
            "id": f"run-{i}",
            "status": "completed",
            "provider": "gemini",
            "model": "m",
            "started_at": f"t{i}",
            "selected_count": 10,
            "processed_count": 10,
            "valid_count": 9,
            "invalid_count": 1,
            "request_error_count": 0,
            "scope": {"version": "1.21.1"},
        }
        for i in range(3)
    ]
    monkeypatch.setattr(
        history_module,
        "list_moddb_smart_review_runs_from_path",
        lambda *_a, limit=20, **_k: runs[:limit],
    )
    opened = []
    history = SmartReviewHistory(ctx, on_open=opened.append, active_run_id=lambda: None)
    history.refresh()
    assert len(history.list_view.controls) == 3
    assert history.more_btn.visible is False
    history.run_id_field.value = " run-typed "
    history._open_typed()
    assert opened == ["run-typed"]


def test_run_breakdown_counts_statuses_and_top_errors(db):
    """驗證批次統計回傳各狀態筆數與最常見的驗證失敗原因。"""
    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    run_id = db.create_smart_review_run(
        snapshots,
        provider="gemini",
        model="m",
        version="1.21.1",
        requested_limit=2,
        batch_size=2,
        scope={},
    )
    with db._tx() as conn:
        conn.execute(
            "UPDATE smart_review_candidate SET status='invalid', "
            "validation_error='reason 不可為空' WHERE run_id=? AND ordinal=1",
            (run_id,),
        )
        conn.execute(
            "UPDATE smart_review_candidate SET status='invalid', "
            "validation_error='reason 不可為空' WHERE run_id=? AND ordinal=2",
            (run_id,),
        )
    breakdown = db.smart_review_run_breakdown(run_id)
    assert breakdown["status"] == {"invalid": 2}
    assert breakdown["errors"] == [("reason 不可為空", 2)]
    assert db.smart_review_run_breakdown("missing") == {"status": {}, "errors": []}
