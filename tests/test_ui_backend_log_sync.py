"""UI 日誌與後台日誌的自動同步（TaskSession / LogView / ui_mirror）。"""

from __future__ import annotations

import logging

import pytest

from app.tasks.task_session import TaskSession
from translation_tool.utils import ui_mirror
from translation_tool.utils.log_unit import log_error, log_info
from translation_tool.utils.ui_logging_handler import UISessionLogHandler


@pytest.fixture(autouse=True)
def _clean_tracker():
    ui_mirror.BACKEND_SEEN_TRACKER.clear()
    yield
    ui_mirror.BACKEND_SEEN_TRACKER.clear()


def _backend(caplog, name_prefix: str | None = None):
    return [
        r
        for r in caplog.records
        if getattr(r, "ui_mirrored", False)
        and (name_prefix is None or r.getMessage().startswith(name_prefix))
    ]


def test_session_add_log_is_mirrored_to_backend_with_task_tag(caplog):
    session = TaskSession(name="合併")
    with caplog.at_level(logging.INFO):
        session.add_log("處理 a.jar", level="warning")

    records = _backend(caplog, "處理 a.jar")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    # app.log 的任務標籤標示這一行屬於哪個任務（不再另加文字前綴）
    assert records[0].task_name == "合併"
    assert records[0].task_id == session.task_id
    assert records[0].task_tag == f"[task=合併/{session.task_id}] "
    assert [e.text for e in session.snapshot()["logs"]] == ["處理 a.jar"]


@pytest.mark.parametrize("source", ["logger", "backend"])
def test_backend_sourced_messages_are_not_mirrored(caplog, source):
    session = TaskSession()
    with caplog.at_level(logging.INFO):
        session.add_log("來自後台", source=source)
    assert _backend(caplog) == []


def test_mirror_false_skips_backend(caplog):
    session = TaskSession()
    with caplog.at_level(logging.INFO):
        session.add_log("只進畫面", mirror=False)
    assert _backend(caplog) == []


def test_already_logged_backend_message_is_not_duplicated(caplog):
    """核心流程自己 log 之後又 yield 給 UI 的訊息，後台只應出現一次。"""
    ui_mirror.ensure_tracker()
    session = TaskSession()
    with caplog.at_level(logging.INFO):
        log_info("分析 ZIP 檔案: a.zip")  # 核心流程自己寫的後台 log
        session.add_log("分析 ZIP 檔案: a.zip")  # UI 轉送同一則
        session.add_log("只有 UI 的訊息")

    assert [r.getMessage() for r in _backend(caplog)] == ["只有 UI 的訊息"]


def test_each_backend_record_excuses_only_one_ui_line(caplog):
    ui_mirror.ensure_tracker()
    session = TaskSession()
    with caplog.at_level(logging.INFO):
        log_info("完成")
        session.add_log("完成")  # 被後台那一筆抵銷
        session.add_log("完成")  # 第二次只有 UI：必須補寫

    assert [r.getMessage() for r in _backend(caplog)] == ["完成"]


def test_multiline_forward_only_mirrors_unseen_lines(caplog):
    ui_mirror.ensure_tracker()
    session = TaskSession()
    with caplog.at_level(logging.INFO):
        log_info("第一行")
        session.add_log("第一行\n第二行")

    assert [r.getMessage() for r in _backend(caplog)] == ["第二行"]


def test_mirrored_record_is_not_fed_back_into_ui():
    handler = UISessionLogHandler()
    session = TaskSession()
    handler.set_session(session)
    logger = logging.getLogger("test.mirror.loop")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        ui_mirror.mirror_to_backend("鏡像訊息", logger=logger)
        logger.info("一般後台訊息")
    finally:
        logger.removeHandler(handler)

    assert [e.text for e in session.snapshot()["logs"]] == ["一般後台訊息"]


def test_secrets_are_redacted_in_backend_mirror(caplog):
    ui_mirror.ensure_tracker()
    with caplog.at_level(logging.INFO):
        ui_mirror.mirror_to_backend("呼叫失敗 Authorization: Bearer abcdef123456")
    assert "abcdef123456" not in caplog.text


def test_session_lifecycle_is_written_to_backend(caplog):
    session = TaskSession(name="提取")
    with caplog.at_level(logging.INFO):
        session.start()
        session.set_summary({"success": 3})
        session.set_error()
        session.finish()

    mine = [r for r in caplog.records if getattr(r, "task_name", None) == "提取"]
    messages = [r.getMessage() for r in mine]
    assert "任務開始" in messages
    ended = [m for m in messages if m.startswith("任務結束：ERROR")]
    assert ended and "耗時" in ended[0] and "'success': 3" in ended[0]


def test_start_logs_replayed_by_start_are_not_mirrored_twice(caplog):
    session = TaskSession()
    with caplog.at_level(logging.INFO):
        session.add_start_log("預設輸出路徑：out")
        session.start()  # 清空並重放開頭訊息

    assert [r.getMessage() for r in _backend(caplog)].count("預設輸出路徑：out") == 1
    assert [e.text for e in session.snapshot()["logs"]] == ["預設輸出路徑：out"]


def test_log_error_in_except_block_attaches_traceback(caplog):
    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError("壞掉了")
        except ValueError as exc:
            log_error(f"處理失敗：{exc}")

    record = caplog.records[-1]
    assert record.exc_info and record.exc_info[0] is ValueError


def test_log_error_does_not_duplicate_manual_traceback(caplog):
    import traceback

    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError("壞掉了")
        except ValueError:
            log_error(f"處理失敗\n{traceback.format_exc()}")

    assert not caplog.records[-1].exc_info


def test_log_error_outside_except_has_no_traceback(caplog):
    with caplog.at_level(logging.ERROR):
        log_error("單純的錯誤訊息")
    assert not caplog.records[-1].exc_info
