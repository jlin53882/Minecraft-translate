from app.views.cache_manager.cache_presenter import CachePresenter
from app.views.cache_manager.cache_types import ActionState, CacheUiState


def test_status_text_ready_and_busy():
    p = CachePresenter()

    ready = CacheUiState(busy=False, reason="")
    saving = CacheUiState(busy=True, reason="SAVING")

    assert p.status_text(ready) == "狀態：就緒"
    assert p.status_text(saving) == "狀態：儲存中..."


def test_action_trace_and_log():
    p = CachePresenter()
    action = ActionState(action_id=3, reason="RELOADING", phase="start")

    assert p.action_trace(action) == "trace: ACTION#3 start RELOADING"
    assert p.action_log(action) == "[ACTION#3] start RELOADING"


def test_presenter_status_and_phase_mapping_complete():
    p = CachePresenter()

    assert p.status_text(CacheUiState(busy=False, reason="READY")) == "狀態：就緒"
    assert p.status_text(CacheUiState(busy=True, reason="NEXT")) == "狀態：準備下一步..."
    assert p.status_text(CacheUiState(busy=True, reason="ERROR")) == "狀態：錯誤..."
    assert p.status_text(CacheUiState(busy=True, reason="CANCELLED")) == "狀態：已取消..."

    finish_action = ActionState(action_id=8, reason="READY", phase="finish")
    cancel_action = ActionState(action_id=8, reason="RELOADING", phase="cancelled")

    assert p.action_trace(finish_action) == "trace: ACTION#8 ready READY"
    assert p.action_log(cancel_action) == "[ACTION#8] cancelled RELOADING"
