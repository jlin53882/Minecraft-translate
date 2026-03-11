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
    def __init__(self):
        self._seq = 0
        self.current_action_id = 0

    def begin_action(self) -> int:
        self._seq += 1
        self.current_action_id = self._seq
        return self.current_action_id

    def is_current(self, run_id: int | None) -> bool:
        if run_id is None:
            return True
        return run_id == self.current_action_id
