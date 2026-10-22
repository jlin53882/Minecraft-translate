"""AI 機翻修復審查（repair review）的狀態詞彙。

與 ``smart_review_status.SmartReviewStatus``（智慧校對候選）是兩套獨立的狀態，不可混用。
值就是寫進 ``*.repair-review.sqlite3`` 的字串（資料格式不變），``StrEnum`` 與裸字串可互相比較。
"""

from __future__ import annotations

from enum import StrEnum


class RepairReviewStatus(StrEnum):
    """單筆修復審查項目的持久化狀態（``repair_review.status`` 欄位）。"""

    PENDING = "pending"  # 待審查：尚未套用
    KEPT_OLD = "kept_old"  # 使用者選擇保留舊譯文
    APPLIED = "applied"  # 已套用（含「現值已等於草稿」）
    STALE = "stale"  # 套用被拒，沒有寫入（原因見 RepairStaleReason）
    INVALID = "invalid"  # 草稿待使用者確認


class RepairApplyOutcome(StrEnum):
    """套用一筆修復草稿的結果；未寫入時會附上 ``stale:<原因>``（見 :func:`stale_outcome`）。"""

    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"  # 現值已等於草稿，不需要再寫入
    STALE = "stale"


class RepairStaleReason(StrEnum):
    """套用被拒的原因碼（``stale:`` 後面的部分）；``UNCLASSIFIED`` 是沒有可歸類的原因。"""

    CHANGED = "changed"
    NOT_NEEDED = "not_needed"
    MISSING = "missing"
    ALREADY_APPLIED = "already_applied"
    UNCLASSIFIED = ""


REPAIR_REVIEW_STATUS_LABELS: dict[RepairReviewStatus, str] = {
    RepairReviewStatus.PENDING: "待審查",
    RepairReviewStatus.APPLIED: "已套用",
    RepairReviewStatus.KEPT_OLD: "保留舊譯文",
    RepairReviewStatus.STALE: "未寫入（見原因）",
    RepairReviewStatus.INVALID: "待確認草稿",
}

# 已完成審查、不能再從審查畫面修改的狀態。
REPAIR_FINISHED_STATUSES: frozenset[RepairReviewStatus] = frozenset(
    {RepairReviewStatus.APPLIED, RepairReviewStatus.KEPT_OLD}
)
# 仍待使用者確認的狀態（進度摘要的「待確認」）。
REPAIR_UNRESOLVED_STATUSES: tuple[RepairReviewStatus, ...] = (
    RepairReviewStatus.PENDING,
    RepairReviewStatus.INVALID,
)
# 重新讀取基準時會一併重設的狀態。
REPAIR_REBASE_STATUSES: tuple[RepairReviewStatus, ...] = (
    RepairReviewStatus.PENDING,
    RepairReviewStatus.INVALID,
    RepairReviewStatus.STALE,
)
# 套用結果中代表「資料庫現值已是草稿」的結果。
REPAIR_APPLY_DONE_OUTCOMES: frozenset[RepairApplyOutcome] = frozenset(
    {RepairApplyOutcome.APPLIED, RepairApplyOutcome.ALREADY_APPLIED}
)

_STALE_PREFIX = f"{RepairApplyOutcome.STALE}:"


def stale_outcome(reason: str) -> str:
    """組出未寫入的套用結果：有原因為 ``stale:<原因>``，否則為 ``stale``。"""
    return f"{_STALE_PREFIX}{reason}" if reason else str(RepairApplyOutcome.STALE)


def split_stale_reason(outcome: str) -> str:
    """取出套用結果 ``stale:<原因>`` 的原因部分；沒有原因回傳空字串。"""
    return outcome.partition(":")[2]


def status_after_apply(outcome: str) -> RepairReviewStatus:
    """套用結果對應要寫回審查項目的狀態。"""
    if outcome in REPAIR_APPLY_DONE_OUTCOMES:
        return RepairReviewStatus.APPLIED
    return RepairReviewStatus.STALE
