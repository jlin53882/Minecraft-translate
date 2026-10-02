# Performance baseline — ABCF foundation

**Recorded:** 2026-10-02

**Base commit:** `986d058a08a21a679ae440299a1407bf5c1a8bc1`

**Environment:** Windows 11 (10.0.26200), AMD64 Family 25 Model 117, Python 3.12.10, Flet 1.0.1, Playwright 1.63.0
**Commands:**

```bash
uv run --isolated python tools/ui_smoke.py \
  --interval 1.5 --output-dir .artifacts/ui-smoke/final
uv run --isolated python tools/performance_baseline.py \
  --ui-report .artifacts/ui-smoke/final/report.json \
  --output-dir .artifacts/performance/final \
  --repeats 7
```

The complete machine-readable result is `.artifacts/performance/final/performance.json`. Generated artifacts are ignored by Git.

## UI timing

- AppShell mount median: **29.242 ms** (27.981–101.620 ms)
- Browser-ready median: **1,234.740 ms** (1,219.160–1,593.855 ms)

First lazy view build medians:

- Dashboard 4.450 ms
- Pipeline 22.689 ms
- Extractor 10.712 ms
- Merge 14.352 ms
- LM 13.716 ms
- Translation 24.585 ms
- QC 14.504 ms
- Icon Preview 7.789 ms
- Cache 42.525 ms
- Rules 11.454 ms
- Lookup 9.826 ms
- Bundler 23.751 ms
- Config 44.174 ms

The first session had large cold outliers for Pipeline/QC and several data views. The median is the comparison value; the range is retained so cold-start variance is not hidden.

## JAR scan

Synthetic fixtures contain deterministic lang JSON and metadata, with every tenth JAR stored in a nested directory. `scan_jars()` receives the explicit file list and uses four workers.

- 10 JAR: **2.123 ms median**, 4,710.316 JAR/s
- 100 JAR: **16.624 ms median**, 6,015.399 JAR/s

## LM batch selection

10,000 deterministic translation items, count cap 300:

- Count-only: **0.002 ms median**, 500,000 ops/s
- Token budget: **0.820 ms median**, 1,219.512 ops/s

The benchmark measures local selection only; it does not call Gemini or any other network API.

## Large-list construction

Python-side Flet `ListView` construction:

- 1,000 rows: **8.584 ms median**
- 5,000 rows: **44.678 ms median**
- 10,000 rows: **86.287 ms median**

This does not claim renderer scroll-frame timing. Real scrolling and longest event-loop blocking handler remain separate future measurements.

## Regression policy

This is the first reproducible baseline, so no hard threshold is established. Run the full baseline at least three times on the same machine and browser before proposing a warning budget. Compare each metric independently and investigate changes outside observed variance; do not combine them into one score or compare across platforms.
