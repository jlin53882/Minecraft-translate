"""app/logging.log_entry 向後兼容 re-export。

新 code 請從 app.views._log.log_entry import。

PR refactor/unified-log-view: 把實作搬到 app.views._log.log_entry，
這裡保留 stub 避免破壞既有 from app.logging.log_entry import LogEntry。
"""

from app.views._log.log_entry import LogEntry, LogLevel

__all__ = ["LogEntry", "LogLevel"]