"""智慧校對視窗（執行／審查／歷史三分頁）的 UI 行為測試。"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from typing import ClassVar

import flet as ft
import pytest

from app.views.moddb import entry_filters
from app.views.moddb import smart_review_actions as actions_module
from app.views.moddb import smart_review_batch_reviewer as batch_reviewer_module
from app.views.moddb import smart_review_candidate_loader as loader_module
from app.views.moddb import smart_review_evidence as evidence_module
from app.views.moddb import smart_review_reviewer as reviewer_module
from app.views.moddb.smart_review_batch_reviewer import SmartReviewBatchReviewer
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_evidence import (
    SmartReviewEvidencePresenter,
    initial_draft_text,
    is_manual_edit_row,
    manual_edit_hint,
    not_applicable_reason,
)
from app.views.moddb.smart_review_history import SmartReviewHistory
from app.views.moddb.smart_review_job import (
    SmartReviewJob,
    SmartReviewJobState,
    SmartReviewRequest,
    format_duration,
    job_for_page,
)
from app.views.moddb.smart_review_reviewer import SmartReviewReviewer
from app.views.moddb.smart_review_run_status import run_status_label
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


def test_batch_reviewer_paginates_and_preserves_selection_across_pages(
    db, ctx, monkeypatch
):
    """批量審查跨頁勾選時保留前一頁的選取。"""
    rows = [_candidate(index) for index in range(1, 31)]

    breakdown_calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_run(self, _run_id):
            return {
                "id": "run-1",
                "provider": "gemini",
                "model": "test",
                "scope_version": "1.21.1",
                "started_at": "now",
            }

        def run_breakdown(self, _run_id):
            breakdown_calls.append(True)
            return {"status": {"ready": len(rows)}}

        def candidate_page(self, _run_id, *, status, limit, offset, **_kwargs):
            selected = [row for row in rows if status is None or row.status == status]
            return len(selected), selected[offset : offset + limit]

    monkeypatch.setattr(
        batch_reviewer_module,
        "moddb_smart_review_read_session",
        lambda *_args, **_kwargs: Session(),
    )
    reviewer = SmartReviewBatchReviewer(ctx, on_changed=lambda: None)

    reviewer.load_run("run-1")
    assert reviewer.pager.total_items == 30
    assert reviewer.pager.total_pages == 2
    reviewer._select_page()
    assert len(reviewer._selected) == 25

    reviewer._on_page(2)
    reviewer._select_page()

    assert reviewer.page == 2
    assert len(reviewer.rows) == 5
    assert len(reviewer._selected) == 30
    assert reviewer.apply_btn.disabled is False
    assert len(breakdown_calls) == 1


def test_batch_reviewer_page_navigation_runs_inline(db, ctx, monkeypatch):
    """批量審查換頁直接更新畫面，不顯示背景載入狀態。"""
    rows = [_candidate(index) for index in range(1, 31)]

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_run(self, _run_id):
            return {"id": "run-1"}

        def run_breakdown(self, _run_id):
            return {"status": {"ready": len(rows)}}

        def candidate_page(self, _run_id, *, status, limit, offset, **_kwargs):
            selected = [row for row in rows if status is None or row.status == status]
            return len(selected), selected[offset : offset + limit]

    monkeypatch.setattr(
        batch_reviewer_module,
        "moddb_smart_review_read_session",
        lambda *_args, **_kwargs: Session(),
    )
    background_calls = []
    original_run_background = ctx.run_background

    def record_background(*args, **kwargs):
        background_calls.append(kwargs.get("name"))
        return original_run_background(*args, **kwargs)

    ctx.run_background = record_background
    reviewer = SmartReviewBatchReviewer(ctx, on_changed=lambda: None)
    reviewer.load_run("run-1")
    calls_after_initial_load = list(background_calls)

    reviewer._on_page(2)

    assert background_calls == calls_after_initial_load
    assert reviewer.page == 2
    assert [row.ordinal for row in reviewer.rows] == list(range(26, 31))


def test_batch_candidate_header_separates_wrapped_chips_from_text(ctx):
    """批量候選標題不可把 expand 文字放進可換行 Row。"""
    reviewer = SmartReviewBatchReviewer(ctx, on_changed=lambda: None)

    card = reviewer._candidate_card(_candidate())
    details = card.content.controls[1]
    header = details.controls[0]

    assert isinstance(header, ft.Column)
    assert isinstance(header.controls[1], ft.Row)
    assert header.controls[1].wrap is True
    assert details.controls[4].controls[1].value == "原文是問候語"
    assert details.controls[5].controls[1].value == "未標記"


def test_single_reviewer_places_ai_reason_before_editing_columns(ctx):
    """逐筆檢視先顯示 AI 理由，再顯示原文與人工編輯區。"""
    reviewer = SmartReviewReviewer(ctx, on_changed=lambda: None)

    columns = reviewer._columns(_candidate())
    explanation = columns.controls[0]
    explanation_texts = explanation.content.controls

    assert explanation_texts[1].value == "原文是問候語"
    assert "問題類型：未標記" == explanation_texts[2].value


def test_batch_reviewer_exposes_expired_override_action(ctx):
    """已過期候選可選取並顯示獨立覆蓋動作，不會混入一般套用或保留。"""
    reviewer = SmartReviewBatchReviewer(ctx, on_changed=lambda: None)
    stale = _candidate(status="stale")
    reviewer._row_cache[stale.ordinal] = stale
    reviewer._selected.add(stale.ordinal)
    reviewer._update_action_buttons()

    assert reviewer._can_select(stale) is True
    assert reviewer._can_override(stale) is True
    assert reviewer.apply_btn.disabled is True
    assert reviewer.reject_btn.disabled is True
    assert reviewer.override_btn.disabled is False
    assert reviewer.override_btn.content == "批量覆蓋已過期（1）"

    checkbox = reviewer._candidate_card(stale).content.controls[0]
    assert checkbox.disabled is False


def test_dialog_switches_between_single_and_batch_review_modes(db, monkeypatch):
    """智慧校對審查分頁提供逐筆／批量模式切換而不重建外層分頁。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    monkeypatch.setattr(
        "app.services_impl.moddb_smart_review_service.load_config",
        lambda: {
            "lm_translator": {
                "provider": "gemini",
                "models": {"test": {"enabled": True}},
            }
        },
    )
    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)

    assert dialog.reviewer.control.visible is True
    assert dialog.batch_reviewer.control.visible is False
    dialog._on_review_mode("batch")
    assert dialog.reviewer.control.visible is False
    assert dialog.batch_reviewer.control.visible is True


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


def test_run_panel_accepts_a_custom_model_input(db, monkeypatch):
    """執行頁模型欄位可輸入設定清單以外的模型名稱。"""
    monkeypatch.setattr(
        "app.services_impl.moddb_smart_review_service.load_config",
        lambda: {
            "lm_translator": {
                "provider": "gemini",
                "models": {"gemini-configured": {"enabled": True}},
            }
        },
    )
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    dialog.run_panel.model_dd.value = "gemini-custom-model"

    assert dialog.run_panel._request().model == "gemini-custom-model"


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
    assert "歷史重用後實際送 AI" in dialog.run_panel.estimate_text.value
    assert "每批 20 筆" in dialog.run_panel.estimate_text.value


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
    assert "本次上限 不限" in panel.estimate_text.value
    assert "約 1 批（每批 40 筆）" in panel.estimate_text.value


def test_run_panel_uses_planned_ai_count_for_batch_estimate(db):
    """預估行的批次數量必須使用歷史重用後的實際 AI 數量。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    panel = dialog.run_panel
    panel._total = 60_186
    panel.limit_field.value = "0"
    panel.batch_field.value = "100"
    panel._plan_preview_result = {
        "selected_count": 60_186,
        "reused_count": 763,
        "actual_ai_count": 59_423,
        "pending_review_count": 127,
        "history_runs_consulted": 1,
    }
    panel._update_estimate_text()

    assert "符合範圍 60,186 筆" in panel.estimate_text.value
    assert "實際送 AI 59,423 筆" in panel.estimate_text.value
    assert "約 595 批（每批 100 筆）" in panel.estimate_text.value


def test_run_panel_rejects_batch_size_over_safe_limit(db, monkeypatch):
    """驗證 UI 不會讓使用者送出超過 Smart Review 安全上限的批次。"""
    from app.views.moddb import smart_review_run_panel as run_panel_module
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    monkeypatch.setattr(
        run_panel_module,
        "get_smart_review_settings",
        lambda: {
            "default_batch_size": 20,
            "max_batch_size": 50,
            "storage_chunk_size": 200,
        },
    )
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
    batch_loaded = []
    dialog.reviewer.load_run = loaded.append
    dialog.batch_reviewer.load_run = batch_loaded.append
    dialog.open_review("run-9")
    assert loaded == ["run-9"]
    assert batch_loaded == []
    assert dialog.nav.value == "review"
    assert dialog._panels["review"].visible is True
    assert dialog._panels["run"].visible is False

    dialog._on_review_mode("batch")
    assert batch_loaded == ["run-9"]


def test_dialog_resume_confirms_status_and_allows_model_batch_override(db, monkeypatch):
    """續跑先顯示指定 run 狀態，且模型與批次大小可由輸入框覆寫。"""
    import app.views.moddb.smart_review_dialog as dialog_module
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    page = _sync_page()
    dialog = SmartReviewDialog(page, lambda: db, "1.21.1", None)
    dialog._show_tab("history")
    started = []

    preview = {
        "run_id": "run-continue",
        "status": "cancelled",
        "provider": "chatgpt",
        "model": "gpt-history",
        "batch_size": 12,
        "requested_limit": 75_027,
        "version": "1.21.1",
        "scope": {"version": "1.21.1"},
        "selected_count": 75_027,
        "processed_count": 16_100,
        "pending_count": 58_927,
        "status_counts": {"cancelled": 58_927, "no_change": 6_648},
    }
    monkeypatch.setattr(
        dialog_module,
        "preview_moddb_smart_review_resume_from_path",
        lambda _path, _priority, _run_id: preview,
    )

    def resume(run_id, *, model, batch_size, resume_preview):
        started.append((run_id, model, batch_size, resume_preview))
        dialog.job.state = SmartReviewJobState(phase="running")
        return True

    dialog.run_panel.resume_run = resume
    monkeypatch.setattr(
        dialog_module,
        "show_snack",
        lambda _page, message, *_args, **_kwargs: page.snacks.append(message),
    )

    dialog._resume_run("run-continue")

    assert dialog._resume_dialog is not None
    assert dialog._resume_model_field.value == "gpt-history"
    assert dialog._resume_batch_field.value == "12"
    dialog._resume_model_field.value = "gpt-override"
    dialog._resume_batch_field.value = "24"
    dialog._resume_dialog.actions[1].on_click(None)

    assert started == [("run-continue", "gpt-override", 24, preview)]
    assert dialog.nav.value == "run"
    assert dialog._panels["run"].visible is True
    assert "正在繼續未完成校對" in page.snacks[-1]


def test_resume_run_syncs_history_scope_into_execution_inputs(db, monkeypatch):
    """續跑建立 request 前，執行頁欄位要同步到所選歷史批次。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    panel = dialog.run_panel
    panel.version_dd.value = "1.20.1"
    panel.limit_field.value = "1000"
    panel.batch_field.value = "20"
    panel.model_dd.value = "stale-model"
    captured = []

    def fake_start(request, *, parent_callback=None):
        captured.append(request)

    monkeypatch.setattr(panel, "_start_polling", lambda: None)
    monkeypatch.setattr(dialog.job, "start", fake_start)
    preview = {
        "model": "gpt-history",
        "batch_size": 100,
        "requested_limit": 0,
        "version": "1.21.1",
        "scope": {
            "version": "1.21.1",
            "mod_id": None,
            "kind": None,
            "effective_source": None,
            "query": "",
        },
        "selected_count": 75_027,
        "processed_count": 15_467,
        "pending_count": 59_560,
    }

    assert panel.resume_run(
        "run-resume",
        model="gpt-override",
        batch_size=100,
        resume_preview=preview,
    )

    assert panel.version_dd.value == "1.21.1"
    assert panel.limit_field.value == "0"
    assert panel.batch_field.value == "100"
    assert panel.model_dd.value == "gpt-override"
    assert len(captured) == 1
    request = captured[0]
    assert (request.version, request.limit, request.model, request.batch_size) == (
        "1.21.1",
        0,
        "gpt-override",
        100,
    )
    assert (
        request.resume_selected_count,
        request.resume_processed_count,
        request.resume_pending_count,
    ) == (75_027, 15_467, 59_560)


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


def test_context_run_inline_delivers_success_and_errors(db):
    """驗證可見導覽查詢同步執行，且仍沿用統一錯誤回呼格式。"""
    context = SmartReviewContext(_sync_page(), lambda: db, None)
    delivered = []

    assert context.run_inline(
        lambda: "ready", lambda result, error: delivered.append((result, error))
    )
    assert delivered == [("ready", None)]

    def fail():
        """模擬同步導覽查詢失敗。"""
        raise RuntimeError("inline failure")

    assert context.run_inline(
        fail, lambda result, error: delivered.append((result, error))
    )
    assert delivered[-1] == (None, "inline failure")


def test_context_run_inline_discards_stale_results(db):
    """驗證同步查詢在世代或呼叫端條件失效時不會回寫舊畫面。"""
    context = SmartReviewContext(_sync_page(), lambda: db, None)
    delivered = []

    def close_while_reading():
        """模擬查詢期間視窗被關閉。"""
        context.close()
        return "late"

    assert context.run_inline(
        close_while_reading,
        lambda result, error: delivered.append((result, error)),
    )
    assert delivered == []

    context = SmartReviewContext(_sync_page(), lambda: db, None)
    assert context.run_inline(
        lambda: "filtered-out",
        lambda result, error: delivered.append((result, error)),
        still_valid=lambda: False,
    )
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


def test_resume_eta_uses_only_current_resume_progress(tmp_path):
    """續跑 ETA 不應把歷史批次已處理數量當成本次速度基準。"""
    request = _request(tmp_path)
    request.resume_run_id = "run-resume"
    request.resume_processed_count = 15_467
    request.resume_pending_count = 59_560
    state = SmartReviewJobState(
        phase="running",
        request=request,
        selected=75_027,
        processed=15_467,
        resume_baseline_processed=15_467,
        resume_pending_total=59_560,
    )
    state.started_at = 10.0
    state.resume_started_at = 20.0

    assert state.eta_seconds(now=60.0) is None
    state.processed = 15_517
    assert state.resume_processed == 50
    assert state.eta_seconds(now=60.0) == pytest.approx(47_608.0)


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


def test_resume_job_logs_snapshot_counts_and_current_progress(tmp_path, monkeypatch):
    """續跑 log 顯示歷史快照與本次新增進度，不把舊處理量當成新批次。"""
    import app.views.moddb.smart_review_job as job_module

    def fake_run(*_args, progress=None, **_kwargs):
        progress(
            {
                "run_id": "run-resume",
                "selected": 100,
                "processed": 40,
                "valid": 40,
                "phase": "selection_complete",
                "resume_baseline_processed": 40,
                "resume_pending_total": 60,
                "timing": {"selection_seconds": 0.4, "run_record_seconds": 0.02},
            }
        )
        progress(
            {
                "run_id": "run-resume",
                "selected": 100,
                "processed": 50,
                "valid": 50,
                "phase": "batch_complete",
                "timing": {
                    "batch_number": 1,
                    "ordinal_start": 41,
                    "ordinal_end": 50,
                    "elapsed_seconds": 1.0,
                    "attempts": 1,
                },
            }
        )
        return _Result()

    monkeypatch.setattr(job_module, "run_moddb_smart_review_from_path", fake_run)
    monkeypatch.setattr(
        job_module,
        "get_moddb_smart_review_run_breakdown_from_path",
        lambda *_a, **_k: {"status": {}, "errors": []},
    )
    request = _request(tmp_path)
    request.limit = 0
    request.resume_run_id = "run-resume"
    request.resume_selected_count = 100
    request.resume_processed_count = 40
    request.resume_pending_count = 60
    job = SmartReviewJob(_sync_page())

    assert job.start(request) is None

    texts = [entry.text for entry in job.session.snapshot()["logs"]]
    assert any(
        "原批次 100 筆、既有處理 40 筆、本次待處理 60 筆、上限 不限 筆、每批 5 筆"
        in text
        for text in texts
    )
    assert any("續跑準備完成：原批次 100 筆；既有處理 40 筆；本次待處理 60 筆" in text for text in texts)
    assert any("已處理 50 / 100 筆" in text and "第 1 批" in text for text in texts)
    assert not any("已處理 40 / 100 筆" in text for text in texts)


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
        self.sessions = 0

    def _filtered(self, status):
        """依狀態篩選候選。"""
        return [r for r in self.rows if status is None or r.status == status]

    def page(
        self,
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

    def get_run(self, run_id):
        """回傳批次資訊。"""
        return {"id": run_id, "provider": "gemini", "model": "m", "started_at": "t"}

    def breakdown(self, *_a, **_k):
        """回傳各狀態筆數。"""
        counts = {}
        for row in self.rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {"status": counts, "errors": []}

    @contextmanager
    def read_session(self, _path, _priority):
        """模擬同一個唯讀連線：一次 session 內的所有查詢共用。"""
        self.sessions += 1
        yield SimpleNamespace(
            get_run=self.get_run,
            run_breakdown=self.breakdown,
            candidate_page=self.page,
        )

    def page_calls(self):
        """只取出候選頁查詢的紀錄。"""
        return [call for call in self.calls if call[0] == "page"]


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
        loader_module, "moddb_smart_review_read_session", fake.read_session
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
    """驗證預設只看可套用，每頁只顯示一筆；同一次查詢順便帶回下一筆供預載。"""
    reviewer.load_run("run-1")
    assert reviewer.state.status_filter == "ready"
    assert reviewer.state.total == 2
    assert reviewer.state.row.ordinal == 2
    assert reviewer.position_input.value == "1"
    assert reviewer.position_input.text_align == ft.TextAlign.CENTER
    assert reviewer.position_suffix.value == " / 2 筆（可套用）"
    assert reviewer.fake.page_calls() == [("page", "ready", 2, 0, None)]
    assert reviewer.fake.sessions == 2  # 批次資訊與統計共用一個連線；候選頁一個連線
    labels = [option.text for option in reviewer.filter_dd.options]
    assert labels[0] == "可套用（2）"
    assert "全部（4）" in labels


def test_reviewer_next_and_previous_move_one_item_at_a_time(reviewer):
    """驗證「下一筆」就是換到下一頁，到頭時提示而不是沒反應。"""
    reviewer.load_run("run-1")
    sessions = reviewer.fake.sessions
    reviewer._move(1)
    assert reviewer.state.row.ordinal == 3
    # 下一筆已在載入第一筆時預載，換筆不再開資料庫
    assert reviewer.fake.sessions == sessions
    assert reviewer.next_btn.disabled is True
    reviewer._move(1)
    assert reviewer.ctx.page.snacks[-1] == "已經是最後一筆。"
    reviewer._move(-1)
    assert reviewer.state.row.ordinal == 2
    reviewer._move(-1)
    assert reviewer.ctx.page.snacks[-1] == "已經是第一筆。"


def test_reviewer_navigation_reads_missing_position_inline(reviewer):
    """逐筆審查換筆查不到預載時也要同步完成，不排入背景載入。"""
    reviewer.load_run("run-1")
    reviewer.loader.invalidate()
    reviewer._preload = lambda: None
    background_calls = []
    reviewer.ctx.run_background = lambda *args, **kwargs: background_calls.append(
        kwargs.get("name")
    )

    reviewer._go_to(1)

    assert reviewer.state.row.ordinal == 3
    assert background_calls == []


def test_reviewer_clamps_to_remaining_candidate_after_queue_shrinks(reviewer):
    """目前候選被處理後，原位置縮到最後一筆仍應顯示剩餘候選。"""
    reviewer.load_run("run-1")
    reviewer._go_to(1)
    assert reviewer.state.row.ordinal == 3

    reviewer.fake.rows = [row for row in reviewer.fake.rows if row.ordinal != 3]
    reviewer.loader.invalidate()
    reviewer.load_position(refresh_counts=True)

    assert reviewer.state.position == 0
    assert reviewer.state.total == 1
    assert reviewer.state.row is not None
    assert reviewer.state.row.ordinal == 2


def test_reviewer_refresh_reloads_counts_after_peer_apply(reviewer):
    """另一個審查面板套用後，逐筆面板要同步清空佇列與篩選筆數。"""
    reviewer.load_run("run-1")
    assert reviewer.state.counts["ready"] == 2

    reviewer.fake.rows = [row for row in reviewer.fake.rows if row.status != "ready"]
    reviewer.refresh()

    assert reviewer.state.total == 0
    assert reviewer.state.row is None
    assert reviewer.state.counts.get("ready", 0) == 0
    labels = [option.text for option in reviewer.filter_dd.options]
    assert labels[0] == "可套用（0）"
    assert (
        reviewer.body.controls[0].content.controls[1].value == "可套用的候選都處理完了"
    )


def test_dialog_refreshes_the_opposite_review_mode_after_apply(db, monkeypatch):
    """逐筆與批量套用後，已載入的另一個模式要讀取同一批次最新狀態。"""
    from app.views.moddb.smart_review_dialog import SmartReviewDialog

    monkeypatch.setattr(
        "app.services_impl.moddb_smart_review_service.load_config",
        lambda: {
            "lm_translator": {
                "provider": "gemini",
                "models": {"test": {"enabled": True}},
            }
        },
    )
    initial_rows = [
        _candidate(1, "invalid", suggested_zh_tw=None),
        _candidate(2, "ready"),
        _candidate(3, "ready"),
        _candidate(4, "applied"),
    ]
    fake = _FakeService(list(initial_rows))
    monkeypatch.setattr(
        loader_module, "moddb_smart_review_read_session", fake.read_session
    )
    monkeypatch.setattr(
        batch_reviewer_module,
        "moddb_smart_review_read_session",
        fake.read_session,
    )

    def run_now(context, work, on_done, *, still_valid=None, **_kwargs):
        """同步執行對話框背景讀取，保留過期結果檢查。"""
        try:
            result, error = work(), None
        except Exception as exc:  # noqa: BLE001 - mirrors production error capture
            result, error = None, str(exc)
        if context.closed or (still_valid is not None and not still_valid()):
            return True
        on_done(result, error)
        return True

    monkeypatch.setattr(SmartReviewContext, "run_background", run_now)
    parent_changes = []
    dialog = SmartReviewDialog(
        _sync_page(), lambda: db, "1.21.1", lambda: parent_changes.append(True)
    )

    dialog.open_review("run-1")
    dialog._on_review_mode("batch")
    assert dialog.reviewer.state.total == 2
    assert dialog.batch_reviewer.total == 2

    fake.rows[:] = [row for row in fake.rows if row.status != "ready"]
    assert dialog.reviewer.state.total == 2
    dialog.batch_reviewer.on_changed()

    assert dialog.reviewer.state.total == 0
    assert dialog.reviewer.state.counts.get("ready", 0) == 0
    assert parent_changes == [True]

    fake.rows[:] = list(initial_rows)
    dialog.open_review("run-1")
    dialog._on_review_mode("single")
    fake.rows[:] = [row for row in fake.rows if row.status != "ready"]
    assert dialog.batch_reviewer.status_filter == "ready"
    assert dialog.batch_reviewer.total == 2
    dialog.reviewer.on_changed()
    dialog.reviewer.on_review_changed()

    assert dialog.batch_reviewer.status_filter == "all"
    assert dialog.batch_reviewer.total == 2
    assert dialog.batch_reviewer.counts.get("ready", 0) == 0
    assert parent_changes == [True, True]


def test_reviewer_accepts_manual_position_and_reuses_known_total(reviewer):
    """驗證可輸入筆次跳轉，且換筆時沿用已取得的篩選總數。"""
    reviewer.load_run("run-1")
    reviewer.state.status_filter = "all"
    reviewer.state.position = 0
    reviewer.load_position()

    reviewer.position_input.value = "3"
    reviewer._on_position_submit(SimpleNamespace(control=reviewer.position_input))

    assert reviewer.state.row.ordinal == 3
    assert reviewer.position_input.value == "3"
    assert reviewer.position_suffix.value == " / 4 筆（全部）"
    # 預載沒有涵蓋的位置才查詢，並沿用已知總數（known_total=4）
    assert ("page", None, 3, 1, 4) in reviewer.fake.page_calls()


def test_reviewer_ignores_navigation_while_position_is_loading(reviewer):
    """驗證背景載入期間連點不會再排入下一個位置查詢。"""
    reviewer.load_run("run-1")
    reviewer.set_busy(True)

    reviewer._move(1)

    assert reviewer.state.position == 0
    assert reviewer.state.row.ordinal == 2
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
    assert reviewer.state.status_filter == "all"
    assert reviewer.state.row.status == "invalid"
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
    review_changed = []
    reviewer.on_changed = lambda: changed.append(True)
    reviewer.on_review_changed = lambda: review_changed.append(True)
    monkeypatch.setattr(
        loader_module,
        "realign_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "unchanged",
    )
    monkeypatch.setattr(
        actions_module,
        "apply_moddb_smart_review_manual_edit_from_path",
        lambda _p, _pr, run_id, ordinal, draft: (
            calls.append((run_id, ordinal, draft)) or "applied"
        ),
    )
    monkeypatch.setattr(
        actions_module,
        "apply_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: pytest.fail("已套用的筆不應走 AI 建議套用路徑"),
    )
    reviewer.load_run("run-1")
    reviewer.state.status_filter = "applied"
    reviewer.state.position = 0
    reviewer.load_position()
    assert reviewer.state.row.status == "applied"
    assert reviewer.draft.read_only is False
    assert reviewer.apply_btn.content == "重新套用"
    assert reviewer.apply_btn.disabled is False

    reviewer.draft.value = "修正後的譯文"
    reviewer._on_draft_change()
    reviewer._apply()

    assert calls == [("run-1", 4, "修正後的譯文")]
    assert changed == [True]
    assert review_changed == [True]
    assert "已套用人工修改" in reviewer.ctx.page.snacks[-1]


def test_reviewer_manual_edit_reports_changed_snapshot_without_notifying(
    reviewer, monkeypatch
):
    """資料庫已被他處修改時只提示並重載，不通知上層也不覆蓋。"""
    changed = []
    reviewer.on_changed = lambda: changed.append(True)
    monkeypatch.setattr(
        loader_module,
        "realign_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "unchanged",
    )
    monkeypatch.setattr(
        actions_module,
        "apply_moddb_smart_review_manual_edit_from_path",
        lambda *_a, **_k: "changed",
    )
    reviewer.load_run("run-1")
    reviewer.state.status_filter = "applied"
    reviewer.load_position()
    reviewer._apply()
    assert "已變更" in reviewer.ctx.page.snacks[-1]
    assert changed == []


def test_reviewer_blocks_manual_apply_when_draft_is_blank(reviewer, monkeypatch):
    """人工編輯模式下空白草稿不能套用。"""
    monkeypatch.setattr(
        loader_module,
        "realign_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "unchanged",
    )
    reviewer.load_run("run-1")
    reviewer.state.status_filter = "applied"
    reviewer.load_position()
    reviewer.draft.value = "  "
    reviewer._on_draft_change()
    assert reviewer.apply_btn.disabled is True


def test_reviewer_blocks_apply_when_draft_has_format_issue(reviewer, monkeypatch):
    """驗證草稿有格式問題時不能套用，避免 service 把候選永久鎖成 invalid_format。"""
    applied = []
    monkeypatch.setattr(
        actions_module,
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
        evidence_module,
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
        evidence_module,
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

    assert reviewer.state.row.ordinal == 1
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
        actions_module, "apply_moddb_smart_review_candidate_from_path", fake_apply
    )
    reviewer.load_run("run-1")
    assert reviewer.state.row.ordinal == 2
    reviewer._apply()
    assert changed == [True]
    assert reviewer.state.row.ordinal == 3
    assert reviewer.state.total == 1
    assert reviewer.state.counts["applied"] == 2
    assert "已套用 1 筆" in reviewer.ctx.page.snacks[-1]


def test_reviewer_keeps_current_draft_when_auto_save_fails(reviewer, monkeypatch):
    """驗證自動保存失敗時不會換筆而遺失目前草稿。"""
    monkeypatch.setattr(
        actions_module,
        "save_moddb_smart_review_draft_from_path",
        lambda *_a, **_k: "stale",
    )
    reviewer.load_run("run-1")
    current_ordinal = reviewer.state.row.ordinal
    reviewer.draft.value = "尚未保存的草稿"
    reviewer._on_draft_change()

    reviewer._move(1)

    assert reviewer.state.row.ordinal == current_ordinal
    assert reviewer.draft.value == "尚未保存的草稿"


def test_reviewer_stale_and_reject_messages(reviewer, monkeypatch):
    """驗證 stale 不通知上層，保留原譯文仍通知同視窗審查面板。"""
    changed = []
    review_changed = []
    reviewer.on_changed = lambda: changed.append(True)
    reviewer.on_review_changed = lambda: review_changed.append(True)
    monkeypatch.setattr(
        actions_module,
        "apply_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "stale",
    )
    monkeypatch.setattr(
        actions_module,
        "reject_moddb_smart_review_candidate_from_path",
        lambda *_a, **_k: "rejected",
    )
    reviewer.load_run("run-1")
    reviewer._apply()
    assert "已過期" in reviewer.ctx.page.snacks[-1]
    assert changed == []
    reviewer._reject()
    assert reviewer.ctx.page.snacks[-1] == "已保留原譯文，這筆不會套用。"
    assert review_changed == [True]


def test_reviewer_discards_stale_position_results(ctx, reviewer):
    """驗證快速連按時只有最新的位置結果會更新畫面。"""
    pending = []
    ctx.run_background = lambda work, done, *, still_valid=None, **_k: pending.append(
        (work, done, still_valid)
    )
    reviewer.state.run_id = "run-1"
    reviewer.state.status_filter = "ready"
    reviewer.state.position = 0
    reviewer.load_position()
    reviewer.state.position = 1
    reviewer.load_position()
    (_work0, _done0, valid0), (work1, done1, valid1) = pending
    assert valid0() is False
    assert valid1() is True
    done1(work1(), None)
    assert reviewer.state.row.ordinal == 3


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
    text = SmartReviewEvidencePresenter.evidence_text(
        _candidate(1, context=context, evidence_ids=())
    )
    assert text.startswith("AI 未引用任何本地證據。")
    assert "非本次 AI 證據" in text
    cited = SmartReviewEvidencePresenter.evidence_text(
        _candidate(1, context=context, evidence_ids=("db:1:source:1:revision:2",))
    )
    assert "非本次 AI 證據" not in cited
    legacy = ({"id": "db:1:source:1:revision:2", "legacy_id": "db:1"},)
    resolved = SmartReviewEvidencePresenter.evidence_text(
        _candidate(1, context=legacy, evidence_ids=("db:1",))
    )
    assert "db:1 → db:1:source:1:revision:2" in resolved
    missing = SmartReviewEvidencePresenter.evidence_text(
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

    text = SmartReviewEvidencePresenter.evidence_text(
        _candidate(1, context=context, evidence_ids=())
    )

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


def test_batch_result_summary_total_equals_processed(ctx):
    """「完成 N / M」必須等於各分類加總：未知或少見的 outcome 也要計入略過。"""
    from app.services_impl.moddb_smart_review_service import (
        SmartReviewBatchItemResult,
        SmartReviewBatchResult,
    )

    statuses = [
        "applied",
        "rejected",
        "error",
        "not_ready",
        "missing",
        "manual_reviewed",
        "missing_entry",
        "stale",
        "brand_new_status",
    ]
    result = SmartReviewBatchResult(
        action="override",
        requested=len(statuses),
        processed=len(statuses),
        cancelled=False,
        items=tuple(
            SmartReviewBatchItemResult("run-1", index, f"k{index}", status)
            for index, status in enumerate(statuses, start=1)
        ),
    )
    reviewer = SmartReviewBatchReviewer(ctx, on_changed=lambda: None)

    reviewer._render_result(result)

    text = reviewer.result_text.value
    assert "完成 9 / 9 筆" in text
    assert "已套用 1" in text
    assert "已保留原譯文 1" in text
    assert (
        "略過 6" in text
    )  # not_ready/missing/manual_reviewed/missing_entry/stale/未知
    assert "失敗 1" in text
