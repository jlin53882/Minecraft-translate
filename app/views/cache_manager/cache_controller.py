"""cache_controller.py

Cache UI 的「動作序號控制器」。

背景：快取頁面有大量非同步操作（reload/save/rebuild index）。
若使用者連點或操作交錯，舊任務回來的結果可能覆蓋新任務狀態。

做法：
- 每次開始新動作時產生遞增 action_id。
- 回呼/背景任務更新 UI 前先比對 action_id，只允許目前動作寫入。

這是一種很輕量的取消/去抖策略，不涉及真正的 thread cancellation。
"""

from __future__ import annotations


class CacheController:
    """CacheController 類別。

    用途：封裝與 CacheController 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """
    def __init__(self):
        """__init__ 的用途說明。

        Args:
            參數請見函式簽名。
        Returns:
            回傳內容依實作而定；若無顯式回傳則為 None。
        Side Effects:
            可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
        """
        self._seq = 0
        self.current_action_id = 0

    def begin_action(self) -> int:
        """開始一個新動作並回傳 action_id。

        呼叫端通常會：
        - 在按鈕事件觸發時先 begin_action()
        - 把回傳 action_id 透傳進背景任務
        - 背景任務回來更新 UI 前先用 is_current() 檢查是否仍是最新動作
        """
        self._seq += 1
        self.current_action_id = self._seq
        return self.current_action_id

    def is_current(self, run_id: int | None) -> bool:
        """is_current 的用途說明。

        Args:
            參數請見函式簽名。
        Returns:
            回傳內容依實作而定；若無顯式回傳則為 None。
        Side Effects:
            可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
        """
        if run_id is None:
            return True
        return run_id == self.current_action_id
