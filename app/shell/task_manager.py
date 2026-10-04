"""app/shell/task_manager.py：全域任務狀態的單一來源。

頂列的「任務膠囊」、通知清單與狀態列都從這裡取資料。它掛在 ``TaskSession`` 的全域觀察者上，
因此每個頁面只要照原本方式使用 ``TaskSession``，就會自動出現在這裡；頁面不需要另外回報。

純邏輯、執行緒安全，不依賴 Flet（方便測試）。訂閱者會在觸發事件的執行緒被呼叫，
UI 端要自行節流 / 切回 UI 執行緒。
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.tasks import task_session as task_session_module

_logger = logging.getLogger(__name__)

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

DEFAULT_TASK_NAME = "背景任務"
RECENT_LIMIT = 20


@dataclass
class TaskInfo:
    """一個任務的快照。"""

    id: int
    name: str
    view_key: str | None
    status: str = STATUS_RUNNING
    progress: float = 0.0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def running(self) -> bool:
        return self.status == STATUS_RUNNING

    @property
    def percent(self) -> int:
        return round(max(0.0, min(1.0, self.progress)) * 100)

    def elapsed(self, now: float | None = None) -> float:
        end = self.finished_at if self.finished_at is not None else (now or time.time())
        return max(0.0, end - self.started_at)


class TaskManager:
    """追蹤進行中與最近完成的任務。"""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._active: dict[int, TaskInfo] = {}
        self._sessions: dict[int, weakref.ReferenceType] = {}
        self._recent: deque[TaskInfo] = deque(maxlen=RECENT_LIMIT)
        self._subscribers: list[Callable[[], None]] = []
        self._attached = False
        self._accepting = True

    # -- 接上 TaskSession ------------------------------------------------------

    def attach(self) -> None:
        """開始接收所有 TaskSession 的事件。"""
        if not self._attached:
            task_session_module.add_observer(self._on_session_event)
            self._attached = True
        self._accepting = True

    def detach(self) -> None:
        if self._attached:
            task_session_module.remove_observer(self._on_session_event)
            self._attached = False

    def _on_session_event(self, session, event: str) -> None:
        sid = id(session)
        with self._lock:
            info = self._active.get(sid)
            if event == "start":
                if not self._accepting:
                    return
                info = TaskInfo(
                    id=sid,
                    name=getattr(session, "name", None) or DEFAULT_TASK_NAME,
                    view_key=getattr(session, "view_key", None),
                    started_at=self._clock(),
                )
                self._active[sid] = info
                self._sessions[sid] = weakref.ref(session)
                weakref.finalize(session, self._drop, sid)
            elif info is None:
                return  # 沒有 start 過的 session（例如單元測試直接呼叫 finish）
            elif event == "progress":
                info.progress = float(getattr(session, "progress", info.progress))
            elif event == "error":
                # 還沒結案：服務通常在 finally 再呼叫 finish()
                info.status = STATUS_ERROR
            elif event == "finish":
                if info.status != STATUS_ERROR:
                    info.status = STATUS_DONE
                    info.progress = 1.0
                info.finished_at = self._clock()
                self._active.pop(sid, None)
                self._sessions.pop(sid, None)
                self._recent.appendleft(info)
        self._emit()

    def stop_accepting(self) -> None:
        """關閉流程進入 drain 階段後，拒絕新的 session 註冊。"""
        with self._lock:
            self._accepting = False

    def _drop(self, sid: int) -> None:
        """session 被回收卻沒有 finish：當作中斷，從進行中移除。"""
        with self._lock:
            info = self._active.pop(sid, None)
            if info is None:
                return
            info.status = STATUS_ERROR
            info.finished_at = self._clock()
            self._sessions.pop(sid, None)
            self._recent.appendleft(info)
        self._emit()

    def request_cancel_active(self) -> int:
        """要求所有已註冊中的 session 取消，回傳送出要求的數量。

        這只設定 worker 可觀察的 cancellation flag，不把 requested 誤當成
        worker 已停止；呼叫端仍必須等待 ``finish``／實際 writer completion。
        """
        with self._lock:
            sessions = [ref() for ref in self._sessions.values()]
        requested = 0
        for session in sessions:
            if session is None:
                continue
            request_cancel = getattr(session, "request_cancel", None)
            if not callable(request_cancel):
                continue
            try:
                request_cancel()
            except Exception:
                _logger.exception("要求任務取消失敗")
                continue
            requested += 1
        return requested

    # -- 讀取 ---------------------------------------------------------------

    def active(self) -> list[TaskInfo]:
        """進行中的任務（最早開始的在前）。"""
        with self._lock:
            tasks = [t for t in self._active.values() if t.status != STATUS_DONE]
        return sorted(tasks, key=lambda t: t.started_at)

    def current(self) -> TaskInfo | None:
        """頂列膠囊要顯示的任務：最近開始、仍在進行中的那個。"""
        tasks = self.active()
        return tasks[-1] if tasks else None

    def recent(self, limit: int = 8) -> list[TaskInfo]:
        """最近結束的任務（新的在前）。"""
        with self._lock:
            return list(self._recent)[:limit]

    # -- 訂閱 ---------------------------------------------------------------

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """訂閱任務變動；回傳取消訂閱的函式。"""
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def _emit(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback()
            except Exception:
                _logger.exception("TaskManager 訂閱者失敗")
