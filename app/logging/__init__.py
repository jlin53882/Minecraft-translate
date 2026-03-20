"""app/logging/

Logging System Unification — 統一日誌系統模組。

本模組提供：
- LogEntry: 結構化日誌事件
- TaskSession: 支援 LogEntry 的任務狀態容器
- LogPresenter: 兩種渲染模式（append / tail）
- log_config: ui_logging 設定讀取與 normalize
- log_colors: 等級 → 顏色對應
"""

from .log_entry import LogEntry, LogLevel
from .task_session import TaskSession
from .log_presenter import LogPresenter
from .log_config import load_ui_logging_config, DEFAULT_UI_LOGGING
from .log_colors import get_level_color, get_level_prefix

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
    "get_level_color",
    "get_level_prefix",
]
