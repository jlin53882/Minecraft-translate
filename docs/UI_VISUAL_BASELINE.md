# UI visual baseline — ABCF foundation

**Recorded:** 2026-10-02

**Base commit:** `986d058a08a21a679ae440299a1407bf5c1a8bc1`

**Environment:** Windows 11, Python 3.12.10, Flet 1.0.1, Playwright 1.63.0, installed Google Chrome
**Command:** `uv run --isolated python tools/ui_smoke.py --interval 1.5 --output-dir .artifacts/ui-smoke/final`

## Automated result

- Matrix: dark/light × 1360×900, 1180×800, 1100×720
- States per combination: 13 real views + Command Palette + Snackbar
- Screenshots: **90/90 generated**, each non-empty
- Smallest/largest PNG: 83,164 / 205,295 bytes
- Browser console errors: **0**
- Page errors: **0**
- Harness result: **PASS** (`report.json` has `"ok": true`)
- A deterministic `TaskSession` remained active while all 13 views were built and navigated in the same Flet session, then finished before returning to Pipeline.

Generated screenshots and `report.json` remain under `.artifacts/ui-smoke/final/` and are intentionally ignored by Git.

## Representative visual review

Reviewed desktop dark Dashboard, desktop light QC, minimum-size dark Pipeline,
minimum-size light Config, Command Palette, and minimum-size light Snackbar.

### Passed observations

- Traditional Chinese text rendered without missing-glyph boxes using the bundled Noto Sans TC asset.
- No Flutter error panel, blank application shell, or control overlap was observed in the reviewed desktop states.
- Command Palette remained within the viewport with a working modal scrim and readable content.
- Dark/light contrast was generally readable in the reviewed states.

### Recorded visual findings

1. At 1100×720, long Pipeline and Config content extends below the visible area and requires vertical scrolling. The screenshots show partial lower cards/sections; no Row overflow or error panel was observed. Keep this as a minimum-size scroll acceptance item in future reviews.
2. At 1100×720 light mode, the Snackbar touches the bottom edge and partially covers the status bar. The text remains readable, but the placement is a real visual finding. It is **not silently accepted as a new baseline** and should be handled in a separate product-UI fix because this ABCF work establishes the harness rather than redesigning production layout.

## Baseline status

The reproducible harness and artifact contract are accepted. The Snackbar placement finding remains open; therefore this record is not a claim that every existing visual state is defect-free. Future UI changes must rerun the same matrix and compare reviewed states rather than replacing screenshots automatically.
