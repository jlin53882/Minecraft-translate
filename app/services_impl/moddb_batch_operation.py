"""Run one Mod DB batch-preview or atomic-write operation off the UI thread."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.tasks.task_session import TaskSession, tag_session
from translation_tool.translation_db.models import BatchReplacePlan, BatchReplaceResult
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope


@dataclass(frozen=True)
class BatchOperationOutcome:
    kind: str
    generation: int
    state: str
    result: object | None = None
    error: Exception | None = None


@dataclass(repr=False)
class BatchOperationResult:
    """One-shot, owner-bound channel for the batch dialog's detailed result."""

    kind: str
    generation: int
    _session: TaskSession | None = field(default=None, init=False, repr=False)
    _outcome: BatchOperationOutcome | None = field(default=None, init=False, repr=False)
    _discarded: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def bind(self, session: TaskSession) -> None:
        with self._lock:
            if self._session is not None and self._session is not session:
                raise RuntimeError("batch result channel already belongs to a session")
            if not self._discarded:
                self._session = session

    def publish(
        self,
        session: TaskSession,
        *,
        state: str,
        result: object | None = None,
        error: Exception | None = None,
    ) -> bool:
        with self._lock:
            if self._discarded or self._session is not session:
                return False
            self._outcome = BatchOperationOutcome(
                self.kind, self.generation, state, result, error
            )
            return True

    def take(
        self, session: TaskSession, kind: str, generation: int
    ) -> BatchOperationOutcome | None:
        with self._lock:
            if (
                self._discarded
                or self._session is not session
                or self.kind != kind
                or self.generation != generation
            ):
                return None
            outcome = self._outcome
            self._outcome = None
            self._session = None
            self._discarded = True
            return outcome

    def take_for_summary(
        self, session: TaskSession, summary: dict
    ) -> BatchOperationOutcome | None:
        generation = summary.get("generation")
        if not isinstance(generation, int):
            return None
        return self.take(session, summary.get("kind", ""), generation)

    def discard(self) -> None:
        with self._lock:
            self._outcome = None
            self._session = None
            self._discarded = True


def _safe_result_summary(result: object) -> dict[str, int | str]:
    if isinstance(result, BatchReplacePlan):
        return {
            "root_count": len(result.root_ids),
            "candidate_count": result.total_unique_entries,
            "update_count": result.update_count,
            "skipped_count": result.skipped_count,
            "extra_version_count": result.extra_version_count,
            "quality_worsened_count": sum(
                change.quality_worsened for change in result.changes
            ),
        }
    if isinstance(result, BatchReplaceResult):
        return {
            "update_count": result.updated,
            "skipped_count": result.skipped,
            "candidate_count": result.total,
        }
    return {"result_type": type(result).__name__}


def _run_batch_job(
    session: TaskSession,
    kind: str,
    generation: int,
    work: Callable[[TaskSession], object],
    result_channel: BatchOperationResult | None,
) -> None:
    try:
        session.start()
        if kind in {"preview", "skipped"}:
            with cancel_scope(lambda: session.cancel_requested):
                result = work(session)
        else:
            result = work(session)
        if result_channel is not None:
            result_channel.publish(session, state="complete", result=result)
        session.set_summary(
            {
                "state": "complete",
                "kind": kind,
                "generation": generation,
                **_safe_result_summary(result),
            }
        )
    except TaskCancelled:
        if result_channel is not None:
            result_channel.publish(session, state="cancelled")
        session.set_summary(
            {"state": "cancelled", "kind": kind, "generation": generation}
        )
    except Exception as exc:  # noqa: BLE001 - worker boundary must publish unexpected failures
        if result_channel is not None:
            result_channel.publish(session, state="error", error=exc)
        session.add_log(
            f"批次替換{'預覽' if kind == 'preview' else '寫入'}失敗（{type(exc).__name__}）",
            "error",
        )
        session.set_summary(
            {
                "state": "error",
                "kind": kind,
                "generation": generation,
                "error_type": type(exc).__name__,
            }
        )
        session.set_error()
    finally:
        if not session.is_finished:
            session.finish()


def launch_batch_replace_job(
    page,
    *,
    kind: str,
    generation: int,
    work: Callable[[TaskSession], object],
    operation_launcher: Callable[..., object] | None = None,
    result_channel: BatchOperationResult | None = None,
) -> tuple[TaskSession, object]:
    """Own session start/finish and operation policy for one batch dialog job."""
    session = tag_session(
        TaskSession(),
        (
            "Mod DB 批次替換預覽"
            if kind == "preview"
            else "Mod DB 略過原因載入"
            if kind == "skipped"
            else "Mod DB 批次替換寫入"
        ),
        "moddb",
        page=page,
    )

    if result_channel is not None:
        result_channel.bind(session)
    target = partial(_run_batch_job, session, kind, generation, work, result_channel)

    if operation_launcher is not None:
        launched = operation_launcher(
            target,
            kind=kind,
            session=session,
            generation=generation,
        )
    else:
        launched = launch_page_operation(
            page,
            target,
            name=session.name or "Mod DB 批次替換",
            owner=f"moddb-batch-replace-{kind}",
            task_session=session,
            cancellation=(
                CancellationPolicy.COOPERATIVE
                if kind in {"preview", "skipped"}
                else CancellationPolicy.NON_CANCELLABLE
            ),
            commit=(
                CommitPolicy.EPHEMERAL
                if kind in {"preview", "skipped"}
                else CommitPolicy.ATOMIC
            ),
            durability=(
                DurabilityPolicy.RECOMPUTABLE
                if kind in {"preview", "skipped"}
                else DurabilityPolicy.USER_ACTION
            ),
            shutdown=(
                ShutdownPolicy.CANCEL_AND_DRAIN
                if kind in {"preview", "skipped"}
                else ShutdownPolicy.ALLOW_TO_FINISH
            ),
        )
    return session, launched
