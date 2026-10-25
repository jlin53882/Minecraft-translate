"""智慧校對批量面板的唯讀候選頁載入。"""

from __future__ import annotations

import logging
import time

from app.services_impl.moddb_smart_review_service import SmartReviewCandidate

logger = logging.getLogger(__name__)


def load_batch_page(
    path,
    priority,
    run_id: str,
    status: str | None,
    page: int,
    page_size: int,
    read_session,
    *,
    include_run: bool,
    include_summary: bool,
    known_total: int | None = None,
) -> tuple[dict | None, dict | None, int, list[SmartReviewCandidate]]:
    """在同一個唯讀 session 讀取批次資訊、必要統計與目前頁。

    ``known_total`` 用於沿用對話框共用的批次摘要，避免執行中的批次在
    摘要與候選頁之間變動時，兩個審查模式各自顯示不同的總數。
    """
    started = time.perf_counter()
    with read_session(path, priority) as session:
        info = session.get_run(run_id) if include_run else None
        breakdown = session.run_breakdown(run_id) if include_summary else None
        total, rows = session.candidate_page(
            run_id,
            status=status,
            limit=page_size,
            offset=(page - 1) * page_size,
            known_total=known_total,
        )
    logger.debug(
        "Smart Review 批量頁載入：run=%s page=%s status=%s rows=%s elapsed_ms=%.1f",
        run_id,
        page,
        status or "all",
        len(rows or []),
        (time.perf_counter() - started) * 1000,
    )
    return info, breakdown, int(total or 0), list(rows or [])


def should_load_summary(include_run: bool, summary_loaded: bool) -> bool:
    """批次切換或尚未有摘要時才讀取整批狀態統計。"""
    return include_run or not summary_loaded
