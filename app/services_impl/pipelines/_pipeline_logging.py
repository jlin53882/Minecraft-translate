"""Shared logging bootstrap for pipeline service wrappers.

PR33: deduplicate repeated logger-config bootstrap logic.
"""

from __future__ import annotations

from app.services_impl.config_service import _load_app_config
from app.services_impl.logging_service import (
    update_logger_config as apply_logger_config,
)
from app.tasks.task_session import add_log_unmirrored
from translation_tool.utils.ui_mirror import mirror_to_backend


def mirror_session_log(
    session, logger, text: str, level: str = "info", *, prefix: str = ""
) -> None:
    """將一則任務訊息同時寫入 TaskSession 與指定模組的後台 logger。

    ``TaskSession.add_log`` 本身就會自動鏡像到通用的 ``app.ui`` logger；這個 helper 給
    「想讓 log 檔顯示是哪個模組發出的」或「要加後台專屬前綴」的服務使用，
    所以先以 ``mirror=False`` 寫 session，再自己寫進指定 logger（一則訊息只會在後台出現一次）。
    ``ui_mirrored`` 標記讓 ``UISessionLogHandler`` 略過，避免回灌 UI 造成畫面重複。
    ``prefix`` 只加在後台，UI 維持原文。
    """
    add_log_unmirrored(session, text, level)
    mirror_to_backend(text, level, prefix=prefix, logger=logger)


def ensure_pipeline_logging():
    """在每次流水線執行前刷新日誌配置，確保 translation_tool 的日誌行為符合預期。"""
    return apply_logger_config(_load_app_config, logger_name="translation_tool")
