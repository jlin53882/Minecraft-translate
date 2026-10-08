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
from app.tasks.operation_registry import (
    OperationHandle,
    OperationRegistry,
    current_operation,
)

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

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        *,
        operation_registry: OperationRegistry | None = None,
    ) -> None:
        self._clock = clock
        self.operation_registry = operation_registry or OperationRegistry()
        self._lock = threading.Lock()
        self._active: dict[int, TaskInfo] = {}
        self._sessions: dict[int, weakref.ReferenceType] = {}
        self._recent: deque[TaskInfo] = deque(maxlen=RECENT_LIMIT)
        self._subscribers: list[Callable[[], None]] = []
        self._attached = False
        self._detach_pending = False
        self._unsubscribe_registry: Callable[[], None] | None = None

    # -- 接上 TaskSession ------------------------------------------------------

    def attach(self) -> None:
        """開始接收所有 TaskSession 的事件。"""
        if not self._attached:
            task_session_module.add_observer(self._on_session_event)
            self._unsubscribe_registry = self.operation_registry.subscribe(
                self._on_registry_event
            )
            self._attached = True

    def detach(self) -> None:
        if not self._attached:
            return
        if self.operation_registry.active_count():
            # A Web session may expire after the bounded drain deadline. Keep the
            # admission observer alive until workers actually finish, but detach
            # all UI projections now.
            self._detach_pending = True
            with self._lock:
                self._subscribers.clear()
            return
        self._finish_detach()

    def _finish_detach(self) -> None:
        task_session_module.remove_observer(self._on_session_event)
        if self._unsubscribe_registry is not None:
            self._unsubscribe_registry()
            self._unsubscribe_registry = None
        self._attached = False
        self._detach_pending = False

    def _on_session_event(self, session, event: str) -> bool | None:
        if event == "admission":
            parent = current_operation()
            handle = getattr(session, "operation_handle", None)
            if parent is not None:
                parent.bind_task_session(session)
                return True
            if isinstance(handle, OperationHandle) and not handle.done_event.is_set():
                return True
            if handle is not None:
                try:
                    session.operation_handle = None
                except AttributeError:
                    _logger.debug("TaskSession 不支援清除失效 owner handle")
            return self.operation_registry.register_session(session) is not None

        if self._detach_pending:
            if event == "finish":
                handle = getattr(session, "operation_handle", None)
                if handle is not None and not handle._worker_managed:
                    self.operation_registry.finish_session(session)
            return

        sid = id(session)
        legacy_handle = None
        with self._lock:
            info = self._active.get(sid)
            if event == "start":
                handle = getattr(session, "operation_handle", None)
                if handle is None:
                    parent = current_operation()
                    if parent is not None:
                        parent.bind_task_session(session)
                        handle = parent
                    else:
                        handle = self.operation_registry.register_session(session)
                if handle is None:
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
                if event == "error":
                    # 結束（finish）之後才補標失敗（例如流水線安全網）：更正「最近完成」裡的結果，
                    # 不然 TaskSession 是 ERROR、頂列／通知卻顯示 DONE。
                    for done in self._recent:
                        if done.id == sid and done.status != STATUS_ERROR:
                            done.status = STATUS_ERROR
                            break
                    else:
                        return
                else:
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
                handle = getattr(session, "operation_handle", None)
                if handle is not None and not handle._worker_managed:
                    legacy_handle = handle
        if legacy_handle is not None:
            # Registry observers can call back into TaskManager; never run them under this lock.
            self.operation_registry.finish_session(session)
        self._emit()

    def _on_registry_event(self, event: str, handle: OperationHandle) -> None:
        if self._detach_pending:
            if self.operation_registry.active_count() == 0:
                self._finish_detach()
            return
        if event == "start" and handle.task_session is not None:
            # TaskSession.start emits the user-visible transition after its state
            # is initialized; avoid presenting one operation twice at admission.
            return
        if event == "finish" and handle._task_session_id is None:
            failed = handle.error is not None
            info = TaskInfo(
                id=int(handle.id, 16),
                name=handle.descriptor.name or DEFAULT_TASK_NAME,
                view_key=handle.descriptor.view_key,
                status=STATUS_ERROR if failed else STATUS_DONE,
                progress=0.0 if failed else 1.0,
                started_at=handle.created_at,
                finished_at=self._clock(),
            )
            with self._lock:
                self._recent.appendleft(info)
        self._emit()

    def stop_accepting(self) -> None:
        """相容舊呼叫端：關閉 authoritative registry 的 admission。"""
        self.operation_registry.begin_shutdown()

    def resume_accepting(self) -> None:
        """明確恢復 admission；active operation 尚未收斂時會拒絕。"""
        self.operation_registry.reopen_admission()

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
        self.operation_registry.finish_abandoned_session(sid)
        self._emit()

    def request_cancel_active(self) -> int:
        """要求所有已註冊中的 session 取消，回傳送出要求的數量。

        這只設定 worker 可觀察的 cancellation flag，不把 requested 誤當成
        worker 已停止；呼叫端仍必須等待 ``finish``／實際 writer completion。
        """
        return sum(
            handle.request_cancel() for handle in self.operation_registry.active()
        )

    # -- 讀取 ---------------------------------------------------------------

    def active(self) -> list[TaskInfo]:
        """TaskSession 的 UI projection；active membership 由 OperationRegistry 擁有。"""
        projected: list[TaskInfo] = []
        for handle in self.operation_registry.active():
            session = handle.task_session
            session_is_active = session is not None and not getattr(
                session, "is_finished", False
            )
            session_info = None
            if session_is_active:
                with self._lock:
                    session_info = self._active.get(id(session))
            if session_info is not None:
                projected.append(session_info)
                continue
            projected.append(
                TaskInfo(
                    id=int(handle.id, 16),
                    name=(
                        (getattr(session, "name", None) if session_is_active else None)
                        or handle.descriptor.name
                        or DEFAULT_TASK_NAME
                    ),
                    view_key=(
                        (
                            getattr(session, "view_key", None)
                            if session_is_active
                            else None
                        )
                        or handle.descriptor.view_key
                    ),
                    started_at=handle.created_at,
                )
            )
        return sorted(projected, key=lambda task: task.started_at)

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
