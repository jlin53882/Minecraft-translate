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

TaskSession / LogEntry 已搬到 app.tasks（中立，不依賴 UI）；這裡只留 UI 相關的日誌元件。
"""

from app.tasks import LogEntry, LogLevel, TaskSession

from .log_colors import (
    COLOR_MAP,
    LEVEL_PREFIX,
    get_level_color,
    get_level_prefix,
)
from .log_config import DEFAULT_UI_LOGGING, load_ui_logging_config
from .log_presenter import LogPresenter
from .log_view import LogView

__all__ = [
    # Colors
    "COLOR_MAP",
    "DEFAULT_UI_LOGGING",
    "LEVEL_PREFIX",
    # Core
    "LogEntry",
    "LogLevel",
    # Presenter
    "LogPresenter",
    # Widget 🆕
    "LogView",
    "TaskSession",
    "get_level_color",
    "get_level_prefix",
    # Config
    "load_ui_logging_config",
]
