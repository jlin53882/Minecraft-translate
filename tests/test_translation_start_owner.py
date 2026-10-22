"""翻譯頁與服務層：一次執行只能有一個 ``session.start()`` owner，開頭日誌不得消失。"""

from __future__ import annotations

import pytest

from app.services_impl.pipelines._task_runner import run_callable_task
from app.tasks import task_session as task_session_module
from app.views import translation_view as tv
from app.views.translation import translation_actions as ta
from tests.conftest import mock_filepicker, mock_page


@pytest.fixture
def start_events():
    """記錄全域觀察者收到的 start／finish 事件與當時的 task_id。"""
    events: list[tuple[str, str]] = []

    def observer(session, event):
        if event in {"start", "finish"}:
            events.append((event, session.task_id))
        return True

    task_session_module.add_observer(observer)
    yield events
    task_session_module.remove_observer(observer)


def _fake_service(*args, **kwargs):
    """與真實服務相同：交給 run_callable_task（它是 start 的 owner）。"""
    session = kwargs["session"] if "session" in kwargs else args[1]
    return run_callable_task(session=session, task_name="t", func=lambda: {}, kwargs={})


@pytest.mark.parametrize(
    ("runner", "dir_attr", "service_attr"),
    [
        (ta.run_ftb, "ftb_in_dir", "run_ftb_translation_service"),
        (ta.run_kjs, "kjs_in_dir", "run_kubejs_tooltip_service"),
        (ta.run_md, "md_in_dir", "run_md_translation_service"),
    ],
)
def test_translation_run_has_single_start_and_keeps_start_log(
    start_events, runner, dir_attr, service_attr
):
    page = mock_page()
    page.run_thread = lambda target: target()  # 同步執行 worker
    view = tv.TranslationView(page, mock_filepicker())
    getattr(view, dir_attr).value = "C:/pack"
    setattr(view, service_attr, _fake_service)

    runner(view, dry_run=False)

    starts = [tid for event, tid in start_events if event == "start"]
    finishes = [tid for event, tid in start_events if event == "finish"]
    assert len(starts) == 1
    assert finishes == starts
    texts = [entry.text for entry in view.session.snapshot()["logs"]]
    assert any("[系統] 開始" in text and "C:/pack" in text for text in texts)
