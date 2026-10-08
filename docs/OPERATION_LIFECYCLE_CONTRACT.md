# Operation lifecycle contract (PR-A)

Date: 2026-10-08

This document defines ownership, admission, shutdown, and coverage for PR-A. It
does not claim that every blocking call is immediately interruptible; bounded
blocking-call behavior and deeper cooperative cancellation are PR-B work.

## Authoritative ownership

- `OperationRegistry` is the sole source of active-operation membership and
  admission state. `TaskManager` projects registry handles and `TaskSession`
  progress into UI task cards; its `_active` session map is presentation data,
  not a second membership registry.
- `reserve()` performs the admission check and registration under one lock.
  A worker launcher must only be called after a successful reservation. A
  rejected reservation has no worker, service, or write side effect.
- `TaskSession.start()` requests admission for legacy callers. Mounted views
  that launch work directly reserve first and bind the session to that handle.
- A pipeline has one parent handle for the entire sequence. Step sessions are
  projections attached to the parent; a child finish or step handoff does not
  finish the parent.
- `cancel_requested` is not terminal. Registry membership remains until the
  worker wrapper returns, including cleanup and final writes owned by it.
  `_finish()` is idempotent and is the only removal path for active handles.
- Route unmount is not operation completion. View pollers/generation guards may
  stop presenting results without releasing an operation that is still working.

## Operation behavior dimensions

`OperationDescriptor` records four independent dimensions:

| Dimension | Values | Meaning |
|---|---|---|
| Ownership | `owner`, `operation_id`, optional `parent_id` | Which registry handle owns the work, and whether it belongs to a composite parent. |
| Cancellation | `cooperative`, `boundary_only`, `non_cancellable` | Whether the work observes cancellation, only at known boundaries, or cannot be interrupted. |
| Commit | `ephemeral`, `partial_commit_allowed`, `atomic_commit` | Whether output is disposable, completed units may remain, or publication must be atomic. |
| Durability | `recomputable`, `durable_user_action` | Whether output can be regenerated or represents a user-requested durable action. |
| Shutdown | `cancel_and_drain`, `drain_only`, `allow_to_finish`, `transfer_ownership` | What app shutdown requests and waits for. `drain_only` does not imply cancel. |

Commit and durability are separate so an explicit user save can be durable
without falsely claiming that the underlying file write is atomic.

## Admission and shutdown states

```text
OPEN
  └─ begin_shutdown(): atomically reject new reservations
       ├─ CANCEL_AND_DRAIN handles receive cancel requests
       ├─ DRAIN_ONLY / ALLOW_TO_FINISH continue under their policy
       └─ wait for actual handle completion
             ├─ deadline reached with active handles → DRAIN_TIMEOUT
             │    admission stays closed; handles stay visible/owned;
             │    retry close, or explicitly return only after idle
             └─ idle + final flush succeeds → CLOSED
```

Desktop close timeout is a blocking policy, not a hint to resume work. There is
no implicit “Keep Running” after cancellation was requested. Returning to the
app is offered only when no operation remains active; that explicit transition
reopens admission. Retrying close never clears a cancellation request.

For Flet Web, `on_disconnect` is not terminal: a client may reconnect and the
session's operation ownership remains. `on_close` is treated as session expiry:
it begins shutdown, waits up to the configured drain deadline, and then
flushes durable shared state when idle before marking closed, then disposes the
expired UI session. If the deadline expires, handles remain in the registry and
the app does not reopen admission; there is no reconnect assumption after the
session itself has expired. A failed final flush is logged, UI teardown still
runs, and admission remains closed rather than claiming a successful close.

## Known long-running operation coverage

Every audited operation has a disposition. “Registered” means production
launchers reserve before starting the worker. Deferred items are explicitly
tracked risks, not unclassified operations.

| Operation | Disposition | Owner / policy summary | Remaining risk |
|---|---|---|---|
| Pipeline parent and child steps | REGISTERED | One `pipeline` parent for the sequence; child `TaskSession`s are projections; cancel-and-drain; no ownership gap between steps. | PR-B must prove each blocking step's cancellation bound. |
| Standalone JAR extraction | REGISTERED | `extractor`; cooperative cancel; cancel-and-drain; extraction cancel flag is connected to handle. | ZIP/read blocking bounds are PR-B. |
| Extractor preview | REGISTERED | `extractor-preview`; cancel callback sets preview cancel state; cancel-and-drain. | Same underlying archive blocking bounds are PR-B. |
| Pipeline extractor preview | REGISTERED | `pipeline-extract-preview`; cancel event callback; cancel-and-drain. | Archive blocking bounds are PR-B. |
| Bundler | REGISTERED | `bundler`; non-cancellable, partial output policy, durable user action, drain-only. | ZIP/write bound and atomic publication remain PR-B questions. |
| QC / UntranslatedChecker | REGISTERED | `qc`; non-cancellable, drain-only; worker and final UI batch remain owner-bound. | Service-level blocking bound is not proven here. |
| Cache view operations / query / index rebuild | REGISTERED | `cache-manager`, `cache-query`, `cache-index-rebuild`; non-cancellable, drain-only; saves are durable user actions, reload/index work recomputable. | Global JSON mirror executor is separately deferred below. |
| Rules load / validation / save | REGISTERED | `rules`, `rules-validation`, `rules-save`; drain-only; saves are durable user actions. | Save currently uses direct text write, not atomic replace. |
| Lookup single / batch | REGISTERED | `lookup`; non-cancellable, drain-only. | Provider/network timeout remains PR-B. |
| Translation workers (LM, FTB, KubeJS, MD) | REGISTERED | Reserve before start; sessions bind to owner; cancel-and-drain. | Provider and filesystem blocking bounds are PR-B. |
| Standalone language merge | REGISTERED | `merge`; reserve before session/service start; cancel-and-drain. | Per-call latency bounds remain PR-B. |
| IconPreview load / scan / detail / icon cache | REGISTERED | `icon-preview-load`, `icon-preview-detail`, `icon-preview-cache`; boundary-only cancel, ephemeral output, cancel-and-drain. Nested icon/index executors are contained by the owner operation. | An individual ZIP/PIL/filesystem call may block beyond a checkpoint; PR-B. |
| IconPreview Save | REGISTERED | `icon-preview-save`; non-cancellable, partial-commit, durable user action, drain-only. | `Path.write_text` is not atomic; explicit remaining risk until separately fixed. |
| Dashboard reload | REGISTERED | `dashboard`; non-cancellable, drain-only. | Boundedness has not been measured; remains DEFERRED_WITH_EXPLICIT_RISK. |
| ModDB warm stats | REGISTERED | `moddb-warm-stats`; non-cancellable, drain-only. | Deterministic reload/close race test is still needed; SQLite lock serializes close and each query, but multi-query warm sequence may stop early. DEFERRED_WITH_EXPLICIT_RISK. |
| ModDB scan / translation | REGISTERED | `moddb-scan`, `moddb-translate`; session-linked handles, cancel-and-drain. | SQLite transaction and archive-call bounds remain PR-B. |
| Startup search-index rebuild | REGISTERED | `startup-index`; non-cancellable, recomputable, drain-only; main passes AppShell's registry before launch. | No dedicated real-data duration bound; drain policy intentionally waits. |
| Cache-root reload | REGISTERED | `cache-root-reload`; non-cancellable, drain-only; AppShell injects the registry launcher. | Service I/O bound remains PR-B. |
| Resume prompt input check | REGISTERED | `resume-prompt`; non-cancellable, drain-only; stale UI result is discarded by page lifecycle. | Filesystem read bound remains PR-B. |
| Cache JSON history mirror executor | DEFERRED_WITH_EXPLICIT_RISK | Process-global serial executor is not an individual registry handle; app close now calls `history_flush()` before log flush and refuses close on timeout. | A mirror write may outlive its originating cache action and that action's UI terminal status; executor-level membership and normal-operation completion linkage are not implemented in PR-A. |

Short Flet tasks that only pick files, focus controls, or marshal an already
computed result are not domain operations and remain finite event-loop tasks.
Standalone-view/test fallbacks without an `AppShell` registry are compatibility
paths; the mounted application injects the registry.

## Validation contract

- Success, handled error, unexpected worker exception, and cancellation must
  each end in one terminal registry removal for single and composite operations.
- Cancellation tests distinguish request from completion: a blocked worker
  remains active until released, and new work is rejected during drain.
- Pipeline tests require one parent handle across multiple step sessions.
- Desktop/Web tests cover drain success, timeout, retry, and explicit reopen
  only after idle. Web disconnect itself must not dispose the session.
- `docs/WORKER_THREAD_AUDIT.md` and
  `tests/test_worker_thread_inventory.py` enumerate remaining raw Thread call
  sites; production owners use Registry launchers.
- PR-B remains responsible for cooperative checkpoint coverage and explicit
  timeout/chunking/maximum-wait policy for non-interruptible blocking calls.
