"""Shared logging bootstrap for pipeline service wrappers.

PR33: deduplicate repeated logger-config bootstrap logic.
"""

from __future__ import annotations

from app.services_impl.config_service import _load_app_config
from app.services_impl.logging_service import (
    update_logger_config as apply_logger_config,
)


def mirror_session_log(session, logger, text: str, level: str = "info") -> None:
    """將一則任務訊息同時寫入 TaskSession 與後台 log。

    ``ui_mirrored`` 會由 ``UISessionLogHandler`` 識別，避免後台 log
    再次回灌同一個 session，造成 UI 顯示重複訊息。
    """
    try:
        session.add_log(text, level=level)
    except TypeError:
        # 相容仍使用舊版純文字 add_log(text) 介面的測試替身或舊版 session。
        session.add_log(text)
    numeric = {"debug": 10, "info": 20, "warning": 30, "error": 40}.get(level, 20)
    logger.log(numeric, text, extra={"ui_mirrored": True})


def ensure_pipeline_logging():
    """在每次流水線執行前刷新日誌配置，確保 translation_tool 的日誌行為符合預期。"""
    return apply_logger_config(_load_app_config, logger_name="translation_tool")
