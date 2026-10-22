"""智慧校對批量面板的唯讀候選頁載入。"""

from __future__ import annotations

from app.services_impl.moddb_smart_review_service import SmartReviewCandidate


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
) -> tuple[dict | None, dict | None, int, list[SmartReviewCandidate]]:
    """在同一個唯讀 session 讀取批次資訊、必要統計與目前頁。"""
    with read_session(path, priority) as session:
        info = session.get_run(run_id) if include_run else None
        breakdown = session.run_breakdown(run_id) if include_summary else None
        total, rows = session.candidate_page(
            run_id,
            status=status,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    return info, breakdown, int(total or 0), list(rows or [])


def should_load_summary(include_run: bool, summary_loaded: bool) -> bool:
    """批次切換或尚未有摘要時才讀取整批狀態統計。"""
    return include_run or not summary_loaded
