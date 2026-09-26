"""app/logging/task_session.py

新版 TaskSession，支援 LogEntry 結構化日誌。

這是 PR1 的核心產物。
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Deque

from .log_entry import LogEntry


class TaskSession:
    """
    單一長任務的 UI 狀態容器（Single Source of Truth）。

    新版設計：
    - logs 改為 deque[LogEntry]，提供 seq 追蹤與結構化資訊
    - add_log() 接受 level/source 參數，相容舊 caller（text-only）
    - snapshot() 回傳 list[LogEntry]，由 presenter 處理渲染
    """

    def __init__(self, max_logs: int = 2000):
        """
        初始化 TaskSession。

        Args:
            max_logs: deque 最大長度，超出時自動淘汰最舊的
        """
        self.progress: float = 0.0
        self.status: str = "IDLE"  # IDLE / RUNNING / DONE / ERROR
        self.error: bool = False

        self.logs: Deque[LogEntry] = deque(maxlen=max_logs)
        self._next_seq: int = 0
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

    # ---------- 狀態寫入（Worker 使用） ----------

    def set_progress(self, value: float) -> None:
        """更新 progress（0.0～1.0），自動 clamp。"""
        with self._lock:
            self.progress = max(0.0, min(1.0, value))

    def add_log(
        self,
        text: str,
        level: str = "info",
        source: str = "ui",
    ) -> None:
        """
        新增日誌事件。

        支援舊 caller（只傳 text）：level/source 皆使用 default。

        Args:
            text:   日誌文字
            level:  等級（debug/info/warning/error/system）
            source: 來源標記
        """
        if not text:
            return
        with self._lock:
            entry = LogEntry(
                seq=self._next_seq,
                level=level,
                text=text,
                source=source,
            )
            self._next_seq += 1
            self.logs.append(entry)

    def set_error(self) -> None:
        """設定錯誤狀態。"""
        with self._lock:
            self.error = True
            self.status = "ERROR"

    def set_summary(self, summary: dict) -> None:
        """設定任務摘要統計（供 DONE 時 UI 取用）。"""
        with self._lock:
            self.summary = summary

    def finish(self) -> None:
        """完成任務。

        已標記錯誤的任務維持 ERROR（service 常在 finally 呼叫 finish()，
        不可把失敗覆蓋成 DONE，否則 UI 會顯示「任務完成」）。
        """
        with self._lock:
            self.progress = 1.0
            self.status = "ERROR" if self.error else "DONE"

    def request_cancel(self) -> None:
        """要求取消任務；worker 會在下一個檢查點（例如批次之間）停止。"""
        self._cancel_event.set()

    @property
    def cancel_requested(self) -> bool:
        """是否已要求取消。"""
        return self._cancel_event.is_set()

    def start(self) -> None:
        """開始任務，清空日誌並重置序號。"""
        self._cancel_event.clear()
        with self._lock:
            self.progress = 0.0
            self.logs.clear()
            self._next_seq = 0
            self.error = False
            self.status = "RUNNING"

    # ---------- UI 讀取（UI 使用） ----------

    def snapshot(self) -> dict:
        """
        回傳 UI 用的不可變快照。

        回傳值：
            logs      — list[LogEntry]（新格式）
            log_texts — list[str]（backward compat：舊 caller 仍可正常運行）
            progress  — float
            status   — str
            error    — bool
        """
        with self._lock:
            return {
                "progress": self.progress,
                "logs": list(self.logs),
                "log_texts": [e.text for e in self.logs],
                "status": self.status,
                "error": self.error,
                "summary": getattr(self, "summary", None),
            }
