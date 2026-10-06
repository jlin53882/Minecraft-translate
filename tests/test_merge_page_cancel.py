"""語系合併頁：取消按鈕、手動輸入 ZIP 路徑（Web）、session 旗標取消與 cancel_scope 取消並存。"""

from __future__ import annotations

import pytest

from app.services_impl.pipelines import merge_service
from app.tasks.task_session import TaskSession
from app.views import merge_view
from app.views.merge import merge_widgets
from tests.conftest import mock_filepicker, mock_page
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope


class _Session:
    def __init__(self, status="RUNNING", max_logs=2000):
        self.status = status
        self.cancel_calls = 0
        self.logs = []

    def start(self):
        pass

    def add_log(self, text, level="info", **kwargs):
        self.logs.append((level, text))

    def request_cancel(self):
        self.cancel_calls += 1
        self.cancel_requested = True

    cancel_requested = False

    def set_error(self):
        self.status = "ERROR"

    def finish(self):
        self.finished = True

    def snapshot(self):
        return {"status": self.status, "progress": 0.0, "logs": []}


@pytest.fixture
def view(monkeypatch):
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    monkeypatch.setattr(merge_widgets, "TaskSession", _Session)
    monkeypatch.setattr(merge_view, "load_config", lambda: {"lang_merger": {}})
    monkeypatch.setattr(merge_widgets, "load_config", lambda: {"lang_merger": {}})
    return merge_view.MergeView(mock_page(), mock_filepicker())


# ---------------------------------------------------------------- 取消按鈕


def test_cancel_button_is_hidden_until_a_merge_runs(view):
    assert view.cancel_button.visible is False


def test_cancel_merge_requests_cancel_while_running(view):
    view.cancel_merge(None)

    assert view.session.cancel_calls == 1
    assert view.cancel_button.disabled is True
    assert any(
        level == "warning" and "取消" in text for level, text in view.session.logs
    )


def test_cancel_merge_does_nothing_when_not_running(view):
    view.session.status = "DONE"

    view.cancel_merge(None)

    assert view.session.cancel_calls == 0


# ------------------------------------------------------- Web：手動輸入 ZIP 路徑


def test_typed_zip_path_is_added_to_the_selected_zips(view):
    view.selected_zips = ["a.zip"]
    view.zip_path_field.value = "  C:/mods/b.zip  "

    assert view._zip_paths_for_run() == ["a.zip", "C:/mods/b.zip"]


def test_typed_zip_path_is_not_duplicated_or_empty(view):
    view.selected_zips = ["a.zip"]
    view.zip_path_field.value = "a.zip"
    assert view._zip_paths_for_run() == ["a.zip"]

    view.zip_path_field.value = "   "
    assert view._zip_paths_for_run() == ["a.zip"]


def test_start_merge_accepts_only_a_typed_zip_path(view):
    view.input_mode_group.value = "zip"
    view.selected_zips = []
    view.zip_path_field.value = ""
    view.start_merge(None)  # 沒有任何 ZIP：提示、不啟動
    assert view.start_button.disabled is not True


# ---------------------------------------- 服務層：兩種取消來源都要能停止核心迴圈


def _fake_core(processed, session=None, cancel_at=None):
    def core(*args, **kwargs):
        for i in range(10):
            processed.append(i)
            if session is not None and i == cancel_at:
                session.request_cancel()
            yield {"progress": i / 10, "log": f"處理 {i}"}

    return core


def test_session_flag_stops_the_folder_service_without_a_cancel_scope(
    tmp_path, monkeypatch
):
    """Web 合併頁沒有流水線的 cancel_scope：只靠 session 取消旗標也必須停止。"""
    src = tmp_path / "in"
    src.mkdir()
    out = tmp_path / "out"
    session = TaskSession()
    session.start()
    processed: list[int] = []
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        _fake_core(processed, session, cancel_at=1),
    )

    with pytest.raises(TaskCancelled):
        list(
            merge_service.run_merge_folder_batch_service(
                str(src), str(out), session, True
            )
        )

    assert len(processed) <= 3  # 在 update 邊界停止，沒有跑完 10 個
    assert not out.exists()  # 新建的輸出資料夾被清掉


def test_cancel_scope_still_stops_the_folder_service(tmp_path, monkeypatch):
    """流水線（PipelineRunner）用 cancel_scope 取消：改成讀 session 旗標後不能退步。"""
    src = tmp_path / "in"
    src.mkdir()
    session = TaskSession()
    session.start()
    processed: list[int] = []
    monkeypatch.setattr(
        merge_service, "merge_zhcn_to_zhtw_from_folder", _fake_core(processed)
    )

    with cancel_scope(lambda: len(processed) >= 2), pytest.raises(TaskCancelled):
        list(
            merge_service.run_merge_folder_batch_service(
                str(src), str(tmp_path / "out"), session, True
            )
        )

    assert len(processed) <= 3


# ------------------------------------------------ 背景執行緒入口（取消／失敗）


def test_worker_exposes_the_session_flag_to_the_core_via_cancel_scope(
    view, monkeypatch
):
    """合併頁的取消要能讓核心（lang_merger）的檢查點生效：worker 必須註冊 cancel_scope。"""
    from translation_tool.utils.cancellation import is_cancelled

    seen = []

    def fake_service(mode):
        seen.append(is_cancelled())
        view.session.request_cancel()
        seen.append(is_cancelled())

    monkeypatch.setattr(view, "_run_merge_service", fake_service)

    view._run_merge_worker("folder")

    assert seen == [False, True]


def test_worker_treats_cancellation_as_a_normal_stop(view, monkeypatch):
    def cancelled(mode):
        raise TaskCancelled()

    monkeypatch.setattr(view, "_run_merge_service", cancelled)

    view._run_merge_worker("folder")  # 不得拋出

    assert any("合併已停止" in text for _, text in view.session.logs)
    assert view.session.status != "ERROR"


def test_worker_marks_real_failures_as_errors(view, monkeypatch):
    def broken(mode):
        raise RuntimeError("boom")

    monkeypatch.setattr(view, "_run_merge_service", broken)

    view._run_merge_worker("zip")

    assert view.session.status == "ERROR"
    assert getattr(view.session, "finished", False) is True
