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
