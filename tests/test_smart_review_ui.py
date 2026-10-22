"""智慧校對視窗（執行／審查／歷史三分頁）的 UI 行為測試。"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import ClassVar

import flet as ft
import pytest

from app.views.moddb import entry_filters
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
    initial_draft_text,
    is_manual_edit_row,
    manual_edit_hint,
    not_applicable_reason,
)
from tests.conftest import mock_page
from tests.test_smart_review import _seed
from translation_tool.translation_db import (
    KIND_LANG,
    ScanItem,
    SmartReviewCandidate,
    TranslationDB,
)
from translation_tool.translation_db.schema import SRC_JAR_TW


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
    assert dialog.run_panel.batch_field.value == "20"
    assert dialog.run_panel.start_btn.disabled is False
    assert dialog.nav.value == "run"
    visible = {name: control.visible for name, control in dialog._panels.items()}
    assert visible == {"run": True, "review": False, "history": False}


def test_run_panel_reads_configured_smart_review_batch_size(db, monkeypatch):
    """驗證執行頁的批次預填值來自智慧校對設定，而不是硬編碼。"""
    from app.views.moddb import smart_review_run_panel as run_panel_module
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    monkeypatch.setattr(
        "app.services_impl.moddb_smart_review_service.load_config",
        lambda: {
            "lm_translator": {
                "provider": "gemini",
                "models": {"gemini-3.1-flash-lite": {"enabled": True}},
            }
        },
    )
    monkeypatch.setattr(
        run_panel_module,
        "get_smart_review_settings",
        lambda: {
            "default_batch_size": 12,
            "max_batch_size": 24,
            "storage_chunk_size": 300,
        },
    )

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)

    assert dialog.run_panel.batch_field.value == "12"


def test_run_panel_filters_modules_and_updates_sources_without_starting(
    db, monkeypatch
):
    """智慧校對選擇模組時更新來源，但不會自動開始任務。"""
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "jar_mod",
                "jar",
                "Jar",
                "罐",
                source=SRC_JAR_TW,
            )
        ],
    )

    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    original_scope_options = db.smart_review_scope_options
    scope_calls = []

    def scope_options(**kwargs):
        scope_calls.append(kwargs)
        return original_scope_options(**kwargs)

    monkeypatch.setattr(db, "smart_review_scope_options", scope_options)
    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    panel = dialog.run_panel
    assert len(scope_calls) == 1

    assert {option.key for option in panel.mod_dd.options} == {
        "__all__",
        "demo",
        "jar_mod",
    }
    panel.mod_dd.value = "jar_mod"
    # 這個測試頁面用 asyncio.run 立即執行 run_task；避免 debounce callback
    # 在 callback 內再次呼叫 run_background 時巢狀建立 event loop。
    panel._scope_debounce.call = lambda *_args, **_kwargs: None
    panel._on_mod()
    assert len(scope_calls) == 1

    assert [option.key for option in panel.source_dd.options] == [
        "__all__",
        str(SRC_JAR_TW),
    ]
    assert panel.job.running is False
    assert panel.start_btn.disabled is False


def test_run_panel_shows_scope_count_when_attached(db, monkeypatch):
    """驗證開啟執行分頁時會顯示目前範圍符合的條目數量。"""
    from app.views.moddb import smart_review_run_panel as run_panel_module
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    monkeypatch.setattr(
        "app.services_impl.moddb_smart_review_service.load_config",
        lambda: {
            "lm_translator": {
                "provider": "gemini",
                "models": {"gemini-3.1-flash-lite": {"enabled": True}},
            }
        },
    )
    calls = []
    monkeypatch.setattr(
        run_panel_module,
        "count_moddb_smart_review_candidates_from_path",
        lambda _path, _priority, **kwargs: calls.append(kwargs) or 28,
    )

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    dialog.run_panel.attach()

    assert calls and calls[-1]["version"] == "1.21.1"
    assert dialog.run_panel._total == 28
    assert "符合範圍的未審核 AI 譯文共 28 筆" in dialog.run_panel.estimate_text.value


def test_run_panel_treats_zero_limit_as_unlimited(db):
    """驗證上限 0 代表處理全部候選，但每批仍維持正整數。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    panel = dialog.run_panel
    panel._total = 28
    panel.limit_field.value = "0"
    panel.batch_field.value = "40"
    panel._update_estimate_text()

    request = panel._request()
    assert request.limit == 0
    assert request.batch_size == 40
    assert "上限 不限" in panel.estimate_text.value
    assert "本次將處理 28 筆" in panel.estimate_text.value


def test_run_panel_rejects_batch_size_over_safe_limit(db):
    """驗證 UI 不會讓使用者送出超過 Smart Review 安全上限的批次。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    panel = dialog.run_panel
    panel._total = 80
    panel.batch_field.value = "51"
    panel._update_estimate_text()

    assert "不可超過 50 筆" in panel.estimate_text.value
    with pytest.raises(ValueError, match="1 與 50"):
        panel._request()


def test_run_panel_does_not_refresh_page_when_progress_and_log_unchanged(db):
    """驗證 Smart Review 輪詢沒有可見變更時不會重送整頁 UI。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    page = _sync_page()
    dialog = SmartReviewDialog(page, lambda: db, "1.21.1", None)
    panel = dialog.run_panel

    before = page.updated
    panel.sync()
    first = page.updated
    panel.sync()

    assert first == before + 1
    assert page.updated == first


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
            """初始化此測試替身或服務物件所需的狀態。"""
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
    metrics: ClassVar[dict[str, float | int]] = {
        "duration_seconds": 2.5,
        "setup_seconds": 0.01,
        "selection_seconds": 0.4,
        "run_record_seconds": 0.02,
        "preparation_seconds": 0.1,
        "rate_limit_wait_seconds": 0.2,
        "api_seconds": 1.3,
        "parse_seconds": 0.05,
        "result_write_seconds": 0.04,
        "batch_count": 2,
        "request_attempts": 2,
        "retry_count": 0,
    }


def test_job_runs_in_background_and_derives_logs(tmp_path, monkeypatch):
    """驗證任務把核心進度轉成逐批 log，結束時通知監聽者與上層。"""
    import app.views.moddb.smart_review_job as job_module

    def fake_run(*_args, progress=None, **_kwargs):
        """模擬兩批進度回報後完成。"""
        progress(
            {
                "run_id": "run-ok",
                "selected": 4,
                "processed": 0,
                "valid": 0,
                "phase": "selection_complete",
                "timing": {"selection_seconds": 0.4, "run_record_seconds": 0.02},
            }
        )
        progress(
            {
                "run_id": "run-ok",
                "selected": 4,
                "processed": 0,
                "valid": 0,
                "phase": "event",
                "event": {
                    "kind": "retry_wait",
                    "batch_number": 1,
                    "batch_total": 2,
                    "smart_review_attempt": 1,
                    "retry_number": 1,
                    "retry_total": 3,
                    "wait_seconds": 30,
                    "error_kind": "service_unavailable",
                    "http_status": 503,
                    "retry_after_seconds": None,
                },
            }
        )
        for event in (
            {
                "kind": "validation_repair_start",
                "batch_number": 1,
                "batch_total": 2,
                "smart_review_attempt": 1,
                "expected_count": 1,
                "validation_errors": {"1": "reason 不可為空"},
            },
            {
                "kind": "validation_repair_attempt_start",
                "batch_number": 1,
                "batch_total": 2,
                "smart_review_attempt": 2,
                "expected_count": 1,
            },
            {
                "kind": "validation_repair_attempt_success",
                "batch_number": 1,
                "batch_total": 2,
                "smart_review_attempt": 2,
                "api_elapsed_seconds": 0.2,
                "http_status": 200,
                "expected_count": 1,
                "actual_count": 1,
            },
            {
                "kind": "validation_repair_complete",
                "batch_number": 1,
                "batch_total": 2,
                "smart_review_attempt": 2,
                "repaired_valid_count": 1,
                "repaired_invalid_count": 0,
            },
        ):
            progress(
                {
                    "run_id": "run-ok",
                    "selected": 4,
                    "processed": 0,
                    "valid": 0,
                    "phase": "event",
                    "event": event,
                }
            )
        progress(
            {
                "run_id": "run-ok",
                "selected": 4,
                "processed": 2,
                "valid": 2,
                "phase": "batch_complete",
                "timing": {
                    "batch_number": 1,
                    "ordinal_start": 1,
                    "ordinal_end": 2,
                    "preparation_seconds": 0.05,
                    "rate_limit_wait_seconds": 0.1,
                    "api_seconds": 0.6,
                    "parse_seconds": 0.02,
                    "result_write_seconds": 0.01,
                    "elapsed_seconds": 0.8,
                    "attempts": 1,
                },
            }
        )
        progress(
            {
                "run_id": "run-ok",
                "selected": 4,
                "processed": 4,
                "valid": 3,
                "invalid": 1,
                "request_errors": 0,
                "phase": "batch_complete",
                "timing": {
                    "batch_number": 2,
                    "ordinal_start": 3,
                    "ordinal_end": 4,
                    "preparation_seconds": 0.05,
                    "rate_limit_wait_seconds": 0.1,
                    "api_seconds": 0.7,
                    "parse_seconds": 0.03,
                    "result_write_seconds": 0.03,
                    "elapsed_seconds": 0.9,
                    "attempts": 1,
                },
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
    assert any("範圍搜尋完成" in text for text in texts)
    assert any(
        "[智慧校對][批次 1/2][第 1 次嘗試]" in text
        and "第 1/3 次重試" in text
        and "等待 30s" in text
        for text in texts
    )
    assert any("驗證修復開始" in text for text in texts)
    assert any("驗證修復 API 成功" in text for text in texts)
    assert any("驗證修復完成：有效 +1" in text for text in texts)
    assert any("耗時拆解" in text and "API" in text for text in texts)
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

    def page(
        self,
        _path,
        _priority,
        _run_id,
        *,
        status=None,
        limit=None,
        offset=0,
        known_total=None,
    ):
        """記錄一次總數與候選頁查詢，模擬共用唯讀連線。"""
        rows = self._filtered(status)
        total = len(rows) if known_total is None else known_total
        self.calls.append(("page", status, limit, offset, known_total))
        return total, rows[offset : offset + limit]

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
        "load_moddb_smart_review_candidate_page_from_path",
        fake.page,
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
    assert reviewer.position_input.value == "1"
    assert reviewer.position_input.text_align == ft.TextAlign.CENTER
    assert reviewer.position_suffix.value == " / 2 筆（可套用）"
    assert reviewer.fake.calls[-1] == ("page", "ready", 1, 0, None)
    labels = [option.text for option in reviewer.filter_dd.options]
    assert labels[0] == "可套用（2）"
    assert "全部（4）" in labels


def test_reviewer_next_and_previous_move_one_item_at_a_time(reviewer):
    """驗證「下一筆」就是換到下一頁，到頭時提示而不是沒反應。"""
    reviewer.load_run("run-1")
    reviewer._move(1)
    assert reviewer.row.ordinal == 3
    assert reviewer.fake.calls[-1] == ("page", "ready", 1, 1, 2)
    assert reviewer.next_btn.disabled is True
    reviewer._move(1)
    assert reviewer.ctx.page.snacks[-1] == "已經是最後一筆。"
    reviewer._move(-1)
    assert reviewer.row.ordinal == 2
    reviewer._move(-1)
    assert reviewer.ctx.page.snacks[-1] == "已經是第一筆。"


def test_reviewer_accepts_manual_position_and_reuses_known_total(reviewer):
    """驗證可輸入筆次跳轉，且換筆時沿用已取得的篩選總數。"""
    reviewer.load_run("run-1")
    reviewer.status_filter = "all"
    reviewer.position = 0
    reviewer.load_position()

    reviewer.position_input.value = "3"
    reviewer._on_position_submit(SimpleNamespace(control=reviewer.position_input))

    assert reviewer.row.ordinal == 3
    assert reviewer.position_input.value == "3"
    assert reviewer.position_suffix.value == " / 4 筆（全部）"
    assert reviewer.fake.calls[-1] == ("page", None, 1, 2, 4)


def test_reviewer_ignores_navigation_while_position_is_loading(reviewer):
    """驗證背景載入期間連點不會再排入下一個位置查詢。"""
    reviewer.load_run("run-1")
    reviewer.set_busy(True)

    reviewer._move(1)

    assert reviewer.position == 0
    assert reviewer.row.ordinal == 2
    assert reviewer.next_btn.disabled is True
    assert reviewer.position_input.disabled is True


def test_reviewer_falls_back_to_all_when_nothing_is_ready(ctx, reviewer):
    """驗證沒有可套用候選時改顯示全部，無效的筆改為人工編輯並說明做法。"""
    reviewer.fake.rows = [
        _candidate(
            1,
            "invalid",
            suggested_zh_tw=None,
            validation_error="reason 不可為空",
        )
    ]
    reviewer.load_run("run-1")
    assert reviewer.status_filter == "all"
    assert reviewer.row.status == "invalid"
    assert reviewer.draft.read_only is False
    assert reviewer.draft.value == "哈囉"
    assert reviewer.apply_btn.disabled is False
    assert reviewer.apply_btn.content == "套用修改"
    assert reviewer.save_btn.disabled is True
    assert reviewer.reject_btn.disabled is True
    assert "AI 的回應沒有通過驗證" in reviewer.hint_text.value
    assert "reason 不可為空" in reviewer.hint_text.value


def test_manual_edit_helpers_choose_draft_and_hint_per_status():
    """已套用以目前資料庫值為草稿起點；其他可編輯狀態依序取草稿、建議、現況。"""
    applied = _candidate(1, "applied", draft_zh_tw="舊草稿", current_zh_tw="已寫入")
    assert initial_draft_text(applied) == "已寫入"
    no_change = _candidate(1, "no_change", suggested_zh_tw=None)
    assert initial_draft_text(no_change) == "哈囉"
    assert initial_draft_text(_candidate(1, "no_change", draft_zh_tw="草稿")) == "草稿"
    assert initial_draft_text(_candidate(1, "ready")) == "你好"
    assert is_manual_edit_row(applied) and not is_manual_edit_row(_candidate(1))
    assert "重新套用" in manual_edit_hint(applied)
    assert "換筆後不會保留" in manual_edit_hint(no_change)


def test_reviewer_reapplies_applied_candidate_with_manual_edit(reviewer, monkeypatch):
    """已套用的筆可編輯後「重新套用」，走人工編輯路徑並通知上層重載條目。"""
    calls = []
    changed = []
    reviewer.on_changed = lambda: changed.append(True)
    monkeypatch.setattr(
        reviewer_module,
        "realign_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "unchanged",
    )
    monkeypatch.setattr(
        reviewer_module,
        "apply_moddb_smart_review_manual_edit_from_path",
        lambda _p, _pr, run_id, ordinal, draft: (
            calls.append((run_id, ordinal, draft)) or "applied"
        ),
    )
    monkeypatch.setattr(
        reviewer_module,
        "apply_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: pytest.fail("已套用的筆不應走 AI 建議套用路徑"),
    )
    reviewer.load_run("run-1")
    reviewer.status_filter = "applied"
    reviewer.position = 0
    reviewer.load_position()
    assert reviewer.row.status == "applied"
    assert reviewer.draft.read_only is False
    assert reviewer.apply_btn.content == "重新套用"
    assert reviewer.apply_btn.disabled is False

    reviewer.draft.value = "修正後的譯文"
    reviewer._on_draft_change()
    reviewer._apply()

    assert calls == [("run-1", 4, "修正後的譯文")]
    assert changed == [True]
    assert "已套用人工修改" in reviewer.ctx.page.snacks[-1]


def test_reviewer_manual_edit_reports_changed_snapshot_without_notifying(
    reviewer, monkeypatch
):
    """資料庫已被他處修改時只提示並重載，不通知上層也不覆蓋。"""
    changed = []
    reviewer.on_changed = lambda: changed.append(True)
    monkeypatch.setattr(
        reviewer_module,
        "realign_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "unchanged",
    )
    monkeypatch.setattr(
        reviewer_module,
        "apply_moddb_smart_review_manual_edit_from_path",
        lambda *_a, **_k: "changed",
    )
    reviewer.load_run("run-1")
    reviewer.status_filter = "applied"
    reviewer.load_position()
    reviewer._apply()
    assert "已變更" in reviewer.ctx.page.snacks[-1]
    assert changed == []


def test_reviewer_blocks_manual_apply_when_draft_is_blank(reviewer, monkeypatch):
    """人工編輯模式下空白草稿不能套用。"""
    monkeypatch.setattr(
        reviewer_module,
        "realign_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "unchanged",
    )
    reviewer.load_run("run-1")
    reviewer.status_filter = "applied"
    reviewer.load_position()
    reviewer.draft.value = "  "
    reviewer._on_draft_change()
    assert reviewer.apply_btn.disabled is True


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
            """初始化此測試替身或服務物件所需的狀態。"""
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


def test_reviewer_keeps_invalid_unsaved_draft_when_navigating(reviewer, monkeypatch):
    """格式不符的未保存草稿也必須阻止換筆，避免繞過保存失敗保護。"""

    class Diagnostics:
        """模擬格式診斷結果。"""

        def __init__(self, issues):
            """初始化診斷結果。"""
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
        _candidate(1, "ready", en_us="Hello %s", suggested_zh_tw="你好 %s"),
        _candidate(2, "ready", en_us="Bye", suggested_zh_tw="再見"),
    ]
    reviewer.load_run("run-1")
    reviewer.draft.value = "尚未補回之前的草稿"
    reviewer._on_draft_change()

    reviewer._move(1)

    assert reviewer.row.ordinal == 1
    assert reviewer.draft.value == "尚未補回之前的草稿"
    assert "尚未保存" in reviewer.ctx.page.snacks[-1]


def test_smart_review_completion_refreshes_entry_list_before_parent_changed(
    monkeypatch,
):
    """驗證智慧校對完成後先更新條目清單，再通知外層狀態已變更。"""
    events = []
    panel = SimpleNamespace(
        _page=mock_page(),
        drafts={},
        version="1.21.1",
        mod_id=None,
        kind=None,
        source_filter=SimpleNamespace(code="all"),
        query="",
        db=lambda: object(),
        _on_changed=lambda: events.append(("parent",)),
    )

    monkeypatch.setattr(
        entry_filters,
        "refresh_entry_filter",
        lambda target, **kwargs: events.append(("refresh", target, kwargs)),
    )

    class FakeSmartReviewDialog:
        """在測試中立即執行完成回呼。"""

        def __init__(self, _page, _get_db, _version, on_complete, *_args):
            """初始化此測試替身或服務物件所需的狀態。"""
            self.on_complete = on_complete

        def open(self):
            """立即執行完成 callback，模擬對話框開啟後完成智慧校對。"""
            self.on_complete()

    monkeypatch.setattr(entry_filters, "SmartReviewDialog", FakeSmartReviewDialog)

    entry_filters.open_smart_review(panel)

    assert events[0] == ("refresh", panel, {"keep_selection": True})
    assert events[1] == ("parent",)


def test_bulk_review_prefills_entry_scope_without_running_preview(monkeypatch):
    """驗證批量審核只預填目前範圍，不會在開啟時自動預覽。"""
    captured = {}
    panel = SimpleNamespace(
        _page=mock_page(),
        drafts={},
        version="1.21.1",
        mod_id="accessories",
        source_filter=SimpleNamespace(code=7),
        db=lambda: object(),
        _after_batch_replace=lambda: None,
    )

    class FakeBulkReviewDialog:
        """記錄批量審核開啟時收到的預填值。"""

        def __init__(self, *_args, **kwargs):
            """保存建構參數，模擬尚未產生預覽的對話框。"""
            captured.update(kwargs)
            self.previewed = False

        def open(self):
            """開啟替身，不觸發預覽或執行。"""
            captured["opened"] = True

    monkeypatch.setattr(entry_filters, "BulkReviewDialog", FakeBulkReviewDialog)

    entry_filters.open_bulk_review(panel)

    assert captured == {
        "mod_id": "accessories",
        "effective_source": 7,
        "opened": True,
    }


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


def test_reviewer_keeps_current_draft_when_auto_save_fails(reviewer, monkeypatch):
    """驗證自動保存失敗時不會換筆而遺失目前草稿。"""
    monkeypatch.setattr(
        reviewer_module,
        "save_moddb_smart_review_draft_from_path",
        lambda *_a, **_k: "stale",
    )
    reviewer.load_run("run-1")
    current_ordinal = reviewer.row.ordinal
    reviewer.draft.value = "尚未保存的草稿"
    reviewer._on_draft_change()

    reviewer._move(1)

    assert reviewer.row.ordinal == current_ordinal
    assert reviewer.draft.value == "尚未保存的草稿"


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


def test_candidate_evidence_text_distinguishes_official_and_module_references():
    """未引用時分開顯示官方完整翻譯、官方術語與模組低信心參考。"""
    context = (
        {
            "id": "official-term:full",
            "reference_type": "official_locale",
            "match_type": "exact_key",
            "en_us": "Diamond Axe",
            "zh_tw": "鑽石斧",
            "confidence": "high",
        },
        {
            "id": "official-term:term",
            "reference_type": "official_locale",
            "match_type": "source_substring",
            "en_us": "Netherite",
            "zh_tw": "獄髓",
            "confidence": "high",
        },
        {
            "id": "module-locale:1",
            "reference_type": "module_locale",
            "match_type": "same_entry_unreviewed",
            "en_us": "Diamond Axe",
            "zh_tw": "模組斧",
            "confidence": "low",
        },
    )

    text = candidate_evidence_text(_candidate(1, context=context, evidence_ids=()))

    assert "AI 未引用任何本地證據。" in text
    assert "官方完整翻譯參考：" in text
    assert "官方術語參考：" in text
    assert "未審核模組翻譯（低信心，非官方標準）" in text


def test_history_shows_interrupted_status_and_opens_run(ctx, monkeypatch):
    """驗證歷史清單把沒在跑的 running 標為可能中斷，並能開啟批次。"""
    assert run_status_label("running", active=False) == ("可能已中斷", "red")
    assert run_status_label("running", active=True) == ("執行中", "gold")
    assert run_status_label("interrupted", active=False) == ("意外中斷", "red")
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
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    history = SmartReviewHistoryStore(db.path)
    history.update_candidate(
        run_id, 1, status="invalid", validation_error="reason 不可為空"
    )
    history.update_candidate(
        run_id, 2, status="invalid", validation_error="reason 不可為空"
    )
    breakdown = db.smart_review_run_breakdown(run_id)
    assert breakdown["status"] == {"invalid": 2}
    assert breakdown["errors"] == [("reason 不可為空", 2)]
    assert db.smart_review_run_breakdown("missing") == {"status": {}, "errors": []}
