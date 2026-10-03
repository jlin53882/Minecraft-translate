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
- Viewports: 1360×900, 1180×800, 1100×720, 900×700, 720×900
- The last two sizes are narrow / portrait probes below the desktop minimum; they
  are responsive checks, not a change to `MIN_WINDOW_SIZE`.
- Per combination: 13 first-build views, 13 same-session revisits, Command
  Palette, Command Palette reopen, and Snackbar.

`report.json` records browser path/version, OS, Python/Flet/Playwright versions,
source commit and whether the worktree has uncommitted changes, screenshot
inventory, AppShell mount time, each lazy view's first
Python build time, same-session revisit navigation time, browser-ready time,
missing/duplicate cases, empty artifacts, console errors, page errors, and a real-
session dispose/late-task probe. Raw timings are kept per case rather than
replaced by one aggregate.

## Supplementary scenarios

Run one scenario per command. Every scenario uses the same disposable runtime
root and records dark/light screenshots for the requested viewports.

```bash
# Primary workflow Dialog gallery; no extraction/translation run or external API call
uv run --isolated python tools/ui_smoke.py \
  --scenario dialogs --viewport 1360x900 --viewport 1100x720 \
  --viewport 900x700 --viewport 720x900 --interval 0.5

# Dashboard states; repeat with empty, data, running, error, cancelled, loading
uv run --isolated python tools/ui_smoke.py \
  --scenario empty --viewport 1100x720 --viewport 720x900 --interval 0.5
```

The Dialog gallery invokes the production Pipeline extract/merge/translate/bundle
and one-click entry points, closes and reopens both the extract and Pipeline
Merge Dialogs, and includes the standalone extractor run/preview and merge-result
summary Dialogs. The one-click workflow advances through steps 1–4 via its real
callbacks, captures each step, dismisses the wizard, and captures a fresh
reopen. The step screenshots are visual evidence, not a pixel-diff gate.

The gallery closes managed dialogs through Flet's dialog stack and signals raw
`page.overlay` AlertDialogs with `open=False`. It keeps those controls mounted
until the reverse transition finishes, then removes only closed entries. This
two-phase lifecycle prevents a stale modal route from appearing behind the next
case.

### Smoke title and screenshot acknowledgement contract

`wait_for_title()` uses exact matching by default. Prefix matching is opt-in and
is used only for titles that intentionally include a changing suffix, such as a
view build duration or the initial ready marker. Dialog-gallery capture waits
for the exact `SMOKE:DIALOG:<key>:OPEN` title; the sibling `OPENING` state cannot
release the screenshot step.

The capture/advance order is:

1. The app publishes `OPENING`, builds and opens the production Dialog, then
   publishes the exact `OPEN` state.
2. Playwright waits for that exact state and captures the screenshot.
3. The capture helper verifies the screenshot file exists and is non-empty before
   adding the case and writing its `.smoke-acks/<token>.done` marker.
4. The app consumes the marker before dismissing the Dialog or advancing to the
   next case.

If capture fails or the artifact is empty, no acknowledgement is sent; a stale
or incomplete screenshot cannot be reported as a completed Dialog case.

For each Dialog-gallery theme/viewport, the harness also sends a real Playwright
wheel event over the Pipeline Merge body and records a `pipeline_merge_bottom`
case before acknowledging the Dialog. Review this second image to confirm the
lower Patchouli controls are reachable while the footer actions remain visible;
the structural layout test alone cannot prove the rendered scroll interaction.

The state `cancelled` means **cancel requested while the worker is still active**.
`TaskSession` / `TaskManager` currently has no terminal `CANCELLED` status, so this
case does not claim a distinct completed-cancelled Dashboard state.

## Review and acceptance

1. Require `report.json` to contain `"ok": true`, no console/page errors, no
   missing or duplicate case keys, no empty/missing screenshot artifacts, and one
   passed lifecycle probe for each theme/viewport session.
2. Verify all expected screenshots exist and are non-empty.
3. Review first-build and revisit screenshots for every view, and every Dialog
   screenshot in both themes. Include desktop, the 1100×720 supported minimum, and
   narrow/portrait probes. Look for clipping, overflow, overlap, missing CJK
   glyphs, unreadable contrast, stale progress, and Flutter error panels.
4. Compare intentional visual changes with `docs/design/ui-redesign/`; do not
   accept a new screenshot merely because the harness generated it.
5. Preserve the report and screenshots as CI artifacts or task evidence. Do not
   commit generated PNG files.

Every screenshot case sets `needs_visual_review`. CanvasKit content is painted to
canvas and does not expose reliable DOM bounds for internal Dialog overflow; a
complete case list and non-empty PNGs are not machine proof that content fits.
The reviewer must inspect the images and record pass/fail/blocked in the PR/task
evidence.

## Interaction / lifecycle acceptance checks

The automated harness checks same-session first build/revisit, command-palette
close/reopen, workflow Dialog close/reopen, and dispose followed by a late
`TaskSession` event. Also run and record these real user interactions when a
change touches the corresponding UI:

- Review the captured one-click wizard steps 1–4, dismissal, and reopen.
- While a real task is busy, navigate away/back; verify progress/log freshness,
  busy guards, and the actual cancel control.
- At minimum and portrait widths, confirm each major Dialog remains usable and
  its close/confirm actions are not clipped.
- After teardown, verify subscriptions are removed and a late callback does not
  access a disposed view.

The one-click step/dismiss/reopen sequence and same-session busy-task navigation
are covered by the deterministic real-Flet scenario. The `cancelled` scenario
invokes the real `TaskSession.request_cancel()` path and records the requested
state; the repository's cancel-control test covers the control callback. A
terminal `CANCELLED` state is not claimed because the product has no such
status. Any remaining desktop-only close/destroy behavior must still be marked
blocked or unverified rather than inferred from a screenshot.

## PR #140 local acceptance evidence (2026-10-03)

- `views`: 290/290 cases across both themes and all five viewports, with first
  build/revisit pairs, no console/page errors, and ten teardown probes.
- `dialogs`: 64/64 cases at 1100×720 and 900×700 in both themes; the four
  Pipeline Merge bottom-scroll screenshots show Patchouli controls reachable
  while the footer remains visible.
- One-click wizard cases ran at both required heights and themes, including
  steps 1–4, dismiss, and reopen; no console/page errors.
- Dashboard state checks: empty, data, running, error, cancellation-requested,
  and loading each produced 4/4 cases at 900×700 and 720×900 in both themes.

The reports and PNGs are ignored local artifacts under `.artifacts/pr1/`; they
are evidence for the task/PR and are not committed baselines.

The initial phase is report-only. Pixel-diff thresholds are intentionally not a
merge gate until repeated runs establish normal renderer variance.

## Recording results

Screenshots and `report.json` are review evidence, not repository baselines:
attach them to the PR or task that needs them and do not commit generated PNG
files. Record the Flet version, browser executable/version, OS, viewport, theme,
and report path with the evidence. A visual defect found by the harness belongs
in an issue; never replace or ignore a known-bad screenshot to make a review
look clean.
