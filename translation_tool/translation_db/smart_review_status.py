"""智慧校對候選狀態的單一事實來源。

資料層（SQLite／JSON history）、服務層與 UI 都從這裡取得狀態名稱、分組與允許的
狀態轉移。``SmartReviewStatus`` 是 ``StrEnum``，值與既有 JSON／SQLite 內保存的字串相同，
因此舊資料不需遷移，``status == "ready"`` 這類比較也維持成立。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class SmartReviewStatus(StrEnum):
    """候選（candidate）在審查流程中的狀態。"""

    PENDING = "pending"
    READY = "ready"
    NO_CHANGE = "no_change"
    NEEDS_CONTEXT = "needs_context"
    INVALID = "invalid"
    REQUEST_FAILED = "request_failed"
    CANCELLED = "cancelled"
    APPLIED = "applied"
    REJECTED = "rejected"
    STALE = "stale"
    INVALID_FORMAT = "invalid_format"


class ProjectionState(StrEnum):
    """JSON 候選投影相對於 SQLite 套用紀錄（audit）的同步狀態。

    SQLite audit 是「寫入事實」，JSON 是「候選狀態投影」：

    * ``PROJECTED``：JSON 已依最近一次寫入更新；
    * ``PENDING_PROJECTION``：audit 已提交，JSON 尚未反映（寫入失敗或尚在回寫）；
    * ``PROJECTION_FAILED``：回寫 JSON 失敗且已放棄本次重試，等待下一次對帳；
    * ``RECONCILED``：最近一次 JSON 變更由對帳依 audit 補回。
    """

    PROJECTED = "projected"
    PENDING_PROJECTION = "pending_projection"
    PROJECTION_FAILED = "projection_failed"
    RECONCILED = "reconciled"


S = SmartReviewStatus

# AI 已處理完（不論結果好壞）的狀態；不含尚待處理的 pending 與被取消的 cancelled。
PROCESSED_STATUSES: Final = frozenset(
    {S.READY, S.NO_CHANGE, S.NEEDS_CONTEXT, S.INVALID, S.REQUEST_FAILED}
)
# AI 回應通過驗證的狀態。
VALID_STATUSES: Final = frozenset({S.READY, S.NO_CHANGE, S.NEEDS_CONTEXT})
# 審查者可手動編輯並覆蓋套用的狀態；``ready`` 走 AI 建議套用流程。
MANUAL_EDIT_STATUSES: Final = frozenset(
    {
        S.APPLIED,
        S.REJECTED,
        S.NEEDS_CONTEXT,
        S.NO_CHANGE,
        S.INVALID,
        S.INVALID_FORMAT,
        S.STALE,
    }
)

# AI decision → 候選狀態。
DECISION_STATUS: Final = {
    "no_change": S.NO_CHANGE,
    "suggest_revision": S.READY,
    "needs_context": S.NEEDS_CONTEXT,
}

# 允許的狀態轉移：來源 → 可到達的目標。
# - pending 只會被 AI 處理結果或取消改變。
# - ready 由人工套用／保留，或在套用當下偵測到過期／格式不符。
# - 手動編輯狀態可再次人工套用（覆蓋），ready 以外的狀態不能被 reject。
# - AI 處理結果（invalid／request_failed／cancelled）可由重新執行回到 AI 結果狀態。
_AI_RESULTS: Final = frozenset(PROCESSED_STATUSES)
TRANSITIONS: Final[dict[SmartReviewStatus, frozenset[SmartReviewStatus]]] = {
    S.PENDING: _AI_RESULTS | {S.CANCELLED},
    S.READY: frozenset({S.APPLIED, S.REJECTED, S.STALE, S.INVALID_FORMAT}),
    S.NO_CHANGE: frozenset({S.APPLIED}) | _AI_RESULTS,
    S.NEEDS_CONTEXT: frozenset({S.APPLIED}) | _AI_RESULTS,
    S.INVALID: frozenset({S.APPLIED}) | _AI_RESULTS,
    S.REQUEST_FAILED: _AI_RESULTS,
    S.CANCELLED: _AI_RESULTS,
    S.APPLIED: frozenset({S.APPLIED}),
    S.REJECTED: frozenset({S.APPLIED}),
    S.STALE: frozenset({S.APPLIED}),
    S.INVALID_FORMAT: frozenset({S.APPLIED}),
}


class InvalidStatusTransition(ValueError):
    """嘗試進行狀態機不允許的狀態轉移。"""

    def __init__(self, old: str, new: str, reason: str = "") -> None:
        self.old = old
        self.new = new
        self.reason = reason or f"不允許的狀態轉移：{old} → {new}"
        super().__init__(self.reason)


def coerce_status(value: object, default: SmartReviewStatus = S.PENDING) -> str:
    """把 JSON／SQLite 內的狀態值轉成 ``SmartReviewStatus``；空值用 ``default``，未知字串原樣保留。"""
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return SmartReviewStatus(text)
    except ValueError:
        return text


def can_transition(old: object, new: object) -> bool:
    """``old`` 是否可轉移到 ``new``；未知狀態一律不允許。"""
    try:
        return SmartReviewStatus(str(new)) in TRANSITIONS[SmartReviewStatus(str(old))]
    except (KeyError, ValueError):
        return False


def validate_transition(old: object, new: object) -> None:
    """不允許的轉移丟出 ``InvalidStatusTransition``（含可寫入 audit 的原因）。"""
    if not can_transition(old, new):
        raise InvalidStatusTransition(str(old), str(new))


def status_for_decision(decision: object) -> SmartReviewStatus:
    """AI decision 對應的候選狀態；未知 decision 視為 ``invalid``。"""
    return DECISION_STATUS.get(str(decision), S.INVALID)
