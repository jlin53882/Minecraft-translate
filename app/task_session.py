import time
import threading
from collections import deque
from typing import Optional


class TaskSession:
    """
    單一長任務的 UI 狀態容器（Single Source of Truth）
    """
    def __init__(self, max_logs: int = 300):
        self.progress: float = 0.0
        self.status: str = "IDLE"   # IDLE / RUNNING / DONE / ERROR
        self.error: bool = False

        self.logs = deque(maxlen=max_logs)

        self._lock = threading.Lock()
        self._last_log_flush = 0.0

    # ---------- 狀態寫入（Worker 使用） ----------

    def set_progress(self, value: float):
        with self._lock:
            self.progress = max(0.0, min(1.0, value))

    def add_log(self, text: str):
        if not text:
            return
        with self._lock:
            self.logs.append(text)

    def set_error(self):
        with self._lock:
            self.error = True
            self.status = "ERROR"

    def finish(self):
        with self._lock:
            self.progress = 1.0
            self.status = "DONE"

    def start(self):
        with self._lock:
            self.progress = 0.0
            self.logs.clear()
            self.error = False
            self.status = "RUNNING"

    # ---------- UI 讀取（UI 使用） ----------

    def snapshot(self):
        """
        UI 用的快照（避免 race condition）
        """
        with self._lock:
            return {
                "progress": self.progress,
                "logs": list(self.logs),
                "status": self.status,
                "error": self.error,
            }
