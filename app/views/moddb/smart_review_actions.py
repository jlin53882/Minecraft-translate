"""智慧校對審查頁的動作服務：儲存草稿、套用、保留原譯文，並把 service 結果轉成提示訊息。

動作都在背景執行並以穩定的資料庫身分提交（寫入本身會讓 cache generation 改變，內容衝突交給
service 層的 CAS）。完成後以 ``ActionOutcome`` 回報要顯示的訊息與是否需要通知外層重載條目頁，
由審查頁決定如何呈現；這裡不碰 Flet 控制項。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.services_impl.moddb_smart_review_service import (
    SmartReviewCandidate,
    SmartReviewStatus,
    apply_moddb_smart_review_candidate_from_path,
    apply_moddb_smart_review_manual_edit_from_path,
    reject_moddb_smart_review_candidate_from_path,
    save_moddb_smart_review_draft_from_path,
)
from app.tasks.operation_registry import CommitPolicy, DurabilityPolicy
from app.ui.design import C
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_evidence import is_manual_edit_row

DRAFT_SAVED = "saved"
_APPLY_MESSAGES = {
    SmartReviewStatus.APPLIED: (
        "已套用 1 筆人工（未審核）譯文；原 AI 譯文保留。",
        C.EM,
    ),
    SmartReviewStatus.STALE: (
        "資料庫內容已變更，這筆候選已過期；未覆蓋目前譯文。",
        C.GOLD,
    ),
    "manual_reviewed": (
        "目前已有人工審核過的譯文，不會被覆蓋；請到條目校對編輯。",
        C.GOLD,
    ),
    SmartReviewStatus.INVALID_FORMAT: ("草稿格式未通過檢查，未套用。", C.GOLD),
    "invalid_draft": ("草稿不可為空白。", C.GOLD),
    "not_ready": ("這筆目前不是可套用的狀態。", C.GOLD),
    "missing": ("找不到這筆候選。", C.GOLD),
}
# 人工編輯套用（任何非 ready 的可編輯狀態）可能回傳的結果。
_MANUAL_EDIT_MESSAGES = {
    **_APPLY_MESSAGES,
    SmartReviewStatus.APPLIED: ("已套用人工修改（未審核）；可從條目歷史還原。", C.EM),
    "changed": (
        "資料庫內容在載入後已變更，未覆蓋；已重新載入這筆，請確認目前譯文後再套用。",
        C.GOLD,
    ),
    "state_sync_failed": (
        "譯文已寫入（可從條目歷史還原），但批次狀態同步失敗；重新載入這個批次時會自動補回。",
        C.GOLD,
    ),
    "unchanged": ("草稿與目前譯文相同，沒有需要套用的變更。", C.GOLD),
    "not_editable": ("這筆目前不能人工編輯。", C.GOLD),
    "unsupported": ("舊版批次不支援人工編輯套用；請重新校對。", C.GOLD),
}
# state_sync_failed：翻譯已提交，條目頁仍要重載。
_PARENT_RELOAD_STATUSES = frozenset({SmartReviewStatus.APPLIED, "state_sync_failed"})
# 這些結果會改變審查佇列；即使不需要重載外層條目頁，也要通知同視窗的另一個審查面板。
_REVIEW_CHANGED_STATUSES = frozenset(
    {SmartReviewStatus.APPLIED, SmartReviewStatus.REJECTED, "state_sync_failed"}
)


@dataclass(frozen=True)
class ActionOutcome:
    """一次動作的結果：提示、外層條目頁刷新與審查面板同步所需的狀態。"""

    status: str | None
    message: str | None
    color: str
    error: str | None = None
    reload_parent: bool = False
    review_changed: bool = False

    @property
    def failed(self) -> bool:
        """背景工作本身是否失敗（例外），而不是 service 回報了不可處理的狀態。"""
        return self.error is not None


class SmartReviewActionService:
    """對單筆候選執行寫入動作，並把結果整理成 ``ActionOutcome``。"""

    def __init__(self, ctx: SmartReviewContext) -> None:
        """保存共用的背景執行上下文。"""
        self._ctx = ctx

    def save_draft(
        self,
        database: tuple[Path, tuple[int, ...]],
        row: SmartReviewCandidate,
        draft: str,
        on_done: Callable[[ActionOutcome], None],
        *,
        quiet: bool = False,
    ) -> None:
        """儲存草稿；``quiet`` 時成功不提示（換筆前的自動儲存）。"""
        path, priority = database

        def work() -> str:
            """儲存草稿並回傳 service 狀態。"""
            return save_moddb_smart_review_draft_from_path(
                path, priority, row.run_id, row.ordinal, draft
            )

        def done(status, error: str | None) -> None:
            """整理草稿儲存結果。"""
            if error:
                outcome = ActionOutcome(None, f"草稿儲存失敗：{error}", C.RED, error)
            elif status == DRAFT_SAVED:
                message = None if quiet else "草稿已儲存，尚未改寫正式譯文。"
                outcome = ActionOutcome(status, message, C.EM)
            else:
                outcome = ActionOutcome(status, f"草稿目前無法儲存：{status}", C.GOLD)
            on_done(outcome)

        self._launch(work, done, "儲存智慧校對草稿")

    def apply(
        self,
        database: tuple[Path, tuple[int, ...]],
        row: SmartReviewCandidate,
        draft: str,
        on_done: Callable[[ActionOutcome], None],
    ) -> None:
        """套用目前草稿：``ready`` 走 AI 建議流程，其餘可編輯狀態走人工編輯。"""
        path, priority = database
        manual = is_manual_edit_row(row)
        messages = _MANUAL_EDIT_MESSAGES if manual else _APPLY_MESSAGES

        def work() -> str:
            """以 CAS 條件套用候選（ready 走 AI 建議流程，其餘走人工編輯）。"""
            if manual:
                return apply_moddb_smart_review_manual_edit_from_path(
                    path, priority, row.run_id, row.ordinal, draft
                )
            return apply_moddb_smart_review_candidate_from_path(
                path, priority, row.run_id, row.ordinal, draft_zh_tw=draft
            )

        self._launch(
            work,
            lambda status, error: on_done(
                self._outcome(status, error, "套用失敗", messages, notify_parent=True)
            ),
            "套用智慧校對候選",
        )

    def reject(
        self,
        database: tuple[Path, tuple[int, ...]],
        row: SmartReviewCandidate,
        on_done: Callable[[ActionOutcome], None],
    ) -> None:
        """保留原譯文並把這筆候選標為已處理。"""
        path, priority = database

        def work() -> str:
            """標記候選為保留原譯文並回傳 service 狀態。"""
            return reject_moddb_smart_review_candidate_from_path(
                path, priority, row.run_id, row.ordinal
            )

        self._launch(
            work,
            lambda status, error: on_done(
                self._outcome(
                    status, error, "保留失敗", _APPLY_MESSAGES, notify_parent=False
                )
            ),
            "保留智慧校對原譯文",
        )

    def _launch(self, work, done, name: str) -> None:
        """以寫入動作的提交與持久性策略在背景執行。"""
        self._ctx.run_background(
            work,
            done,
            name=name,
            commit=CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
            stable_identity=True,
        )

    @staticmethod
    def _outcome(
        status,
        error: str | None,
        failure: str,
        messages: dict,
        *,
        notify_parent: bool,
    ) -> ActionOutcome:
        """把套用／保留的 service 狀態對應成提示訊息與是否重載條目頁。"""
        if error:
            return ActionOutcome(None, f"{failure}：{error}", C.RED, error)
        if status == SmartReviewStatus.REJECTED:
            message, color = "已保留原譯文，這筆不會套用。", C.EM
        else:
            message, color = messages.get(
                status, (f"這筆目前無法處理：{status}", C.GOLD)
            )
        return ActionOutcome(
            status,
            message,
            color,
            reload_parent=notify_parent and status in _PARENT_RELOAD_STATUSES,
            review_changed=status in _REVIEW_CHANGED_STATUSES,
        )
