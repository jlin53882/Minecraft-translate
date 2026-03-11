from __future__ import annotations

from .cache_types import ActionState, CacheUiState


class CachePresenter:
    _STATUS_MAP = {
        "": "就緒",
        "READY": "就緒",
        "SAVING": "儲存中",
        "RELOADING": "重新載入中",
        "NEXT": "準備下一步",
        "ERROR": "錯誤",
        "CANCELLED": "已取消",
    }

    _PHASE_MAP = {
        "start": "start",
        "success": "success",
        "error": "error",
        "cancelled": "cancelled",
        "finish": "ready",
        "next": "next",
    }

    def status_label(self, state: CacheUiState) -> str:
        if not state.busy:
            return self._STATUS_MAP["READY"]
        reason = (state.reason or "").strip().upper()
        return self._STATUS_MAP.get(reason, state.reason or "處理中")

    def status_text(self, state: CacheUiState) -> str:
        label = self.status_label(state)
        return f"狀態：{label}" + ("..." if state.busy else "")

    def phase_label(self, phase: str) -> str:
        return self._PHASE_MAP.get((phase or "").strip().lower(), phase or "next")

    def action_trace(self, action: ActionState) -> str:
        return f"trace: ACTION#{action.action_id} {self.phase_label(action.phase)} {action.reason}"

    def action_log(self, action: ActionState) -> str:
        return f"[ACTION#{action.action_id}] {self.phase_label(action.phase)} {action.reason}"
