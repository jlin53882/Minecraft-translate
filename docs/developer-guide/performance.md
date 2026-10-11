# Performance measurement contract

This document defines a reproducible measurement contract, not an optimization
target. Recorded numbers are not kept in the repository: they depend on the
machine and drift quickly, so attach them to the PR or issue that needs them. The
same command and fixture must be used before and after a change; numbers from
different platforms or browser versions are not directly comparable.

## Metrics

- AppShell mount time and browser-ready time from the real Flet smoke report
- First lazy build and same-session warm revisit for every registered view
- Dashboard cache-overview assembly/active-shard read, replace-rule file read,
  and Python-side activity/control refresh with a fixed task list
- Explicit-list JAR scan throughput for deterministic 10- and 100-JAR fixtures,
  including nested JAR paths
- LM batch selection cost with token budgeting enabled and disabled
- Python-side construction cost for 1k, 5k, and 10k-row Flet ListViews

The baseline uses no real Gemini API and performs no external network requests.
Synthetic JARs, translation items, Dashboard cache shards and replace rules are
generated in temporary directories. Dashboard task rendering is measured as
Python-side control refresh; it is not browser frame or scroll performance.

## Commands

Generate the real-Flet timing source:

```bash
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-source
```

Generate JSON and Markdown performance reports:

```bash
# Run the same real-Flet views matrix three times. Pass all three reports below.
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-run-1
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-run-2
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-run-3

uv run --isolated python tools/performance_baseline.py \
  --ui-report .artifacts/ui-smoke/performance-run-1/report.json \
  --ui-report .artifacts/ui-smoke/performance-run-2/report.json \
  --ui-report .artifacts/ui-smoke/performance-run-3/report.json \
  --output-dir .artifacts/performance/current \
  --repeats 7 \
  --runs 3
```

The number of `--ui-report` arguments must be either zero, one, or exactly the
requested `--runs` value. Supplying one report for multiple runs is supported
for engine-only diagnostics, but the output records `ui_report_reused=true` and
must not be interpreted as independent UI measurements. For a complete baseline,
use one freshly generated real-Flet report per run as shown above.

Outputs (under the Git-ignored `.artifacts/`):

- `.artifacts/performance/current/performance.json` — machine-readable values
- `.artifacts/performance/current/performance.md` — human-readable summary
- `performance.json` preserves each complete run under `runs[]`; every direct
  benchmark keeps its `samples_ms` list, plus median, min, max, population
  standard deviation, repeat count, environment and source commit.
- The top-level `summary` preserves run medians, run-to-run range and standard
  deviation for UI startup/cold/warm timings, JAR, batching, lists and Dashboard.
- `measurement` records the requested run count, repeat count, UI report paths,
  whether one UI report was reused, and the exact command. A single UI report
  reused across multiple engine runs is explicitly marked and is not equivalent
  to three complete real-Flet runs.
- The linked UI smoke report preserves each first-build/revisit timing as a case;
  the performance report references that report and summarizes those cases.

Run with `PYTHONPATH`, `PYTHONHOME`, `VIRTUAL_ENV`, and
`UV_PROJECT_ENVIRONMENT` unset when the shell inherits another Python runtime.

## Reproducibility

Record and compare all of the following:

- OS and hardware
- Python, Flet, Playwright, and browser versions
- Git commit and whether the worktree contains uncommitted changes
- exact command, repeats, theme, viewport, and fixture sizes
- cold first construction or warm same-session revisit status

Here, **cold** means a view's first lazy construction in a fresh Flet session;
**warm** means navigating back to the already-built view in that same session.
This does not flush the operating-system disk cache and must not be reported as a
cold-disk measurement.

Use the median as the primary value and retain raw samples, min/max and standard
deviation to expose both within-run and run-to-run variance. The report is
traceable because each raw sample remains under its numbered `runs[]` entry,
alongside the source commit, environment, input report path and command.

## Regression policy

1. Run at least three complete baselines on the same machine, including three
   real-Flet UI reports, before proposing a threshold.
2. Compare medians and observed ranges. Investigate changes that exceed normal
   variance; do not immediately classify them as product regressions.
3. Keep this report non-blocking initially. Add a CI warning or hard gate only
   after the fixture and machine class are stable.
4. Do not mix optimization into a measurement-only change. Any optimization
   needs its own behavior tests and before/after evidence.
5. Browser/render timing, Python view construction, JAR I/O, and token
   estimation are separate metrics; do not combine them into one score.

The protocol is measurement-first: a baseline describes observed behavior and
variance; it does not by itself establish a performance budget. Set a budget or
CI threshold only after the fixture, platform, renderer, and acceptable variance
are explicitly agreed upon.

## Large-list and event-loop follow-up

List construction is measured now because Cache, Rules, and QC can display
large datasets. Real scroll-frame timing and the longest blocking synchronous
handler require renderer instrumentation beyond this first baseline. Add those
measurements only with a deterministic interaction and an explicit owner; do not
infer event-loop blocking from Python construction time alone.
