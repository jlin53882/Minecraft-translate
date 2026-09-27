"""UISessionLogHandler：UI 只顯示訊息本身，且保留 logging 等級。"""

import logging

from app.views._log.task_session import TaskSession
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
