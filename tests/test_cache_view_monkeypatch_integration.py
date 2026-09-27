import flet as ft

from app.views.cache_view import CacheView


class FakePage:
    def update(self, *args, **kwargs):
        return None

    def set_clipboard(self, _text):
        return None


class DummyBtn:
    def __init__(self):
        self.disabled = False
        self.tooltip = ""


class DummyCheckbox:
    def __init__(self, value=False):
        self.value = value


def _build_test_view(monkeypatch):
    view = CacheView.__new__(CacheView)
    view._page = FakePage()
    view.ui_busy = False
    view.busy_reason = ""
    view._all_logs = []
    view._only_error = False

    view.overview_status = ft.Text(value="狀態：就緒")
    view.overview_trace = ft.Text(value="trace: init")
    view.overview_text = ft.Text(value="")

    view.btn_reload_all = DummyBtn()
    view.btn_save_all_new = DummyBtn()
    view.btn_refresh_stats = DummyBtn()
    view.btn_save_all_fill = DummyBtn()
    view.chk_danger_confirm = DummyCheckbox(False)

    view.log_list = type("Dummy", (), {"controls": []})()

    monkeypatch.setattr(view, "_render_logs", lambda: None)
    # 2026-08-01 (PR #85):view._show_snack_bar 物理刪除,改用 show_snack helper
    # monkeypatch module-level show_snack 函式(因為 view 不再有 _show_snack_bar method)
    monkeypatch.setattr("app.ui.snack.show_snack", lambda *args, **kwargs: None)
    monkeypatch.setattr(view, "_refresh_overview_ui", lambda data: None)
    monkeypatch.setattr(view, "_refresh_query_type_options", lambda: None)
    monkeypatch.setattr(view, "_render_query_type_shard_page", lambda: None)

    view._last_overview_data = {"total": 0}

    return view


def test_action_success_back_to_ready(monkeypatch):
    view = _build_test_view(monkeypatch)

    view._run_action("RELOADING", lambda: {"ok": True}, "done")

    assert view.ui_busy is False
    assert view.overview_status.value == "狀態：就緒"
    assert any("finish READY" in x for x in view._all_logs)


def test_busy_guard_blocks_second_action(monkeypatch):
    view = _build_test_view(monkeypatch)
    view.ui_busy = True

    called = {"n": 0}

    def work():
        called["n"] += 1
        return {}

    view._run_action("SAVING", work, "done")

    assert called["n"] == 0
    assert any("目前正在處理" in x for x in view._all_logs)


def test_fill_requires_danger_confirm(monkeypatch):
    view = _build_test_view(monkeypatch)

    view._on_save_all_fill(None)

    assert any("尚未勾選高風險確認" in x for x in view._all_logs)


def test_action_runs_off_event_loop_and_stays_busy_until_done(monkeypatch):
    """B4：有 event loop 時工作在執行緒中執行，完成前維持忙碌狀態，且只執行一次。"""
    import asyncio
    import threading

    view = _build_test_view(monkeypatch)
    tasks = []
    view._page.run_task = lambda handler, *args: tasks.append(handler)
    calls = []

    def work():
        calls.append(threading.current_thread().name)
        raise TypeError("bad arg inside service")

    view._run_action("RELOADING", work, "done")
    # 點擊當下不等待工作完成（原本 future.result() 會凍結 UI）
    assert calls == []
    assert view.ui_busy is True

    main_thread = threading.current_thread().name
    asyncio.run(tasks[0]())

    assert len(calls) == 1  # 原本 TypeError 時會重跑 work_fn 最多三次
    assert calls[0] != main_thread
    assert view.ui_busy is False
    assert any("RELOADING 失敗" in x for x in view._all_logs)
