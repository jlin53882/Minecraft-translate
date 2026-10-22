"""智慧校對審查頁的狀態：目前批次、篩選、頁碼、選取候選、草稿與非同步請求序號。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services_impl.moddb_smart_review_service import (
    SmartReviewCandidate,
    SmartReviewStatus,
)

ALL_FILTER = "all"
DEFAULT_FILTER = SmartReviewStatus.READY
# 篩選器：(狀態值, 顯示名稱)；``all`` 表示不篩選。
FILTERS: tuple[tuple[str, str], ...] = (
    (SmartReviewStatus.READY, "可套用"),
    (SmartReviewStatus.APPLIED, "已套用"),
    (SmartReviewStatus.REJECTED, "已保留原譯文"),
    (SmartReviewStatus.NEEDS_CONTEXT, "需補 context"),
    (SmartReviewStatus.NO_CHANGE, "AI 認為不用改"),
    (SmartReviewStatus.INVALID, "無效（AI 回應不合格）"),
    (SmartReviewStatus.INVALID_FORMAT, "格式不符"),
    (SmartReviewStatus.STALE, "已過期"),
    (ALL_FILTER, "全部"),
)
STATUS_LABEL = dict(FILTERS)


@dataclass
class SmartReviewReviewerState:
    """審查頁在畫面之外需要記住的一切；控制項只負責顯示它。

    ``begin_request`` 每次發出會改寫畫面狀態的非同步讀取就遞增序號，回來時以
    ``is_current`` 判斷是否已被更新的請求取代；過期的結果必須丟棄，否則快速切換
    候選或篩選時，慢回來的舊結果會蓋掉新狀態。
    """

    run_id: str | None = None
    run_info: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    status_filter: str = DEFAULT_FILTER
    position: int = 0
    total: int = 0
    row: SmartReviewCandidate | None = None
    busy: bool = False
    stored_draft: str = ""
    has_format_issue: bool = False
    _request_seq: int = 0

    def begin_request(self) -> int:
        """登記一次新的非同步讀取並回傳它的序號；先前的請求因此全部過期。"""
        self._request_seq += 1
        return self._request_seq

    def is_current(self, token: int) -> bool:
        """``token`` 是否仍是最新的請求序號。"""
        return token == self._request_seq

    @property
    def status_query(self) -> str | None:
        """傳給 service 的狀態條件；「全部」不篩選。"""
        return None if self.status_filter == ALL_FILTER else self.status_filter

    @property
    def total_count(self) -> int:
        """批次內所有狀態的候選總數（「全部」篩選器顯示用）。"""
        return sum(self.counts.values())

    def prefer_ready_filter(self) -> str:
        """載入批次後的初始篩選：有可套用候選看「可套用」，否則看全部。"""
        has_ready = self.counts.get(SmartReviewStatus.READY, 0)
        return SmartReviewStatus.READY if has_ready else ALL_FILTER

    def ready_queue_finished(self) -> bool:
        """「可套用」佇列已清空，且批次內有處理過（已套用或保留）的候選。"""
        processed = self.counts.get(SmartReviewStatus.APPLIED, 0) + self.counts.get(
            SmartReviewStatus.REJECTED, 0
        )
        return self.status_filter == SmartReviewStatus.READY and processed > 0

    def clamp_position(self, requested: int, total: int) -> int:
        """位置超出範圍時退回最後一筆；沒有候選時為 0。"""
        return min(requested, total - 1) if total else 0

    def draft_changed(self, draft: str) -> bool:
        """``ready`` 候選的草稿是否與已儲存內容不同。"""
        return (
            self.row is not None
            and self.row.status == SmartReviewStatus.READY
            and draft != self.stored_draft
        )
