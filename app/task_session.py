"""app/task_session.py

TaskSession 是 UI 與背景工作執行緒之間的「任務狀態容器」。

設計目標：
- 背景 worker 只負責寫入：progress / logs / status。
- UI 只透過 snapshot() 讀取不可變快照，避免讀寫競態。

維護注意：
- 這裡的鎖只保證 session 內部狀態一致，不涵蓋任何 IO 或外部資源。
- logs 使用 deque 限長，避免長任務把記憶體撐爆。
"""

import threading
from collections import deque


class TaskSession:
    """
    單一長任務的 UI 狀態容器（Single Source of Truth）。

    這個物件是 UI 執行緒與背景 worker 之間共享的最小狀態面：
    - worker 只負責寫入 progress / logs / status
    - UI 只透過 snapshot() 讀取快照後再決定如何渲染

    這樣做的目的不是把所有事情都塞進 session，
    而是把跨執行緒共享的狀態收斂到單一地方，降低 race condition 與散落旗標的維護成本。
    """
    def __init__(self, max_logs: int = 300):
        self.progress: float = 0.0
        self.status: str = "IDLE"   # IDLE / RUNNING / DONE / ERROR
        self.error: bool = False

        self.logs = deque(maxlen=max_logs)

        self._lock = threading.Lock()

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
        回傳 UI 用的不可變快照。

        UI 不直接拿著內部 deque / 欄位引用來讀，
        而是每次取一份快照，避免畫面更新時撞上背景執行緒正在寫入。
        """
        with self._lock:
            return {
                "progress": self.progress,
                "logs": list(self.logs),
                "status": self.status,
                "error": self.error,
            }
