"""app.logging.log_config 向後兼容 re-export。

新 code 請從 app.views._log.log_config import。

PR refactor/unified-log-view: 把實作搬到 app.views._log.log_config，
這裡保留 stub 避免破壞既有 from app.logging.log_config import load_ui_logging_config 等。
"""

from app.views._log.log_config import (
    DEFAULT_UI_LOGGING,
    load_ui_logging_config,
)

__all__ = ["DEFAULT_UI_LOGGING", "load_ui_logging_config"]