"""Capture deterministic desktop-sized screenshots from the real Flet web app."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIEWPORTS = ((1360, 900), (1180, 800), (1100, 720))
DEFAULT_THEMES = ("dark", "light")
VIEW_KEYS = (
    "dashboard",
    "pipeline",
    "extractor",
    "merge",
    "lm",
    "translation",
    "qc",
    "icon_preview",
    "cache",
    "rules",
    "lookup",
    "bundler",
    "config",
)


def find_browser_executable(explicit: str | None = None) -> Path:
    """Find an existing Chromium browser without downloading one implicitly."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise FileNotFoundError(f"找不到指定瀏覽器：{path}")

    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "找不到 Chrome/Edge。請安裝瀏覽器或用 --browser-executable 指定路徑。"
    )


def choose_port(preferred: int = 0) -> int:
    """Return an available localhost port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", preferred))
        return int(sock.getsockname()[1])


def parse_viewports(values: list[str] | None) -> tuple[tuple[int, int], ...]:
    """Parse WIDTHxHEIGHT values."""
    if not values:
        return DEFAULT_VIEWPORTS
    parsed = []
    for value in values:
        match = re.fullmatch(r"(\d+)x(\d+)", value.lower())
        if not match:
            raise ValueError(f"無效 viewport：{value}，格式應為 WIDTHxHEIGHT")
        parsed.append((int(match.group(1)), int(match.group(2))))
    return tuple(parsed)


def wait_for_title(page: Page, expected: str, timeout_ms: int) -> str:
    """Wait until the Flet session publishes an exact smoke state title."""
    page.wait_for_function(
        "expected => document.title.startsWith(expected)",
        arg=expected,
        timeout=timeout_ms,
    )
    return page.title()


def _wait_for_server(
    process,
    base_url: str,
    server_log: Path,
    *,
    timeout_seconds: float = 45.0,
    poll_interval: float = 0.2,
    monotonic=time.monotonic,
    sleep=time.sleep,
    urlopen=urllib.request.urlopen,
) -> None:
    """Wait for HTTP readiness with one shared deadline path.

    Args:
        process: Server process exposing ``poll()``.
        base_url: Local readiness URL.
        server_log: Log path used in early-exit/timeout diagnostics.
        timeout_seconds: Maximum readiness wait.
        poll_interval: Delay between failed probes.
        monotonic: Injectable clock for deterministic tests.
        sleep: Injectable sleeper for deterministic tests.
        urlopen: Injectable HTTP probe function.

    Raises:
        RuntimeError: If the server exits before becoming ready.
        TimeoutError: If readiness is not observed before the deadline.
    """
    deadline = monotonic() + timeout_seconds
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"Flet smoke server 提前結束，請查看 {server_log}")
        try:
            with urlopen(base_url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, TimeoutError, urllib.error.URLError):
            # Failed probes are transient until the shared deadline expires.
            pass
        if monotonic() >= deadline:
            raise TimeoutError(f"Flet smoke server 45 秒內未就緒：{server_log}")
        sleep(poll_interval)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def run_smoke(
    *,
    output_dir: Path,
    browser_executable: Path,
    viewports: tuple[tuple[int, int], ...] = DEFAULT_VIEWPORTS,
    themes: tuple[str, ...] = DEFAULT_THEMES,
    interval: float = 2.5,
    port: int = 0,
) -> dict:
    """Run the full screenshot matrix and return its machine-readable report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    port = choose_port(port)
    server_log = output_dir / "server.log"
    report: dict = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "browser": str(browser_executable),
        "port": port,
        "themes": list(themes),
        "viewports": [f"{w}x{h}" for w, h in viewports],
        "cases": [],
        "console_errors": [],
        "page_errors": [],
    }

    with tempfile.TemporaryDirectory(prefix="minecraft-ui-smoke-") as temp_name:
        runtime_root = Path(temp_name)
        shutil.copy2(ROOT / "replace_rules.json", runtime_root / "replace_rules.json")
        env = os.environ.copy()
        for key in (
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
        ):
            env.pop(key, None)
        env["MINECRAFT_TRANSLATOR_SMOKE_ROOT"] = str(runtime_root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        with server_log.open("w", encoding="utf-8", newline="\n") as log_stream:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "tools/ui_smoke_app.py"),
                    "--port",
                    str(port),
                ],
                cwd=ROOT,
                env=env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                base_url = f"http://127.0.0.1:{port}"
                _wait_for_server(process, base_url, server_log)

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True,
                        executable_path=str(browser_executable),
                    )
                    try:
                        for width, height in viewports:
                            for theme in themes:
                                context = browser.new_context(
                                    viewport={"width": width, "height": height},
                                    device_scale_factor=1,
                                )
                                page = context.new_page()
                                page.on(
                                    "console",
                                    lambda message, t=theme, v=f"{width}x{height}": (
                                        report["console_errors"].append(
                                            {
                                                "theme": t,
                                                "viewport": v,
                                                "text": message.text,
                                            }
                                        )
                                        if message.type == "error"
                                        else None
                                    ),
                                )
                                page.on(
                                    "pageerror",
                                    lambda error, t=theme, v=f"{width}x{height}": (
                                        report["page_errors"].append(
                                            {
                                                "theme": t,
                                                "viewport": v,
                                                "text": str(error),
                                            }
                                        )
                                    ),
                                )
                                url = (
                                    f"{base_url}/?theme={theme}&view=dashboard"
                                    f"&interval={interval}"
                                )
                                started = time.perf_counter()
                                page.goto(
                                    url, wait_until="domcontentloaded", timeout=45000
                                )
                                ready_title = wait_for_title(
                                    page, "SMOKE:READY:", 45000
                                )
                                browser_ready_ms = (
                                    time.perf_counter() - started
                                ) * 1000
                                startup_ms = float(ready_title.rsplit(":", 1)[1])

                                for index, view_key in enumerate(VIEW_KEYS):
                                    prefix = f"SMOKE:VIEW:{index}:{view_key}:"
                                    title = wait_for_title(page, prefix, 30000)
                                    build_ms = float(title.rsplit(":", 1)[1])
                                    page.wait_for_timeout(250)
                                    filename = (
                                        f"{theme}-{width}x{height}-{index:02d}-"
                                        f"{_safe_name(view_key)}.png"
                                    )
                                    screenshot = output_dir / filename
                                    page.screenshot(
                                        path=str(screenshot), full_page=True
                                    )
                                    report["cases"].append(
                                        {
                                            "kind": "view",
                                            "theme": theme,
                                            "viewport": f"{width}x{height}",
                                            "view": view_key,
                                            "startup_ms": startup_ms,
                                            "browser_ready_ms": round(
                                                browser_ready_ms, 3
                                            ),
                                            "server_build_ms": round(build_ms, 3),
                                            "screenshot": filename,
                                        }
                                    )

                                for dialog in ("command_palette", "snackbar"):
                                    wait_for_title(
                                        page, f"SMOKE:DIALOG:{dialog}", 30000
                                    )
                                    page.wait_for_timeout(250)
                                    filename = (
                                        f"{theme}-{width}x{height}-dialog-{dialog}.png"
                                    )
                                    page.screenshot(
                                        path=str(output_dir / filename), full_page=True
                                    )
                                    report["cases"].append(
                                        {
                                            "kind": "dialog",
                                            "theme": theme,
                                            "viewport": f"{width}x{height}",
                                            "view": dialog,
                                            "screenshot": filename,
                                        }
                                    )

                                wait_for_title(page, "SMOKE:DONE", 30000)
                                context.close()
                    finally:
                        browser.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    report["ok"] = not report["console_errors"] and not report["page_errors"]
    report["screenshot_count"] = len(report["cases"])
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--browser-executable")
    parser.add_argument("--viewport", action="append")
    parser.add_argument("--theme", action="append", choices=DEFAULT_THEMES)
    parser.add_argument("--interval", type=float, default=2.5)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or ROOT / ".artifacts/ui-smoke" / timestamp
    try:
        report = run_smoke(
            output_dir=output_dir.resolve(),
            browser_executable=find_browser_executable(args.browser_executable),
            viewports=parse_viewports(args.viewport),
            themes=tuple(args.theme or DEFAULT_THEMES),
            interval=args.interval,
            port=args.port,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports actionable failure
        print(f"UI smoke failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
