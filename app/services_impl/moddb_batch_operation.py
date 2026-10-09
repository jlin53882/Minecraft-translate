"""Run one Mod DB batch-preview or atomic-write operation off the UI thread."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.tasks.task_session import TaskSession, tag_session
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope


def _run_batch_job(
    session: TaskSession,
    kind: str,
    generation: int,
    work: Callable[[TaskSession], object],
) -> None:
    try:
        session.start()
        if kind == "preview":
            with cancel_scope(lambda: session.cancel_requested):
                result = work(session)
        else:
            result = work(session)
        session.set_summary(
            {
                "state": "complete",
                "kind": kind,
                "generation": generation,
                "result": result,
            }
        )
    except TaskCancelled:
        session.set_summary(
            {"state": "cancelled", "kind": kind, "generation": generation}
        )
    except Exception as exc:  # noqa: BLE001 - worker boundary must publish unexpected failures
        session.add_log(
            f"批次替換{'預覽' if kind == 'preview' else '寫入'}失敗：{exc}",
            "error",
        )
        session.set_summary(
            {
                "state": "error",
                "kind": kind,
                "generation": generation,
                "error": exc,
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
) -> tuple[TaskSession, object]:
    """Own session start/finish and operation policy for one batch dialog job."""
    session = tag_session(
        TaskSession(),
        "Mod DB 批次替換預覽" if kind == "preview" else "Mod DB 批次替換寫入",
        "moddb",
        page=page,
    )

    target = partial(_run_batch_job, session, kind, generation, work)

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
                if kind == "preview"
                else CancellationPolicy.NON_CANCELLABLE
            ),
            commit=(
                CommitPolicy.EPHEMERAL if kind == "preview" else CommitPolicy.ATOMIC
            ),
            durability=(
                DurabilityPolicy.RECOMPUTABLE
                if kind == "preview"
                else DurabilityPolicy.USER_ACTION
            ),
            shutdown=(
                ShutdownPolicy.CANCEL_AND_DRAIN
                if kind == "preview"
                else ShutdownPolicy.ALLOW_TO_FINISH
            ),
        )
    return session, launched
