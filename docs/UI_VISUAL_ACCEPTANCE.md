# UI visual and interaction acceptance

This document owns the reproducible real-Flet visual smoke contract. Unit and
FakePage tests remain useful for callbacks and state, but they do not prove that
the Flutter/web renderer has no overflow, overlap, missing glyph, or dialog
layout regression.

## Scope

The harness renders the production `AppShell` and all 13 registered views:

- Dashboard, Pipeline, Extractor, Merge, LM, Translation, QC
- Icon Preview, Cache, Rules, Lookup, Bundler, Config
- Command Palette and a fixed Snackbar state

Every matrix session starts a deterministic `TaskSession`, advances its progress
while navigating all views in the same real Flet session, finishes the task, and
returns to Pipeline. This exercises the top task capsule, sidebar running badge,
status bar, navigation, lazy view construction, and late task completion without
calling a real API.

## Command

```bash
uv sync --frozen --all-groups --python 3.12
uv run --isolated python tools/ui_smoke.py
```

Optional examples:

```bash
# One fast local probe
uv run --isolated python tools/ui_smoke.py \
  --theme dark --viewport 1360x900 --interval 1.5

# Explicit browser and output location
uv run --isolated python tools/ui_smoke.py \
  --browser-executable "C:/Program Files/Google/Chrome/Application/chrome.exe" \
  --output-dir .artifacts/ui-smoke/manual
```

Run with `PYTHONPATH`, `PYTHONHOME`, `VIRTUAL_ENV`, and
`UV_PROJECT_ENVIRONMENT` unset when the shell inherits another Python runtime.

## Deterministic environment

- The harness uses a disposable runtime root for config, cache, species data,
  logs, and replacement rules. It does not read or modify the user's normal
  runtime data.
- `SMOKE_FAKE_KEY` is display-only fixture data. No Gemini or other network API
  is called.
- Flet is served as a local ASGI app and driven by Playwright with an existing
  Chrome or Edge executable. The tool never downloads a browser implicitly.
- Web CanvasKit cannot depend on system fonts. The app registers the bundled
  `assets/fonts/NotoSansTC-Variable.ttf` for Chinese UI text and the existing
  JetBrains Mono asset for monospace text.
- Screenshots and reports are written under `.artifacts/ui-smoke/`, which is
  ignored by Git. They are review artifacts, not repository baselines.

## Default matrix

- Themes: dark and light
- Viewports: 1360×900, 1180×800, 1100×720
- Screenshots per combination: 13 views + Command Palette + Snackbar
- Total default screenshots: 90

`report.json` records the browser path, environment time, screenshot inventory,
AppShell mount time, each lazy view's Python build time, browser-ready time,
console errors, and page errors.

## Review and acceptance

1. Require `report.json` to contain `"ok": true`, no console errors, and no page
   errors.
2. Verify all expected screenshots exist and are non-empty.
3. Review at least one desktop and one minimum-size screenshot for every view in
   both themes. Look for clipping, overflow, overlap, missing CJK glyphs,
   unreadable contrast, stale progress, and Flutter error panels.
4. Compare intentional visual changes with `docs/design/ui-redesign/`; do not
   accept a new screenshot merely because the harness generated it.
5. Preserve the report and screenshots as CI artifacts or task evidence. Do not
   commit generated PNG files.

The initial phase is report-only. Pixel-diff thresholds are intentionally not a
merge gate until repeated runs establish normal renderer variance.

## Baseline update rule

A baseline may be accepted only when the product change is intentional, all
matrix cases render successfully, and a human reviews the changed states. Record
the Flet version, browser executable/version, OS, viewport, theme, report path,
and approval reason. Never replace a known-bad screenshot to make a diff green.
