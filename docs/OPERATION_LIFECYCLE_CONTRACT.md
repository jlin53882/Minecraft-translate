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
  bind legacy sessions to their owning page registry; launchers reserve first
  and bind the session to that handle. Global TaskSession observers only project
  events for the session's owner registry and cannot veto another Page's
  admission.
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
| Presentation | `user_visible`, `maintenance` | Whether the operation is projected into TaskManager's task capsule, recent history, and task-change notifications. Both kinds remain registered and participate in admission, busy checks, and shutdown drain. |

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
reopens admission. If a timed-out worker later finishes, the close dialog is
updated with both return and retry choices; admission stays closed until the
user explicitly chooses return. Retrying close never clears a cancellation
request.

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
launchers reserve before starting the worker. “Tested exception” is a narrow,
source-verified case where a side effect is derived/non-authoritative and a
deterministic test proves that the authoritative result is already committed.
Deferred items are explicitly tracked risks, not unclassified operations.

| Operation | Disposition | Owner / policy summary | Remaining risk |
|---|---|---|---|
| Pipeline parent and child steps | REGISTERED | One `pipeline` parent for the sequence; child `TaskSession`s are projections; cancel-and-drain; no ownership gap between steps. | PR-B must prove each blocking step's cancellation bound. |
| Standalone JAR extraction | REGISTERED | `extractor`; cooperative cancel; cancel-and-drain; extraction cancel flag is connected to handle. | ZIP/read blocking bounds are PR-B. |
| Extractor preview | REGISTERED | `extractor-preview`; cancel callback sets preview cancel state; cancel-and-drain. | Same underlying archive blocking bounds are PR-B. |
| Pipeline extractor preview | REGISTERED | `pipeline-extract-preview`; cancel event callback; cancel-and-drain. | Archive blocking bounds are PR-B. |
| Bundler | REGISTERED | `bundler`; non-cancellable, partial output policy, durable user action, drain-only. | ZIP/write bound and atomic publication remain PR-B questions. |
| QC / UntranslatedChecker | REGISTERED | `qc`; non-cancellable, drain-only; worker and final UI batch remain owner-bound. | Service-level blocking bound is not proven here. |
| Cache view operations / query / index rebuild | REGISTERED | `cache-manager`, `cache-query`, `cache-index-rebuild`; non-cancellable, drain-only; saves are durable user actions, reload/index work recomputable. | Global JSON mirror executor is separately deferred below. |
| Rules load / validation / save | REGISTERED | `rules`; validation and the requested durable save share one `rules-save` operation with drain-only shutdown. | Save currently uses direct text write, not atomic replace. |
| Lookup single / batch | REGISTERED | `lookup`; non-cancellable, drain-only. | Provider/network timeout remains PR-B. |
| Translation workers (LM, FTB, KubeJS, MD) | REGISTERED | Reserve before start; sessions bind to owner; cancel-and-drain. | Provider and filesystem blocking bounds are PR-B. |
| Standalone language merge | REGISTERED | `merge`; reserve before session/service start; cancel-and-drain. | Per-call latency bounds remain PR-B. |
| IconPreview load / scan / detail / icon cache | REGISTERED | `icon-preview-load`, `icon-preview-detail`, `icon-preview-cache`; boundary-only cancel, ephemeral output, cancel-and-drain. Nested icon/index executors are contained by the owner operation. | An individual ZIP/PIL/filesystem call may block beyond a checkpoint; PR-B. |
| IconPreview Save | REGISTERED | `icon-preview-save`; non-cancellable, partial-commit, durable user action, drain-only. | `Path.write_text` is not atomic; explicit remaining risk until separately fixed. |
| Dashboard reload | REGISTERED | `dashboard`; non-cancellable, drain-only, maintenance presentation. Its own registry lifecycle is excluded from user task notifications; concurrent refresh requests collapse into a dirty signal and the worker rereads the latest state before becoming idle. | The data loaders have no measured wall-clock bound; registry ownership/drain remains authoritative. |
| ModDB warm stats | REGISTERED | `moddb-warm-stats`; non-cancellable, drain-only, maintenance presentation. The worker owns a separate writable `TranslationDB` connection, skips priority synchronization from its possibly stale launch snapshot, warms derived stats, and closes its connection in `finally`. | Individual SQLite/filesystem calls have no hard wall-clock bound; registry ownership/drain remains authoritative. Event-controlled reload/priority-race tests cover connection isolation and prevent stale priority rollback. |
| ModDB scan / translation / old-AI retranslation | REGISTERED | `moddb-scan`, `moddb-translate`, `moddb-retranslate`; retranslation is page-bound, partial-commit, durable user action, cancel-and-drain. | SQLite transaction and provider-call bounds remain PR-B. |
| ModDB old-AI retranslation preview | REGISTERED | `moddb-retranslate-preview`; non-cancellable, ephemeral, recomputable, drain-only; worker opens its own read-only `TranslationDB` connection and closes it before publishing; result is applied on the UI loop only if its scope generation and database identity still match. SQL VM execution has a 60-second progress-handler deadline; SQLite lock waiting is capped at 5 seconds. | This is not a strict wall-clock bound for filesystem/OS stalls or Python work between SQLite calls. Such work stays owned and drain-only; the UI reports a timeout as failure. |
| Startup search-index rebuild | REGISTERED | `startup-index`; non-cancellable, recomputable, drain-only, maintenance presentation; main passes AppShell's registry before launch. | No dedicated real-data duration bound; drain policy intentionally waits. |
| Cache-root reload | REGISTERED | `cache-root-reload`; non-cancellable, drain-only, maintenance presentation; AppShell injects the registry launcher. Busy checks use authoritative Registry membership, not TaskManager visibility. | Service I/O bound remains PR-B. |
| Resume prompt input check | REGISTERED | `resume-prompt`; non-cancellable, drain-only, maintenance presentation; stale UI result is discarded by page lifecycle. | Filesystem read bound remains PR-B. |
| Cache JSON history mirror executor | TESTED_EXCEPTION (derived mirror only) | `history_append_event()` synchronously appends the authoritative JSONL event and updates the in-memory index before returning; the pretty JSON file is a serial, process-global projection. `history_load_recent()` reads JSONL, never the pretty mirror. `history_flush()` drains the projection at app close. | Pretty-JSON visibility is intentionally eventual and best-effort during normal operation; an external consumer that requires the projection to be current must call `history_flush()`. A deterministic test blocks the mirror and proves the committed JSONL history remains immediately readable. |

Short Flet tasks that only pick files, focus controls, or marshal an already
computed result are not domain operations and remain finite event-loop tasks.
Standalone-view/test fallbacks without an `AppShell` registry are compatibility
paths; the mounted application injects the registry.

## Validation contract

- Success, handled error, unexpected worker exception, and cancellation must
  each end in one terminal registry removal for single and composite operations.
- Cancellation tests distinguish request from completion: a blocked worker
  remains active until released, and new work is rejected during drain.
- Multi-Page legacy TaskSessions must be admitted and projected only by their
  explicitly bound registry; a detached/draining Page cannot reject work on a
  different live Page.
- A durable action with a preparation stage (Rules validation → save) retains
  one owner continuously from admission through the final write.
- A handled `TaskSession.set_error()` followed by `finish()` and normal worker
  return must leave the registry handle failed, not completed.
- Pipeline tests require one parent handle across multiple step sessions.
- Desktop/Web tests cover drain success, timeout, retry, and explicit reopen
  only after idle; a timed-out close dialog recovers its return option when
  workers drain. Web disconnect itself must not dispose the session.
- `docs/WORKER_THREAD_AUDIT.md` and
  `tests/test_worker_thread_inventory.py` enumerate remaining raw Thread call
  sites; production owners use Registry launchers.
- PR-B cancellation checkpoints, pending-work limits, and per-call blocking
  dispositions are recorded below; remaining non-interruptible calls stay
  owned and must not be reported terminal while still running.

## PR-B: cooperative cancellation and resource boundaries

The current PR-B branch implements the first cancellation/resource-boundary
slice; this section records what the code does and what it deliberately does
not promise.

- `cancel_scope()` stores its checker in a `ContextVar`, so the operation's
  cancellation token follows `ContextThreadPoolExecutor` submissions and
  `run_in_context()` child threads. `OperationHandle.run()` installs that scope
  for cooperative and boundary-only operations. `TaskCancelled` remains
  separate from ordinary errors and maps to the cancelled terminal reason.
- `bounded_as_completed()` consumes input lazily and caps submitted-but-not-yet-
  collected work. On cancellation or early exit it stops consuming inputs and
  cancels pending futures; the owning executor context joins work that already
  started before the parent operation can return.
- Bounded windows now cover language merge (folder and ZIP), JAR scan and
  extraction, extractor preview, translation DB scan, LM/FTB file scans and
  translation, FTB Quests/KubeJS counting, cache-shard load, IconPreview cache
  extraction, and icon-index generation. Tests lock the queue window and
  cancellation behavior with event/barrier synchronization.
- Cooperative checkpoints exist at archive-member loops, 64 KiB
  `read_limited()` chunks, per-file/per-entry processing, before output writes,
  IconPreview discovery/hydration/catalog loops, and row preparation. The
  IconPreview async owner installs its cancellation scope across
  `asyncio.to_thread`, so a generation invalidation or owner cancel reaches
  nested worker checkpoints. The extractor pre-scan waits for and joins its
  child thread on cancellation; cancelled preview UI no longer reports `100%`
  / “preview complete”.
- Completed output units are retained according to their existing partial
  commit policy. Cancellation is not rollback: an already-published file or
  completed database transaction can remain, while the owner waits for active
  workers to stop before terminal state.
- User cancellation remains `TaskCancelled` and maps to the `cancelled`
  terminal reason. Archive safety/resource limits (`ZipSizeError` and
  `ArchiveBudgetError`) remain distinct skip/failure outcomes in operation
  statistics; they are never translated into user cancellation. Provider and
  other unexpected exceptions remain failed outcomes.

### Blocking-call dispositions

| Boundary | Current behavior | Cancellation / wait guarantee |
|---|---|---|
| ZIP archive open and central-directory parsing (`ZipFile`, `namelist`, `infolist`) | One library call parses archive metadata before per-member checkpoints. | Not interruptible within the call. The enclosing operation remains registered; desktop drain timeout does not terminate the worker. |
| ZIP member data (`read_limited`) | Reads decompressed bytes in 64 KiB chunks and checks cancellation between reads. | Cooperative between chunks; an individual filesystem/decompressor read has no strict wall-clock limit. |
| ZIP creation (`zipfile.write` / `writestr`) | Bundler writes to a temporary archive and publishes by replace after successful close/validation. Bundler is explicitly non-cancellable and drain-only. | A single write/compression call is not interruptible. Existing output remains until atomic publication; temporary output is removed on failure/close. |
| Filesystem read/write/copy and JSON parse | Checkpoints surround files; some paths still use `read_bytes`, `write_text`, `shutil.copy2`, or a single parser call. | No hard per-call latency bound; current worker ownership/drain policy remains the safety boundary. |
| Gemini provider request | Explicit Requests timeout tuple: connect is capped at 30 seconds (or the lower configured timeout), read uses the existing configured timeout (default 600 seconds); connection failures retry up to three attempts with interruptible backoff. | Cancellation is observed before/after the request and during retry sleep, not while a request call is blocked. Requests connect/read timeouts are not strict end-to-end wall-clock deadlines, so a request can outlive the app's drain deadline and remain tracked. |
| SQLite | Connections use `PRAGMA busy_timeout = 5000`; translation queries/transactions are serialized by their connection lock. ModDB old-AI preview additionally installs a 60-second SQLite progress-handler deadline across its read-only SQL and checks the same deadline between Python processing stages. | The preview deadline interrupts SQLite VM execution and becomes a failed/timeout result, not user cancellation. It is not a hard bound on OS/filesystem stalls, connection setup, or arbitrary Python/native work. Other in-flight queries/transactions are not forcibly interrupted; registry ownership/drain timeout remains authoritative. |
| PIL image decode/resize | Runs in a background operation with cancellation checks between prepared rows/items. | A native decode/resize call is not interruptible mid-call and has no hard time bound. |
| Cache-history JSON mirror | A process-global single-worker executor remains outside individual operation membership; the authoritative event is synchronously committed to JSONL first, and app close waits through `history_flush(timeout=10)`. | The pretty JSON file is a tested, derived, eventually-consistent projection, not an authoritative cache-history write. If close flush times out it can remain stale; JSONL remains readable and is the source of truth. |

The table is a disposition, not a claim that all calls have a fixed maximum
latency. In particular, cancellation checkpoints and blocking-call latency are
separate guarantees. Any future change that makes one of these operations
terminal must preserve the no-writer-after-terminal rule.
