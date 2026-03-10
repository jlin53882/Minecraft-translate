from app.views.cache_controller import CacheController
from app.views.cache_view import CacheView


class DummyControl:
    def __init__(self):
        self.value = ""

    def update(self):
        return None


class DummyPage:
    def update(self):
        return None


class DummyPresenter:
    def status_text(self, state):
        return f"狀態：{'忙碌' if state.busy else '就緒'}"


class DummyUiState:
    def __init__(self):
        self.busy = False
        self.reason = ""
        self.trace = ""


def make_view_for_state_gate(current_action_id: int = 2):
    v = CacheView.__new__(CacheView)
    v._controller = CacheController()
    v._controller.current_action_id = current_action_id
    v._ui_state = DummyUiState()
    v._presenter = DummyPresenter()
    v._all_logs = []
    v.page = DummyPage()
    v.overview_status = DummyControl()
    v.overview_trace = DummyControl()
    v.ui_busy = False
    v.busy_reason = ""
    v._append_log = lambda text: v._all_logs.append(text)
    v._refresh_disabled_state = lambda: None
    v.commit_ui = lambda controls=None: None
    return v


def test_set_ui_state_ignores_stale_run_id():
    view = make_view_for_state_gate(current_action_id=5)

    CacheView.set_ui_state(view, busy=True, reason="RELOADING", trace="trace old", run_id=4)

    assert view.ui_busy is False
    assert view.busy_reason == ""
    assert any("忽略過期狀態更新" in x for x in view._all_logs)


def test_set_ui_state_applies_current_run_id():
    view = make_view_for_state_gate(current_action_id=5)

    CacheView.set_ui_state(view, busy=True, reason="RELOADING", trace="trace ok", run_id=5)

    assert view.ui_busy is True
    assert view.busy_reason == "RELOADING"
    assert view.overview_trace.value == "trace ok"
    assert view.overview_status.value.startswith("狀態：")
