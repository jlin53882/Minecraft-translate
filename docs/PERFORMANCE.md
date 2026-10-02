# Performance measurement contract

This document defines a reproducible baseline, not an optimization target. The
same command and fixture must be used before and after a change; numbers from
different platforms or browser versions are not directly comparable.

## Metrics

- AppShell mount time and browser-ready time from the real Flet smoke report
- First lazy build time for every registered view
- Explicit-list JAR scan throughput for deterministic 10- and 100-JAR fixtures,
  including nested JAR paths
- LM batch selection cost with token budgeting enabled and disabled
- Python-side construction cost for 1k, 5k, and 10k-row Flet ListViews

The baseline uses no real Gemini API and performs no external network requests.
Synthetic JARs and translation items are generated in temporary directories.

## Commands

Generate the real-Flet timing source:

```bash
uv run --isolated python tools/ui_smoke.py \
  --output-dir .artifacts/ui-smoke/performance-source
```

Generate JSON and Markdown performance reports:

```bash
uv run --isolated python tools/performance_baseline.py \
  --ui-report .artifacts/ui-smoke/performance-source/report.json \
  --output-dir .artifacts/performance/current \
  --repeats 7
```

Outputs:

- `.artifacts/performance/current/performance.json` — machine-readable values
- `.artifacts/performance/current/performance.md` — human-readable summary

Run with `PYTHONPATH`, `PYTHONHOME`, `VIRTUAL_ENV`, and
`UV_PROJECT_ENVIRONMENT` unset when the shell inherits another Python runtime.

## Reproducibility

Record and compare all of the following:

- OS and hardware
- Python, Flet, Playwright, and browser versions
- Git commit
- exact command, repeats, theme, viewport, and fixture sizes
- cold or warm run status

Use the median as the primary value and retain min/max to expose variance. A
single run is evidence that the harness works, not evidence for a regression
budget.

## Regression policy

1. Run at least three complete baselines on the same machine before proposing a
   threshold.
2. Compare medians and observed ranges. Investigate changes that exceed normal
   variance; do not immediately classify them as product regressions.
3. Keep this report non-blocking initially. Add a CI warning or hard gate only
   after the fixture and machine class are stable.
4. Do not optimize inside the baseline PR. Any optimization needs its own
   behavior tests and before/after evidence.
5. Browser/render timing, Python view construction, JAR I/O, and token
   estimation are separate metrics; do not combine them into one score.

## Large-list and event-loop follow-up

List construction is measured now because Cache, Rules, and QC can display
large datasets. Real scroll-frame timing and the longest blocking synchronous
handler require renderer instrumentation beyond this first baseline. Add those
measurements only with a deterministic interaction and an explicit owner; do not
infer event-loop blocking from Python construction time alone.
