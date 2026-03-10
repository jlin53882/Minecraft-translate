import flet as ft

from app.views.cache_view import CacheView


class FakePage:
    def update(self):
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
    view.page = FakePage()
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
    monkeypatch.setattr(view, "_show_snack_bar", lambda msg, color: None)
    monkeypatch.setattr(view, "_refresh_overview_ui", lambda data: None)

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
