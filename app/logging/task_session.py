"""app.logging.task_session 向後兼容 re-export。

新 code 請從 app.views._log.task_session import。

PR refactor/unified-log-view: 把實作搬到 app.views._log.task_session，
這裡保留 stub 避免破壞既有 from app.logging.task_session import TaskSession
（10+ 個檔案用到：services、views、tests）。
"""

from app.views._log.task_session import TaskSession

__all__ = ["TaskSession"]