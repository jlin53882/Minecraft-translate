"""批量審查單筆結果（apply outcome）的分類：成功、略過、錯誤，確保「完成 N／M」不漏計。

資料層回傳的單筆狀態是字面值字串；這裡把已知字面值集中在單一列舉，並把每個結果
歸入且僅歸入一個群組。未知字面值一律視為「略過」而不是被悄悄丟掉，這樣
群組筆數加總恆等於已處理筆數。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services_impl.moddb_smart_review_service import (
    SmartReviewBatchResult,
    SmartReviewStatus,
)


class BatchOutcome(StrEnum):
    """資料層批量審查可能回傳的單筆狀態字面值。"""

    APPLIED = "applied"
    REJECTED = "rejected"
    ERROR = "error"
    NOT_READY = "not_ready"
    MISSING = "missing"
    MANUAL_REVIEWED = "manual_reviewed"
    NOT_STALE = "not_stale"
    NOT_EDITABLE = "not_editable"
    UNSUPPORTED = "unsupported"
    INVALID_DRAFT = "invalid_draft"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    MISSING_ENTRY = "missing_entry"
    STALE = SmartReviewStatus.STALE.value
    INVALID_FORMAT = SmartReviewStatus.INVALID_FORMAT.value


class BatchOutcomeGroup(StrEnum):
    """畫面摘要使用的群組；每筆結果恰好屬於其中一個。"""

    APPLIED = "applied"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    ERROR = "error"


SUCCESS_OUTCOMES = frozenset({BatchOutcome.APPLIED, BatchOutcome.REJECTED})


def classify_outcome(status: str) -> BatchOutcomeGroup:
    """把單筆狀態歸入摘要群組；未知狀態視為略過，不會從統計中消失。"""
    if status == BatchOutcome.APPLIED:
        return BatchOutcomeGroup.APPLIED
    if status == BatchOutcome.REJECTED:
        return BatchOutcomeGroup.REJECTED
    if status == BatchOutcome.ERROR:
        return BatchOutcomeGroup.ERROR
    return BatchOutcomeGroup.SKIPPED


@dataclass(frozen=True)
class BatchOutcomeSummary:
    """批量結果依群組統計；``total`` 恆等於各群組加總。"""

    applied: int = 0
    rejected: int = 0
    skipped: int = 0
    error: int = 0

    @property
    def total(self) -> int:
        """已分類的筆數，等於已處理筆數。"""
        return self.applied + self.rejected + self.skipped + self.error

    @property
    def succeeded(self) -> int:
        """真正寫入資料庫的筆數（套用加保留原譯文）。"""
        return self.applied + self.rejected


def summarize_outcomes(result: SmartReviewBatchResult) -> BatchOutcomeSummary:
    """逐筆分類批量結果並統計。"""
    counts = dict.fromkeys(BatchOutcomeGroup, 0)
    for item in result.items:
        counts[classify_outcome(item.status)] += 1
    return BatchOutcomeSummary(
        applied=counts[BatchOutcomeGroup.APPLIED],
        rejected=counts[BatchOutcomeGroup.REJECTED],
        skipped=counts[BatchOutcomeGroup.SKIPPED],
        error=counts[BatchOutcomeGroup.ERROR],
    )
