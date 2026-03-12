"""app/views/cache_manager/cache_presenter.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from .cache_types import ActionState, CacheUiState


class CachePresenter:
    """Cache UI 的顯示層轉換器（Presenter）。

    功能：把內部狀態（busy/reason/phase）轉成 UI 顯示用的中文 label。

    維護注意：
    - _STATUS_MAP / _PHASE_MAP 是 UI 文案的單一來源；改文案時先改這裡。
    - action_id/phase 主要用於 trace 與 log；不建議把 UI 邏輯散落在 cache_view.py。
    """

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
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`upper`

        回傳：依函式內 return path。
        """
        if not state.busy:
            return self._STATUS_MAP["READY"]
        reason = (state.reason or "").strip().upper()
        return self._STATUS_MAP.get(reason, state.reason or "處理中")

    def status_text(self, state: CacheUiState) -> str:
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`status_label`

        回傳：依函式內 return path。
        """
        label = self.status_label(state)
        return f"狀態：{label}" + ("..." if state.busy else "")

    def phase_label(self, phase: str) -> str:
        """處理此函式的工作（細節以程式碼為準）。

        回傳：依函式內 return path。
        """
        return self._PHASE_MAP.get((phase or "").strip().lower(), phase or "next")

    def action_trace(self, action: ActionState) -> str:
        """處理此函式的工作（細節以程式碼為準）。

        回傳：依函式內 return path。
        """
        return f"trace: ACTION#{action.action_id} {self.phase_label(action.phase)} {action.reason}"

    def action_log(self, action: ActionState) -> str:
        """處理此函式的工作（細節以程式碼為準）。

        回傳：依函式內 return path。
        """
        return f"[ACTION#{action.action_id}] {self.phase_label(action.phase)} {action.reason}"
