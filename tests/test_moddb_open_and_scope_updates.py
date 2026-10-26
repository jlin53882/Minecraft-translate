"""開啟智慧校對視窗、批次機翻範圍變更的更新範圍契約。

不測牆鐘時間，只測行為：哪些控制項被更新、哪些工作沒有在畫面執行緒做。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views._log.log_view import LogView
from app.views.moddb import smart_review_history, translate_panel
from app.views.moddb.smart_review_dialog import SmartReviewDialog
from tests.conftest import mock_page
from tests.test_smart_review import _seed
from tests.test_smart_review_ui import _sync_page
from translation_tool.translation_db import TranslationDB


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "open.db")
    _seed(database)
    yield database
    database.close()


@pytest.fixture
def one_model(monkeypatch):
    monkeypatch.setattr(
        "app.services_impl.moddb_smart_review_service.load_config",
        lambda: {
            "lm_translator": {
                "provider": "gemini",
                "models": {"gemini-3.1-flash-lite": {"enabled": True}},
            }
        },
    )


# ------------------------------------------------------------------ 智慧校對視窗
def test_building_the_smart_review_dialog_does_not_update_the_page(db, one_model):
    page = _sync_page()
    updates: list[int] = []
    page.update = lambda *_a, **_k: updates.append(1)

    SmartReviewDialog(page, lambda: db, "1.21.1", None)

    # 面板建構時沒有任何控制項掛上畫面：不該各自對整個頁面做一次 diff
    assert updates == []


def test_opening_the_dialog_does_not_load_the_hidden_history_tab(
    db, one_model, monkeypatch
):
    reads: list[int] = []
    monkeypatch.setattr(
        smart_review_history,
        "list_moddb_smart_review_runs_from_path",
        lambda *a, **k: reads.append(1) or [],
    )
    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)

    dialog.open()

    # 歷史批次卡片只在切到該分頁時才讀取與繪製（卡片很多時整個視窗的 patch 達 100KB）
    assert reads == []
    assert dialog.history.list_view.controls == []

    dialog._on_tab("history")

    assert reads == [1]


def test_opening_the_dialog_on_the_history_tab_still_loads_it(
    db, one_model, monkeypatch
):
    reads: list[int] = []
    monkeypatch.setattr(
        smart_review_history,
        "list_moddb_smart_review_runs_from_path",
        lambda *a, **k: reads.append(1) or [],
    )
    dialog = SmartReviewDialog(_sync_page(), lambda: db, "1.21.1", None)
    dialog._show_tab("history")

    dialog.open()

    assert reads == [1]


# ------------------------------------------------------------------ 批次機翻範圍
def _panel(monkeypatch, *, requests: list):
    page = mock_page()
    page.run_thread = lambda target: target()
    page.run_task = lambda *_a, **_k: None
    regions: list = []
    monkeypatch.setattr(
        translate_panel, "update_regions", lambda *c: regions.extend(c) or True
    )
    panel = translate_panel.TranslatePanel(
        page, lambda: None, request_refresh=lambda: requests.append(1)
    )
    regions.clear()
    return panel, regions


def test_typing_the_limit_updates_only_the_count_text(monkeypatch):
    requests: list = []
    panel, regions = _panel(monkeypatch, requests=requests)

    panel._on_limit_changed()

    assert regions == [panel.count_text]  # 逐字輸入不 diff 整個批次機翻面板
    assert panel.count_text.value == "正在計算此範圍的筆數…"
    assert requests == [1]


def test_scope_loading_and_snapshot_update_only_the_changed_controls(monkeypatch):
    panel, regions = _panel(monkeypatch, requests=[])

    panel.flush_loading()
    assert regions == [panel.refresh_indicator, panel.count_text]

    regions.clear()
    snapshot = {
        "identity": None,
        "versions": ["1.21.1"],
        "version": "1.21.1",
        "mods": ["foo"],
        "mod": "foo",
        "missing": 3,
        "reusable": 0,
    }
    panel.apply_refresh_snapshot(snapshot)
    panel.flush_refresh()
    assert panel.version_dd in regions and panel.mod_dd in regions
    assert panel.count_text in regions
    assert panel not in regions

    regions.clear()
    panel.apply_refresh_snapshot(snapshot)  # 選項與選值都沒變
    panel.flush_refresh()
    assert regions == [panel.refresh_indicator, panel.count_text]


def test_finishing_a_run_reads_the_counts_in_the_background(monkeypatch):
    """工作結束時的範圍筆數查詢不在畫面執行緒（輪詢協程）上做。"""
    requests: list = []
    panel, _regions = _panel(monkeypatch, requests=requests)
    db_calls: list[str] = []
    fake_db = SimpleNamespace(
        count_untranslated=lambda *a, **k: db_calls.append("untranslated") or 0,
        count_reusable=lambda *a, **k: db_calls.append("reusable") or 0,
    )
    panel._get_db = lambda: fake_db
    panel.version_dd.value = "1.21.1"
    panel.session = SimpleNamespace(
        snapshot=lambda: {
            "status": "DONE",
            "progress": 1,
            "logs": [],
            "summary": {"dry_run": True, "remaining": 3, "candidates": 1},
        },
        cancel_requested=False,
    )
    panel._running = True

    panel.sync_from_session()

    assert db_calls == []
    assert requests == [1]


def test_finishing_a_run_without_a_scheduler_reads_the_counts_inline(monkeypatch):
    panel, _regions = _panel(monkeypatch, requests=[])
    panel._request_scope_refresh = None  # 沒有上層背景快照（單元測試的精簡頁面）
    db_calls: list[str] = []
    panel._get_db = lambda: SimpleNamespace(
        count_untranslated=lambda *a, **k: db_calls.append("untranslated") or 0,
        count_reusable=lambda *a, **k: db_calls.append("reusable") or 0,
    )
    panel.version_dd.value = "1.21.1"

    panel._refresh_counts_after_run()

    assert db_calls[0] == "untranslated"


def test_clearing_the_log_can_leave_the_page_update_to_the_caller():
    page = mock_page()
    updates: list[int] = []
    page.update = lambda *_a, **_k: updates.append(1)
    log_view = LogView(page)

    log_view.clear(update=False)
    assert updates == []

    log_view.clear()
    assert updates == [1]
