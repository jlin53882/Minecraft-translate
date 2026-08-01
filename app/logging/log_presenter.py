"""app.logging.log_presenter 向後兼容 re-export。

新 code 請從 app.views._log.log_presenter import。

PR refactor/unified-log-view: 把實作搬到 app.views._log.log_presenter，
這裡保留 stub 避免破壞既有 from app.logging.log_presenter import LogPresenter。
"""

from app.views._log.log_presenter import LogPresenter

__all__ = ["LogPresenter"]