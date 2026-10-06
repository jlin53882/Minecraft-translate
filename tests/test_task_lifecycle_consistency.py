"""TaskSession / TaskManager / 後台 lifecycle 紀錄 / handler routing 的終止狀態要一致。"""

from __future__ import annotations

import logging
import threading

import pytest

from app.shell.task_manager import STATUS_DONE, STATUS_ERROR, TaskManager
from app.tasks.task_session import TaskSession
from translation_tool.utils import ui_mirror
from translation_tool.utils.ui_logging_handler import UISessionLogHandler


@pytest.fixture
def manager():
    mgr = TaskManager()
    mgr.attach()
    yield mgr
    mgr.detach()


def _messages(caplog, name):
    return [
        r.getMessage()
        for r in caplog.records
        if getattr(r, "task_name", None) == name and r.getMessage().startswith("任務")
    ]


# ------------------------------------------------ ERROR ≠ finished（routing 清理）


def test_error_but_unfinished_task_keeps_its_routing_when_another_task_unbinds():
    handler = UISessionLogHandler()
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("test.lifecycle.routing")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    a, b = TaskSession(name="A"), TaskSession(name="B")
    a.start()
    b.start()
    b_failed = threading.Event()
    a_unbound = threading.Event()
    b_done = threading.Event()

    def run_b():
        handler.set_session(b)
        b.set_error()  # 已標記失敗，但服務還在 finally 前，後面還會有清理 log
        b_failed.set()
        a_unbound.wait(timeout=5)
        logger.warning("B 失敗後的清理紀錄")
        b.finish()
        handler.set_session(None)
        b_done.set()

    def run_a():
        handler.set_session(a)
        b_failed.wait(timeout=5)
        handler.set_session(None)  # A 結束：不能順手把 ERROR 但未 finish 的 B 清掉
        a_unbound.set()
        b_done.wait(timeout=5)

    try:
        threads = [threading.Thread(target=run_b), threading.Thread(target=run_a)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        logger.removeHandler(handler)

    texts_b = [e.text for e in b.snapshot()["logs"]]
    assert any("B 失敗後的清理紀錄" in t for t in texts_b), texts_b
    assert [e.text for e in a.snapshot()["logs"]] == []


def test_prune_removes_only_sessions_that_really_finished():
    handler = UISessionLogHandler()
    errored, finished, running = (
        TaskSession(name="e"),
        TaskSession(name="f"),
        TaskSession(name="r"),
    )
    for s in (errored, finished, running):
        s.start()
    errored.set_error()  # ERROR 但未 finish
    finished.finish()
    for s in (errored, finished, running):
        handler.set_session(s)

    ui_mirror.set_current_task(None)
    handler.set_session(None)  # 無歸屬的呼叫端：只能清已 finish 的

    assert finished.task_id not in handler._sessions
    assert errored.task_id in handler._sessions
    assert running.task_id in handler._sessions
    handler.clear()


def test_is_finished_follows_the_real_lifecycle_not_the_status():
    session = TaskSession(name="life")
    assert session.is_finished is False
    session.start()
    session.set_error()
    assert session.status == "ERROR" and session.is_finished is False
    session.finish()
    assert session.is_finished is True
    session.start()
    assert session.is_finished is False


# ------------------------------------------------ 結束後才 set_error：三邊一致


def test_late_set_error_amends_session_task_manager_and_backend_consistently(
    caplog, manager
):
    session = TaskSession(name="更正")
    with caplog.at_level(logging.INFO):
        session.start()
        session.finish()  # 步驟自己先 finish：DONE
        assert manager.recent()[0].status == STATUS_DONE
        session.set_error()  # 流水線安全網補標失敗
        session.finish()  # 第二次 finish：不再寫結束紀錄

    # 1) TaskSession
    assert session.status == "ERROR" and session.error is True
    # 2) TaskManager：最近完成的結果被更正
    assert manager.recent()[0].status == STATUS_ERROR
    assert manager.active() == []
    # 3) 後台：一筆「任務結束：DONE」＋一筆明確的「任務結果更正」，沒有第二筆結束
    messages = _messages(caplog, "更正")
    assert [m for m in messages if "任務結束" in m] == [
        next(m for m in messages if m.startswith("任務結束：DONE"))
    ]
    assert len([m for m in messages if "任務結果更正" in m]) == 1
    assert "DONE → ERROR" in next(m for m in messages if "任務結果更正" in m)


def test_repeated_late_set_error_writes_one_correction(caplog, manager):
    session = TaskSession(name="多次更正")
    with caplog.at_level(logging.INFO):
        session.start()
        session.finish()
        session.set_error()
        session.set_error()
        session.finish()
    assert len([m for m in _messages(caplog, "多次更正") if "任務結果更正" in m]) == 1


def test_normal_error_then_finish_has_no_correction(caplog, manager):
    session = TaskSession(name="正常失敗")
    with caplog.at_level(logging.INFO):
        session.start()
        session.set_error()
        session.finish()
        session.set_error()  # 已經是 ERROR 結案：沒有東西要更正
        session.finish()

    messages = _messages(caplog, "正常失敗")
    assert len([m for m in messages if "任務結束：ERROR" in m]) == 1
    assert not [m for m in messages if "任務結果更正" in m]
    assert manager.recent()[0].status == STATUS_ERROR


def test_pipeline_runner_safety_net_after_step_exception_is_consistent(caplog, manager):
    """一鍵步驟自己 finally finish 之後，PipelineRunner 安全網再 set_error + finish。"""
    from app.views.pipeline.pipeline_actions import PipelineActions, PipelineServices
    from app.views.pipeline.pipeline_config import PipelineConfig

    def merge_folder(**_kw):
        raise RuntimeError("merge exploded")
        yield  # pragma: no cover

    cfg = PipelineConfig("mods_dir", "out_dir")
    step = PipelineActions(PipelineServices(merge_folder=merge_folder)).one_click_steps(
        {}, cfg, "lang", ["en_us"], {}
    )[1][2]
    session = TaskSession(name="安全網")

    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError):
            for _ in step(session):
                pass
        session.set_error()
        session.finish()

    messages = _messages(caplog, "安全網")
    assert len([m for m in messages if "任務結束" in m]) == 1
    assert session.status == "ERROR"
    assert manager.recent()[0].status == STATUS_ERROR


# ------------------------------------------------ task_id 是「一次執行」的識別


def test_start_issues_a_new_task_id_for_every_run():
    session = TaskSession(name="重跑")
    first = session.task_id
    session.start()
    second = session.task_id
    session.finish()
    session.start()
    third = session.task_id
    assert len({first, second, third}) == 3


def test_previous_runs_backend_record_cannot_excuse_the_next_runs_ui_line(caplog):
    """同一個 session 物件重跑（例如合併頁再按一次）：上一次留下的後台記錄不屬於這一次。"""
    ui_mirror.BACKEND_SEEN_TRACKER.clear()
    ui_mirror.ensure_tracker()
    session = TaskSession(name="重跑去重")
    with caplog.at_level(logging.INFO):
        session.start()
        with ui_mirror.task_scope(session.task_id):
            logging.getLogger("core").info("處理完成")  # 第 1 次執行的後台記錄
        session.finish()

        session.start()  # 第 2 次執行：新的 task_id
        session.add_log("處理完成")  # 第 2 次只有 UI 的同文字訊息

    mirrored = [
        r.getMessage() for r in caplog.records if getattr(r, "ui_mirrored", False)
    ]
    assert "處理完成" in mirrored
    ui_mirror.BACKEND_SEEN_TRACKER.clear()
