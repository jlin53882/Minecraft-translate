"""app/logging/

向後兼容層：re-export 從 app.views._log。

PR refactor/unified-log-view: log 相關實作全部搬到 app.views._log，
這裡保留薄殼 re-export 讓既有 from app.logging import ... 仍可用。

新 code 請從 app.views._log import。
"""

from app.views._log.log_entry import LogEntry, LogLevel
from app.views._log.task_session import TaskSession
from app.views._log.log_presenter import LogPresenter
from app.views._log.log_config import load_ui_logging_config, DEFAULT_UI_LOGGING
from app.views._log.log_colors import (
    COLOR_MAP,
    LEVEL_PREFIX,
    get_level_color,
    get_level_prefix,
)

__all__ = [
    # Core
    "LogEntry",
    "LogLevel",
    "TaskSession",
    # Presenter
    "LogPresenter",
    # Config
    "load_ui_logging_config",
    "DEFAULT_UI_LOGGING",
    # Colors
    "COLOR_MAP",
    "LEVEL_PREFIX",
    "get_level_color",
    "get_level_prefix",
]