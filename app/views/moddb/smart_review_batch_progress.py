"""智慧校對批量操作的節流進度回報。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.views.moddb.smart_review_context import SmartReviewContext

PROGRESS_UPDATE_INTERVAL_SECONDS = 0.15


class SmartReviewBatchProgress:
    """把 worker 的逐筆進度限制在每 150ms 至多發布一次。"""

    def __init__(
        self,
        ctx: SmartReviewContext,
        operation: int,
        callback: Callable[[int, dict[str, Any]], None],
    ) -> None:
        self.ctx = ctx
        self.operation = operation
        self.callback = callback
        self._last_posted_at = 0.0

    def __call__(self, snapshot: dict[str, Any]) -> None:
        """節流中間進度，但保留最後一筆完成進度。"""
        if self.ctx.closed:
            return
        processed = int(snapshot.get("processed", 0) or 0)
        total = int(snapshot.get("total", 0) or 0)
        now = time.monotonic()
        if (
            processed < total
            and now - self._last_posted_at < PROGRESS_UPDATE_INTERVAL_SECONDS
        ):
            return
        self._last_posted_at = now
        self.ctx.post(self.callback, self.operation, snapshot)


__all__ = ["SmartReviewBatchProgress"]
