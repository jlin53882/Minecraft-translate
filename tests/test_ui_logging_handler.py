"""UISessionLogHandler：UI 只顯示訊息本身，且保留 logging 等級。"""

import logging

from app.services_impl.pipelines._pipeline_logging import mirror_session_log
from app.tasks.task_session import TaskSession
from translation_tool.utils.ui_logging_handler import UISessionLogHandler


def _emit(handler, level, msg):
    record = logging.LogRecord(
        "translation_tool.x", level, __file__, 1, msg, None, None
    )
    handler.emit(record)


def test_levels_and_plain_message():
    handler = UISessionLogHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - [%(name)s] - %(message)s")
    )
    session = TaskSession()
    handler.set_session(session)

    _emit(handler, logging.INFO, "掃描完成")
    _emit(handler, logging.WARNING, "金鑰即將用完")
    _emit(handler, logging.ERROR, "API 失敗")

    logs = session.snapshot()["logs"]
    assert [(e.level, e.text) for e in logs] == [
        ("info", "掃描完成"),
        ("warning", "[WARN] 金鑰即將用完"),
        ("error", "[ERROR] API 失敗"),
    ]


def test_no_session_is_noop():
    _emit(UISessionLogHandler(), logging.ERROR, "x")


def test_ui_mirror_record_does_not_duplicate_session_log():
    handler = UISessionLogHandler()
    session = TaskSession()
    handler.set_session(session)
    record = logging.LogRecord(
        "translation_tool.x", logging.INFO, __file__, 1, "mirrored", None, None
    )
    record.ui_mirror = True

    handler.emit(record)

    assert session.snapshot()["logs"] == []


def test_mirror_session_log_writes_once_to_session_and_logger(caplog):
    session = TaskSession()

    with caplog.at_level(logging.WARNING):
        mirror_session_log(session, logging.getLogger("test.pipeline"), "取消任務", "warning")

    assert [(entry.level, entry.text) for entry in session.snapshot()["logs"]] == [
        ("warning", "取消任務")
    ]
    assert [record.message for record in caplog.records] == ["取消任務"]
    assert caplog.records[0].ui_mirror is True
