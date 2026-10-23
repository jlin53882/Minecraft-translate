"""背景結果無法排回畫面（頁面已卸載或排程失敗）時，各流程要回收執行中狀態而不卡住按鈕。"""

from __future__ import annotations

from types import SimpleNamespace

from app.ui.page_post import post_to_ui
from app.views.moddb.bulk_review_dialog import BulkReviewDialog
from app.views.moddb.cleanup_dialog import CleanupDialog
from app.views.moddb.entries_panel import EntriesPanel
from app.views.moddb.species_bulk_lookup import SpeciesBulkLookup
from app.views.moddb.species_dialog import SpeciesCandidateDialog
from app.views.moddb.species_suggestion import SpeciesSuggestion
from tests.conftest import mock_page


def _rejecting_page():
    """run_task 一律拋錯，模擬頁面已卸載。"""
    page = mock_page()

    def reject(*_args, **_kwargs):
        raise RuntimeError("page disposed")

    page.run_task = reject
    return page


def test_post_to_ui_success_and_failure_paths():
    page = SimpleNamespace()  # 沒有 run_task：同步呼叫
    seen = []
    assert post_to_ui(page, seen.append, 1, label="x") is True
    assert seen == [1]

    scheduled = []
    page.run_task = lambda coro: scheduled.append(coro)
    assert post_to_ui(page, seen.append, 2, label="x") is True
    assert len(scheduled) == 1

    recovered = []
    assert (
        post_to_ui(
            _rejecting_page(),
            seen.append,
            3,
            label="x",
            on_failure=lambda: recovered.append(1),
        )
        is False
    )
    assert recovered == [1] and seen == [1]


def test_post_to_ui_failing_recovery_does_not_raise():
    def bad():
        raise RuntimeError("recover failed")

    assert (
        post_to_ui(_rejecting_page(), lambda: None, label="x", on_failure=bad) is False
    )


def test_bulk_review_dialog_releases_busy_when_result_cannot_be_posted():
    dialog = BulkReviewDialog(_rejecting_page(), lambda: None, "1.21.1", None)
    dialog._set_busy(True, "審核中…")
    dialog._mark_scope_post_failure(dialog._scope_seq)
    assert dialog._busy is True  # 選項維護工作的失敗不可清掉目前操作狀態
    dialog._post(dialog._show_progress, "x", 0.5, final=False)
    assert dialog._busy is True  # 進度通知失敗不影響操作
    dialog._post(dialog._apply_result, 0, None, None, None)
    assert dialog._busy is False
    # 排程失敗 callback 在 worker thread 執行，只能回收純狀態；頁面已卸載，
    # 不可在該 thread 直接觸碰 Flet controls。


def test_cleanup_dialog_releases_busy_when_result_cannot_be_posted():
    dialog = CleanupDialog(_rejecting_page(), lambda: None, lambda: None)
    dialog._set_busy(True, "刪除中…")
    dialog._post(dialog._after_apply, None, None)
    assert dialog._busy is False


def test_species_suggestion_discards_running_name_when_result_cannot_be_posted():
    suggestion = SpeciesSuggestion(_rejecting_page(), lambda _t: None, lambda: None)
    suggestion._running.add("橄欖")
    suggestion._post(
        suggestion._finish,
        "橄欖",
        None,
        on_failure=lambda: suggestion._running.discard("橄欖"),
    )
    assert "橄欖" not in suggestion._running


def test_species_bulk_lookup_releases_running_when_finish_cannot_be_posted():
    job = SpeciesBulkLookup(_rejecting_page())
    job.running = True
    job.session = object()
    generation = job._generation
    job._post(
        job._finish,
        generation,
        "completed",
        [],
        on_failure=lambda: job._abandon(generation),
    )
    assert job.running is False and job.session is None

    job.running = True  # 世代已被新的查詢取代：舊失敗不可動新狀態
    job._post(
        job._finish,
        generation,
        "completed",
        [],
        on_failure=lambda: job._abandon(generation + 1),
    )
    assert job.running is True


def test_species_dialog_scan_failure_releases_busy():
    dialog = SpeciesCandidateDialog(
        _rejecting_page(), lambda: None, "1.21.1", None, None
    )
    dialog._set_busy(True, "掃描中")
    dialog._post(
        dialog._apply_scan, 0, None, None, on_failure=lambda: dialog._set_busy(False)
    )
    assert dialog._busy is False


def test_entries_panel_abandon_save_unblocks_editor():
    panel = EntriesPanel(_rejecting_page(), lambda: None)
    panel._save_running = True
    panel.saved_text.value = "正在儲存…"
    panel._abandon_save()
    assert panel._save_running is False
    assert panel.saved_text.value == ""
