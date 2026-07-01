"""app.logging.log_colors 向後兼容 re-export。

新 code 請從 app.views._log.log_colors import。

PR refactor/unified-log-view: 把實作搬到 app.views._log.log_colors，
這裡保留 stub 避免破壞既有 from app.logging.log_colors import get_level_color 等。
"""

from app.views._log.log_colors import (
    COLOR_MAP,
    LEVEL_PREFIX,
    get_level_color,
    get_level_prefix,
)

__all__ = ["COLOR_MAP", "LEVEL_PREFIX", "get_level_color", "get_level_prefix"]