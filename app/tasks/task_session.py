"""app/logging/task_session.py

新版 TaskSession，支援 LogEntry 結構化日誌。

這是 PR1 的核心產物。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable

from translation_tool.utils.redaction import redact_secrets
from translation_tool.utils.ui_mirror import (
    BACKEND_SOURCES,
    accepted_params,
    mirror_to_backend,
)

from .log_entry import LogEntry

_logger = logging.getLogger(__name__)

# 全域觀察者：外殼的 TaskManager 用它得知「有任務開始 / 進度 / 結束」，
# 不必每個頁面各自回報。callback(session, event)，event 為 start / progress / error / finish。
_observers: list[Callable[[TaskSession, str], None]] = []
_observers_lock = threading.Lock()


def add_observer(callback: Callable[[TaskSession, str], None]) -> None:
    """註冊全域任務事件觀察者（重複註冊同一個 callback 只會留一份）。"""
    with _observers_lock:
        if callback not in _observers:
            _observers.append(callback)


def remove_observer(callback: Callable[[TaskSession, str], None]) -> None:
    with _observers_lock:
        if callback in _observers:
            _observers.remove(callback)


def tag_session(session, name: str, view_key: str | None = None):
    """替 session 標上顯示名稱與所屬頁面（頂列任務膠囊用），並回傳 session。

    用屬性設定而不是建構參數，所以替身 / 舊版 session 也能安全呼叫。
    """
    try:
        session.name = name
        session.view_key = view_key
    except AttributeError:
        pass
    return session


def add_log_unmirrored(session, text: str, level: str = "info") -> None:
    """寫進任務畫面日誌但不鏡像到後台（呼叫端已把更完整的內容寫進後台時使用）。

    舊版 / 測試替身 session 的 ``add_log`` 可能沒有 ``level`` 或 ``mirror`` 參數，
    以函式簽章判斷要傳哪些（不靠捕捉 ``TypeError``：那會把 ``add_log`` 內部真正的
    ``TypeError`` 也當成「不支援」而重複呼叫）。
    """
    accepted = accepted_params(session.add_log)
    kwargs: dict[str, object] = {}
    if accepted is None or "level" in accepted:
        kwargs["level"] = level
    if accepted is None or "mirror" in accepted:
        kwargs["mirror"] = False
    session.add_log(text, **kwargs)


def _notify(session: TaskSession, event: str) -> None:
    with _observers_lock:
        observers = list(_observers)
    for callback in observers:
        try:
            callback(session, event)
        except Exception:
            _logger.exception("TaskSession 觀察者失敗：%s", event)


class TaskSession:
    """
    單一長任務的 UI 狀態容器（Single Source of Truth）。

    新版設計：
    - logs 改為 deque[LogEntry]，提供 seq 追蹤與結構化資訊
    - add_log() 接受 level/source 參數，相容舊 caller（text-only）
    - snapshot() 回傳 list[LogEntry]，由 presenter 處理渲染
    """

    def __init__(
        self,
        max_logs: int = 2000,
        *,
        name: str | None = None,
        view_key: str | None = None,
    ):
        """
        初始化 TaskSession。

        Args:
            max_logs: deque 最大長度，超出時自動淘汰最舊的
            name: 任務顯示名稱（頂列的任務膠囊用；未提供時顯示「背景任務」）
            view_key: 任務所屬頁面的 key（點膠囊可跳回該頁）
        """
        self.name = name
        self.view_key = view_key
        # 任務識別：UI→後台鏡像去重用，區分同時執行的不同任務
        self.task_id = uuid.uuid4().hex[:8]
        self.progress: float = 0.0
        self.status: str = "IDLE"  # IDLE / RUNNING / DONE / ERROR
        self.error: bool = False

        self.logs: deque[LogEntry] = deque(maxlen=max_logs)
        self._next_seq: int = 0
        self._start_logs: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._started_at: float | None = None
        self._finished = False  # finish() 的終止通知只送一次（見 finish）

    # ---------- 後台生命週期紀錄 ----------

    def _prefix(self) -> str:
        return f"[{self.name}] " if self.name else ""

    def _log_lifecycle(self, text: str, level: str = "info") -> None:
        """任務開始 / 結束寫進後台 log（不進畫面），讓 log 檔能看出每個任務的邊界、結果與耗時。"""
        mirror_to_backend(text, level, prefix=self._prefix(), dedupe=False)

    # ---------- 狀態寫入（Worker 使用） ----------

    def set_progress(self, value: float) -> None:
        """更新 progress（0.0～1.0），自動 clamp。"""
        with self._lock:
            self.progress = max(0.0, min(1.0, value))
        _notify(self, "progress")

    def add_log(
        self,
        text: str,
        level: str = "info",
        source: str = "ui",
        *,
        mirror: bool = True,
    ) -> None:
        """
        新增日誌事件。

        支援舊 caller（只傳 text）：level/source 皆使用 default。

        UI 與後台同步：寫進畫面的訊息會自動鏡像到後台 log（帶任務名稱前綴），
        所以呼叫端不必再自己配對 ``log_info``。以下情況不會鏡像：

        - ``source`` 是 ``logger`` / ``backend``：訊息本來就來自後台。
        - ``mirror=False``：呼叫端已自行寫入後台（例如 ``mirror_session_log``）。
        - 後台最近已記錄過相同內容（核心流程自己 log 後又 yield 給 UI 的那一份）。

        Args:
            text:   日誌文字
            level:  等級（debug/info/warning/error/system）
            source: 來源標記
            mirror: 是否鏡像到後台 log
        """
        if not text:
            return
        # UI 日誌的總出口：不論來自 logger 或直接呼叫，機密都不會顯示在畫面上（#125）
        text = redact_secrets(text)
        with self._lock:
            entry = LogEntry(
                seq=self._next_seq,
                level=level,
                text=text,
                source=source,
            )
            self._next_seq += 1
            self.logs.append(entry)
        if mirror and source not in BACKEND_SOURCES:
            mirror_to_backend(
                text,
                level,
                prefix=f"[{self.name}] " if self.name else "",
                task=self.task_id,
            )

    def set_error(self) -> None:
        """設定錯誤狀態。"""
        with self._lock:
            self.error = True
            self.status = "ERROR"
        _notify(self, "error")

    def add_start_log(self, text: str, level: str = "info") -> None:
        """新增「開始前就知道、要顯示在任務日誌開頭」的訊息。

        會立刻寫入日誌，而且 ``start()`` 清空日誌後會重新放回——所以由 View 在 service
        ``start()`` 之前寫入的提示（例如預設輸出路徑）也屬於 session snapshot，
        輪詢／tail 重整／卸載後重新掛載都不會讓它消失。
        """
        with self._lock:
            self._start_logs.append((text, level))
        self.add_log(text, level)

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
            already_finished = self._finished
            self._finished = True
            status = self.status
            summary = getattr(self, "summary", None)
            log_count = len(self.logs)
            started = self._started_at
        if already_finished:
            # 流水線的安全網（例外／取消路徑）會在步驟自己 finish 之後再呼叫一次；
            # 狀態仍照上面更新（set_error 之後的第二次 finish 要維持 ERROR），
            # 但後台的「任務結束」紀錄與觀察者通知只送一次。
            return
        elapsed = f"，耗時 {time.monotonic() - started:.1f}s" if started else ""
        summary_text = str(summary) if summary else ""
        if len(summary_text) > 500:
            summary_text = summary_text[:500] + "…（已截斷）"
        extra = f"，摘要：{summary_text}" if summary_text else ""
        self._log_lifecycle(
            f"任務結束：{status}{elapsed}，畫面日誌 {log_count} 筆{extra}",
            "error" if status == "ERROR" else "info",
        )
        _notify(self, "finish")

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
            self._started_at = time.monotonic()
            self._finished = False
            start_logs = list(self._start_logs)
        for (
            text,
            level,
        ) in start_logs:  # 清空日誌後把開頭訊息放回（後台已記錄過，不再鏡像）
            self.add_log(text, level, mirror=False)
        self._log_lifecycle("任務開始")
        _notify(self, "start")

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
