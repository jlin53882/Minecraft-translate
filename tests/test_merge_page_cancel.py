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


def test_start_merge_without_any_zip_is_rejected(view):
    view.input_mode_group.value = "zip"
    view.selected_zips = []
    view.zip_path_field.value = ""

    view.start_merge(None)

    assert view.start_button.disabled is not True


def test_start_merge_runs_with_only_a_typed_zip_path(view, monkeypatch):
    """Web：沒有用選擇器、只手動輸入 ZIP 路徑也能開始，且開始訊息的數量是實際執行的數量。"""
    received = {}

    def fake_service(**kwargs):
        received.update(kwargs)
        return iter(())

    monkeypatch.setattr(merge_view, "run_merge_zip_batch_service", fake_service)

    class _SyncThread:
        def __init__(self, target=None, daemon=None, **kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(merge_view.threading, "Thread", _SyncThread)
    view.input_mode_group.value = "zip"
    view.selected_zips = []
    view.zip_path_field.value = "C:/mods/a.zip"
    view.output_dir_field.value = "C:/out"

    view.start_merge(None)

    assert view.start_button.disabled is True
    assert received["zip_paths"] == ["C:/mods/a.zip"]
    started = [text for _, text in view.session.logs if "開始合併任務" in text]
    assert started and "1 個 ZIP" in started[0] and "0 個 ZIP" not in started[0]


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

    def fake_service(mode, zip_paths=None):
        seen.append(is_cancelled())
        view.session.request_cancel()
        seen.append(is_cancelled())

    monkeypatch.setattr(view, "_run_merge_service", fake_service)

    view._run_merge_worker("folder")

    assert seen == [False, True]


def test_worker_treats_cancellation_as_a_normal_stop(view, monkeypatch):
    def cancelled(mode, zip_paths=None):
        raise TaskCancelled()

    monkeypatch.setattr(view, "_run_merge_service", cancelled)

    view._run_merge_worker("folder")  # 不得拋出

    assert any("合併已停止" in text for _, text in view.session.logs)
    assert view.session.status != "ERROR"


def test_worker_marks_real_failures_as_errors(view, monkeypatch):
    def broken(mode, zip_paths=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(view, "_run_merge_service", broken)

    view._run_merge_worker("zip")

    assert view.session.status == "ERROR"
    assert getattr(view.session, "finished", False) is True


# ----------------------- 背景執行緒邊界的錯誤必須歸屬到自己的任務（不串到別的任務畫面）


def test_worker_boundary_error_stays_attributed_to_its_own_task(monkeypatch):
    import logging
    import threading

    from translation_tool.utils import ui_mirror
    from translation_tool.utils.ui_logging_handler import UISessionLogHandler

    ui_mirror.install_task_record_factory()
    monkeypatch.setattr(merge_view, "load_config", lambda: {"lang_merger": {}})
    monkeypatch.setattr(merge_widgets, "load_config", lambda: {"lang_merger": {}})
    view = merge_view.MergeView(mock_page(), mock_filepicker())
    merge_session, other_session = (
        TaskSession(name="語系合併"),
        TaskSession(name="機器翻譯"),
    )
    merge_session.start()
    other_session.start()
    view.session = merge_session

    handler = UISessionLogHandler()
    handler.setLevel(logging.INFO)
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    capture = _Capture(level=logging.INFO)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.addHandler(capture)
    root.setLevel(logging.INFO)

    def service_that_cleans_up_then_fails(mode, zip_paths=None):
        # 等同真實服務：入口 set_session、finally set_session(None)，之後例外往外傳
        handler.set_session(merge_session)
        handler.set_session(None)
        raise RuntimeError("boom")

    monkeypatch.setattr(view, "_run_merge_service", service_that_cleans_up_then_fails)
    try:
        handler.set_session(other_session)  # 另一個任務是「最近綁定」的 session
        worker = threading.Thread(target=view._run_merge_worker, args=("folder",))
        worker.start()
        worker.join()
    finally:
        root.removeHandler(handler)
        root.removeHandler(capture)
        root.setLevel(previous)
        handler.clear()

    failure = [r for r in records if "合併執行失敗" in r.getMessage()]
    assert failure, "邊界錯誤必須寫進後台 log"
    assert all(r.task_id == merge_session.task_id for r in failure)
    assert all(r.task_name == "語系合併" for r in failure)
    other_texts = [e.text for e in other_session.snapshot()["logs"]]
    assert not any("boom" in t for t in other_texts)  # 沒有串進別的任務畫面
    own_texts = [e.text for e in merge_session.snapshot()["logs"]]
    assert any("合併執行失敗" in t for t in own_texts)  # 自己的畫面有錯誤摘要


def test_translation_page_boundary_error_stays_attributed_to_its_own_task():
    """同一類問題：翻譯頁工作執行緒邊界的錯誤堆疊也要歸屬到自己的任務。"""
    import logging
    import threading
    from types import SimpleNamespace

    from app.views.translation import translation_actions
    from translation_tool.utils import ui_mirror

    ui_mirror.install_task_record_factory()
    session = TaskSession(name="FTB 翻譯")
    session.start()
    view = SimpleNamespace(session=session)
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    capture = _Capture(level=logging.INFO)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(capture)
    root.setLevel(logging.INFO)

    def boundary():
        ui_mirror.set_current_task(None)  # 服務結束後這條執行緒沒有任務歸屬
        try:
            raise RuntimeError("boom")
        except RuntimeError as ex:
            translation_actions._report_service_failure(view, "FTB", ex, "C:/in")

    try:
        worker = threading.Thread(target=boundary)
        worker.start()
        worker.join()
    finally:
        root.removeHandler(capture)
        root.setLevel(previous)

    failure = [r for r in records if "服務執行失敗" in r.getMessage()]
    assert failure
    assert all(r.task_id == session.task_id for r in failure)
