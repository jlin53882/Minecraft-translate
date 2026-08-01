"""app/views/_log/

統一的 log 子系統（家豪要求「log 相關全部集中」）。

提供：
- LogEntry: 結構化日誌事件
- LogLevel: 日誌等級枚舉
- TaskSession: 長任務狀態容器
- LogPresenter: 渲染策略（append/tail）
- LogView: 統一 log 顯示 widget（深色容器 + 等寬字）🆕
- log_colors: 等級顏色對應（從 theme 來）
- load_ui_logging_config: UI 設定讀取

PR refactor/unified-log-view: 從 app/logging/ 搬過來，LogView 是新 widget。
"""

from .log_entry import LogEntry, LogLevel
from .task_session import TaskSession
from .log_presenter import LogPresenter
from .log_view import LogView
from .log_config import load_ui_logging_config, DEFAULT_UI_LOGGING
from .log_colors import (
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
    # Widget 🆕
    "LogView",
    # Config
    "load_ui_logging_config",
    "DEFAULT_UI_LOGGING",
    # Colors
    "COLOR_MAP",
    "LEVEL_PREFIX",
    "get_level_color",
    "get_level_prefix",
]