"""Authoritative ownership and admission for background operations.

``TaskManager`` is a presentation projection for ``TaskSession``. This registry
owns operation membership, admission, cancellation requests, and terminal state.
An operation remains active until its worker actually returns; requesting cancel
never removes it from the registry.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
import weakref
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_logger = logging.getLogger(__name__)
_current_operation: ContextVar[OperationHandle | None] = ContextVar(
    "current_operation", default=None
)


class CancellationPolicy(StrEnum):
    COOPERATIVE = "cooperative"
    BOUNDARY_ONLY = "boundary_only"
    NON_CANCELLABLE = "non_cancellable"


class CommitPolicy(StrEnum):
    EPHEMERAL = "ephemeral"
    PARTIAL_ALLOWED = "partial_commit_allowed"
    ATOMIC = "atomic_commit"


class DurabilityPolicy(StrEnum):
    RECOMPUTABLE = "recomputable"
    USER_ACTION = "durable_user_action"


class ShutdownPolicy(StrEnum):
    CANCEL_AND_DRAIN = "cancel_and_drain"
    DRAIN_ONLY = "drain_only"
    ALLOW_TO_FINISH = "allow_to_finish"
    TRANSFER_OWNERSHIP = "transfer_ownership"


@dataclass(frozen=True)
class OperationDescriptor:
    """Reviewable behavior contract for an owned operation."""

    name: str
    owner: str
    parent_id: str | None = None
    view_key: str | None = None
    cancellation: CancellationPolicy = CancellationPolicy.COOPERATIVE
    commit: CommitPolicy = CommitPolicy.EPHEMERAL
    durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE
    shutdown: ShutdownPolicy = ShutdownPolicy.CANCEL_AND_DRAIN
    metadata: dict[str, Any] = field(default_factory=dict)


class OperationHandle:
    """Stable handle for one admitted unit of background work."""

    def __init__(self, registry: OperationRegistry, descriptor: OperationDescriptor):
        self._registry = registry
        self.descriptor = descriptor
        self.id = uuid.uuid4().hex
        self.created_at = time.time()
        self.cancel_event = threading.Event()
        self.done_event = threading.Event()
        self._state = "reserved"
        self._terminal_reason: str | None = None
        self._error: BaseException | None = None
        self._task_session_ref = None
        self._task_session_id: int | None = None
        self._worker_managed = False
        self._launched = False
        self._cancel_callbacks: list[Callable[[], Any]] = []
        self._run_if_cancelled = False

    @property
    def state(self) -> str:
        with self._registry._condition:
            return self._state

    @property
    def cancel_requested(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def terminal_reason(self) -> str | None:
        with self._registry._condition:
            return self._terminal_reason

    @property
    def error(self) -> BaseException | None:
        with self._registry._condition:
            return self._error

    @property
    def task_session(self):
        return self._task_session_ref() if self._task_session_ref else None

    def bind_task_session(self, session) -> None:
        """Link TaskSession presentation/cancellation to this owner."""
        self._task_session_ref = weakref.ref(session)
        self._task_session_id = id(session)
        try:
            session.operation_handle = self
            session.operation_registry = self._registry
        except AttributeError:
            _logger.debug("TaskSession 不支援 owner handle 欄位")
        bind = getattr(session, "bind_operation_cancel_event", None)
        if callable(bind):
            bind(self.cancel_event)
        if self.cancel_requested:
            request_cancel = getattr(session, "request_cancel", None)
            if callable(request_cancel):
                request_cancel()

    def request_cancel(self) -> bool:
        return self._registry._request_cancel(self)

    def finish(self, *, reason: str = "completed", error: BaseException | None = None):
        """Finish an event-loop-owned operation after its awaited work returns."""
        self._registry._finish(self, reason=reason, error=error)

    def record_error(self, error: BaseException) -> None:
        """Remember a handled failure without ending ownership before cleanup returns."""
        self._registry._record_error(self, error)

    def add_cancel_callback(self, callback: Callable[[], Any]) -> None:
        with self._registry._condition:
            if callback in self._cancel_callbacks:
                return
            self._cancel_callbacks.append(callback)
            already_requested = self.cancel_requested
        if already_requested:
            callback()

    def launch(
        self,
        target: Callable[[], Any],
        *,
        launcher: Callable[[Callable[[], None]], Any] | None = None,
        run_if_cancelled: bool = False,
    ) -> bool:
        return self._registry.launch_reserved(
            self,
            target,
            launcher=launcher,
            run_if_cancelled=run_if_cancelled,
        )

    def run(self, target: Callable[[], Any]) -> None:
        """Run an admitted operation, recording actual completion exactly once."""
        from translation_tool.utils.cancellation import TaskCancelled, cancel_scope

        token: Token = _current_operation.set(self)
        self._registry._mark_started(self)
        error = None
        cancelled_by_exception = False
        try:
            if self._run_if_cancelled or not self.cancel_requested:
                if self.descriptor.cancellation == CancellationPolicy.NON_CANCELLABLE:
                    target()
                else:
                    # Install the owner token at the worker boundary. ContextThreadPoolExecutor
                    # and run_in_context propagate this ContextVar into nested workers.
                    with cancel_scope(lambda: self.cancel_requested):
                        target()
        except TaskCancelled as ex:
            if self.descriptor.cancellation == CancellationPolicy.NON_CANCELLABLE:
                error = ex
                _logger.exception(
                    "不可取消操作意外收到取消訊號：%s", self.descriptor.name
                )
            else:
                cancelled_by_exception = True
        except BaseException as ex:  # worker boundary: registry must always terminate
            error = ex
            _logger.exception("背景操作失敗：%s", self.descriptor.name)
        finally:
            session = self._task_session_ref() if self._task_session_ref else None
            if (
                error is None
                and session is not None
                and getattr(session, "error", False)
            ):
                error = RuntimeError(
                    f"TaskSession reported failure: {self.descriptor.name}"
                )
            try:
                if session is not None and error is not None:
                    set_error = getattr(session, "set_error", None)
                    if callable(set_error):
                        set_error()
                if session is not None and not getattr(session, "is_finished", False):
                    finish = getattr(session, "finish", None)
                    if callable(finish):
                        finish()
            except BaseException as ex:
                if error is None:
                    error = ex
                _logger.exception(
                    "TaskSession terminal callback 失敗：%s", self.descriptor.name
                )
            finally:
                try:
                    self._registry._finish(
                        self,
                        error=error,
                        reason=(
                            "cancelled"
                            if cancelled_by_exception or self.cancel_requested
                            else "completed"
                        ),
                    )
                finally:
                    _current_operation.reset(token)


class OperationRegistry:
    """Single authoritative active-operation registry with atomic admission."""

    OPEN = "open"
    DRAINING = "draining"
    DRAIN_TIMEOUT = "drain_timeout"
    CLOSED = "closed"

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._active: dict[str, OperationHandle] = {}
        self._subscribers: list[Callable[[str, OperationHandle], Any]] = []
        self._accepting = True
        self._shutdown_state = self.OPEN

    @property
    def accepting(self) -> bool:
        with self._condition:
            return self._accepting

    @property
    def shutdown_state(self) -> str:
        with self._condition:
            return self._shutdown_state

    def reserve(
        self, descriptor: OperationDescriptor, *, task_session=None
    ) -> OperationHandle | None:
        """Atomically reserve ownership before the caller schedules work."""
        with self._condition:
            if not self._accepting:
                return None
            handle = OperationHandle(self, descriptor)
            if task_session is not None:
                handle.bind_task_session(task_session)
            self._active[handle.id] = handle
            self._condition.notify_all()
        self._emit("start", handle)
        return handle

    def subscribe(
        self, callback: Callable[[str, OperationHandle], Any]
    ) -> Callable[[], None]:
        with self._condition:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._condition:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def launch(
        self,
        target: Callable[[], Any],
        descriptor: OperationDescriptor,
        *,
        launcher: Callable[[Callable[[], None]], Any] | None = None,
        task_session=None,
        on_cancel: Callable[[], Any] | None = None,
        run_if_cancelled: bool = False,
    ) -> OperationHandle | None:
        """Reserve first, then invoke a thread/event-loop launcher.

        A shutdown racing after reservation sees and cancels this operation;
        ``run`` checks that request before entering user code. A rejected
        reservation never invokes the launcher.
        """
        handle = self.reserve(descriptor, task_session=task_session)
        if handle is None:
            return None
        if on_cancel is not None:
            handle.add_cancel_callback(on_cancel)

        self.launch_reserved(
            handle,
            target,
            launcher=launcher,
            run_if_cancelled=run_if_cancelled,
        )
        return handle

    def launch_reserved(
        self,
        handle: OperationHandle,
        target: Callable[[], Any],
        *,
        launcher: Callable[[Callable[[], None]], Any] | None = None,
        run_if_cancelled: bool = False,
    ) -> bool:
        """Launch a previously admitted handle (including one canceled while reserved)."""
        with self._condition:
            if handle._registry is not self or handle.done_event.is_set():
                return False
            if handle._launched:
                raise RuntimeError("operation handle has already been launched")
            handle._launched = True
            handle._worker_managed = True
            handle._run_if_cancelled = run_if_cancelled

        def run() -> None:
            handle.run(target)

        try:
            if launcher is None:
                thread = threading.Thread(
                    target=run,
                    name=f"operation-{handle.descriptor.owner}-{handle.descriptor.name}"[
                        :80
                    ],
                    daemon=True,
                )
                thread.start()
            else:
                launcher(run)
        except BaseException as ex:
            session = handle._task_session_ref() if handle._task_session_ref else None
            try:
                if session is not None and not getattr(session, "is_finished", False):
                    set_error = getattr(session, "set_error", None)
                    if callable(set_error):
                        set_error()
                    finish = getattr(session, "finish", None)
                    if callable(finish):
                        finish()
            except BaseException:
                _logger.exception(
                    "啟動失敗時結束 TaskSession 失敗：%s", handle.descriptor.name
                )
            finally:
                self._finish(handle, error=ex)
            raise
        return True

    def register_session(self, session) -> OperationHandle | None:
        """Compatibility owner for synchronous/legacy TaskSession-only work."""
        existing = getattr(session, "operation_handle", None)
        if isinstance(existing, OperationHandle):
            return existing
        descriptor = OperationDescriptor(
            name=getattr(session, "name", None) or "背景任務",
            owner=getattr(session, "view_key", None) or "legacy-task-session",
            cancellation=CancellationPolicy.COOPERATIVE,
            commit=CommitPolicy.PARTIAL_ALLOWED,
            shutdown=ShutdownPolicy.CANCEL_AND_DRAIN,
        )
        handle = self.reserve(descriptor, task_session=session)
        return handle

    def finish_session(self, session) -> None:
        """End only legacy session-owned operations; workers finish their own handle."""
        handle = getattr(session, "operation_handle", None)
        if not isinstance(handle, OperationHandle) or handle._worker_managed:
            return
        error = None
        if getattr(session, "error", False):
            error = RuntimeError(
                f"TaskSession reported failure: {handle.descriptor.name}"
            )
        self._finish(
            handle,
            reason="cancelled" if handle.cancel_requested else "session_finished",
            error=error,
        )

    def record_session_error(self, session) -> None:
        """Record or amend a TaskSession failure without reopening its operation."""
        handle = getattr(session, "operation_handle", None)
        if (
            not isinstance(handle, OperationHandle)
            or handle.task_session is not session
        ):
            return
        error = RuntimeError(f"TaskSession reported failure: {handle.descriptor.name}")
        with self._condition:
            if handle.done_event.is_set():
                if handle._error is not None:
                    return
                handle._error = error
                handle._terminal_reason = "failed"
                handle._state = "failed"
                self._condition.notify_all()
                amended = True
            else:
                amended = False
        if amended:
            self._emit("result", handle)
        else:
            self._record_error(handle, error)

    def finish_abandoned_session(self, session_id: int) -> None:
        with self._condition:
            handles = [
                handle
                for handle in self._active.values()
                if handle._task_session_id == session_id and not handle._worker_managed
            ]
        for handle in handles:
            self._finish(handle, error=RuntimeError("TaskSession 被回收前未完成"))

    def active(self) -> list[OperationHandle]:
        with self._condition:
            return list(self._active.values())

    def active_count(self) -> int:
        with self._condition:
            return len(self._active)

    def begin_shutdown(self) -> list[OperationHandle]:
        """Close admission atomically and apply each operation's shutdown policy."""
        with self._condition:
            self._accepting = False
            if self._shutdown_state != self.CLOSED:
                self._shutdown_state = self.DRAINING
            handles = list(self._active.values())
        for handle in handles:
            if handle.descriptor.shutdown == ShutdownPolicy.CANCEL_AND_DRAIN:
                handle.request_cancel()
        return handles

    def mark_drain_timeout(self) -> None:
        with self._condition:
            if self._active:
                self._shutdown_state = self.DRAIN_TIMEOUT
            self._accepting = False
            self._condition.notify_all()

    def mark_closed(self) -> None:
        with self._condition:
            if self._active:
                raise RuntimeError("cannot close registry while operations are active")
            self._accepting = False
            self._shutdown_state = self.CLOSED
            self._condition.notify_all()

    def reopen_admission(self) -> None:
        """Explicitly resume after a user-visible shutdown cancellation decision."""
        with self._condition:
            if self._active:
                raise RuntimeError(
                    "cannot reopen admission while operations are active"
                )
            self._accepting = True
            self._shutdown_state = self.OPEN
            self._condition.notify_all()

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        """Wait for actual worker completion; cancellation requests are not enough."""
        with self._condition:
            return self._condition.wait_for(lambda: not self._active, timeout=timeout)

    def _mark_started(self, handle: OperationHandle) -> None:
        with self._condition:
            if handle.id in self._active and handle._state == "reserved":
                handle._state = (
                    "cancel_requested" if handle.cancel_requested else "running"
                )
            self._condition.notify_all()

    def _request_cancel(self, handle: OperationHandle) -> bool:
        with self._condition:
            if handle.id not in self._active or handle.cancel_event.is_set():
                return False
            handle.cancel_event.set()
            handle._state = "cancel_requested"
            session = handle._task_session_ref() if handle._task_session_ref else None
            callbacks = list(handle._cancel_callbacks)
            self._condition.notify_all()
        request_cancel = getattr(session, "request_cancel", None)
        if callable(request_cancel):
            try:
                request_cancel()
            except Exception:
                _logger.exception("傳遞操作取消要求失敗：%s", handle.descriptor.name)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                _logger.exception("操作取消 callback 失敗：%s", handle.descriptor.name)
        self._emit("cancel", handle)
        return True

    def _record_error(self, handle: OperationHandle, error: BaseException) -> None:
        with self._condition:
            if handle.id in self._active and not handle.done_event.is_set():
                handle._error = error
                self._condition.notify_all()

    def _finish(
        self,
        handle: OperationHandle,
        *,
        reason: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        with self._condition:
            if handle.done_event.is_set():
                return
            terminal_error = error if error is not None else handle._error
            handle._error = terminal_error
            handle._terminal_reason = (
                "failed" if terminal_error is not None else (reason or "completed")
            )
            handle._state = "failed" if terminal_error is not None else "terminal"
            self._active.pop(handle.id, None)
            handle.done_event.set()
            # Preserve an observable timeout state after the final worker
            # returns; the UI may now offer an explicit recovery choice.
            if (
                not self._active
                and not self._accepting
                and self._shutdown_state != self.DRAIN_TIMEOUT
            ):
                self._shutdown_state = self.DRAINING
            self._condition.notify_all()
        self._emit("finish", handle)

    def _emit(self, event: str, handle: OperationHandle) -> None:
        with self._condition:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event, handle)
            except Exception:
                _logger.exception("OperationRegistry subscriber failed: %s", event)


def current_operation() -> OperationHandle | None:
    """Return the operation executing on this thread, if any."""
    return _current_operation.get()


def get_page_operation_registry(page) -> OperationRegistry | None:
    """Return the AppShell registry, or ``None`` for a standalone page."""
    registry = getattr(page, "operation_registry", None)
    return registry if isinstance(registry, OperationRegistry) else None


def launch_task_thread(
    registry: OperationRegistry,
    target: Callable[[], Any],
    *,
    name: str,
    owner: str,
    task_session=None,
    parent_id: str | None = None,
    view_key: str | None = None,
    cancellation: CancellationPolicy = CancellationPolicy.COOPERATIVE,
    commit: CommitPolicy = CommitPolicy.PARTIAL_ALLOWED,
    durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE,
    shutdown: ShutdownPolicy = ShutdownPolicy.CANCEL_AND_DRAIN,
    launcher: Callable[[Callable[[], None]], Any] | None = None,
    on_cancel: Callable[[], Any] | None = None,
    run_if_cancelled: bool = False,
) -> OperationHandle | None:
    """Convenience boundary for UI tasks that expose a ``TaskSession``."""
    return registry.launch(
        target,
        OperationDescriptor(
            name=name,
            owner=owner,
            parent_id=parent_id,
            view_key=view_key,
            cancellation=cancellation,
            commit=commit,
            durability=durability,
            shutdown=shutdown,
        ),
        launcher=launcher,
        task_session=task_session,
        on_cancel=on_cancel,
        run_if_cancelled=run_if_cancelled,
    )


def launch_page_operation(
    page,
    target: Callable[[], Any],
    *,
    name: str,
    owner: str,
    task_session=None,
    view_key: str | None = None,
    cancellation: CancellationPolicy = CancellationPolicy.COOPERATIVE,
    commit: CommitPolicy = CommitPolicy.PARTIAL_ALLOWED,
    durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE,
    shutdown: ShutdownPolicy = ShutdownPolicy.CANCEL_AND_DRAIN,
    on_cancel: Callable[[], Any] | None = None,
    fallback_launcher: Callable[[Callable[[], Any]], Any] | None = None,
) -> OperationHandle | bool | None:
    """Launch a page-owned thread; legacy test pages retain their old behavior.

    ``False`` means the page is attached to a closing app and admission rejected
    the operation. ``True`` is the isolated-page fallback used by small unit
    tests that do not compose an ``AppShell``.
    """
    registry = getattr(page, "operation_registry", None)
    if not isinstance(registry, OperationRegistry):
        _launch_fallback(page, target, fallback_launcher)
        return True
    handle = launch_task_thread(
        registry,
        target,
        name=name,
        owner=owner,
        task_session=task_session,
        view_key=view_key,
        cancellation=cancellation,
        commit=commit,
        durability=durability,
        shutdown=shutdown,
        on_cancel=on_cancel,
    )
    return handle if handle is not None else False


@dataclass
class PageOperationReservation:
    """Admission token that separates registration from worker launch."""

    registry: OperationRegistry | None
    handle: OperationHandle | None
    admitted: bool
    on_cancel: Callable[[], Any] | None = None
    page: Any = None
    fallback_launcher: Callable[[Callable[[], Any]], Any] | None = None
    _fallback_done: threading.Event = field(default_factory=threading.Event)

    @property
    def done_event(self) -> threading.Event:
        return (
            self.handle.done_event if self.handle is not None else self._fallback_done
        )

    def finish(self, *, reason: str = "completed", error: BaseException | None = None):
        if self.handle is not None:
            self.handle.finish(reason=reason, error=error)
        else:
            self._fallback_done.set()

    def launch(self, target: Callable[[], Any]) -> bool:
        if not self.admitted:
            return False

        def run_fallback() -> None:
            try:
                target()
            finally:
                self._fallback_done.set()

        if self.registry is None:
            _launch_fallback(self.page, run_fallback, self.fallback_launcher)
            return True
        assert self.handle is not None
        if self.on_cancel is not None:
            self.handle.add_cancel_callback(self.on_cancel)
        return self.handle.launch(target)


def reserve_page_operation(
    page,
    *,
    name: str,
    owner: str,
    task_session=None,
    view_key: str | None = None,
    cancellation: CancellationPolicy = CancellationPolicy.COOPERATIVE,
    commit: CommitPolicy = CommitPolicy.PARTIAL_ALLOWED,
    durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE,
    shutdown: ShutdownPolicy = ShutdownPolicy.CANCEL_AND_DRAIN,
    on_cancel: Callable[[], Any] | None = None,
    fallback_launcher: Callable[[Callable[[], Any]], Any] | None = None,
) -> PageOperationReservation:
    """Reserve page work before session start or any worker launch side effect."""
    registry = getattr(page, "operation_registry", None)
    if not isinstance(registry, OperationRegistry):
        return PageOperationReservation(
            None, None, True, on_cancel, page, fallback_launcher
        )
    handle = registry.reserve(
        OperationDescriptor(
            name=name,
            owner=owner,
            view_key=view_key,
            cancellation=cancellation,
            commit=commit,
            durability=durability,
            shutdown=shutdown,
        ),
        task_session=task_session,
    )
    return PageOperationReservation(
        registry, handle, handle is not None, on_cancel, page, fallback_launcher
    )


def _launch_fallback(
    page,
    target: Callable[[], Any],
    launcher: Callable[[Callable[[], Any]], Any] | None = None,
) -> None:
    """Preserve standalone-view scheduling without creating a second app registry."""
    if launcher is not None:
        launcher(target)
        return
    run_thread = getattr(page, "run_thread", None)
    if callable(run_thread):
        run_thread(target)
        return
    threading.Thread(target=target, daemon=True).start()
