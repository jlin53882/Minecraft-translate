"""從真實的 Flet 網頁版應用程式擷取確定性的桌面尺寸截圖。"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from importlib import metadata
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIEWPORTS = (
    (1360, 900),
    (1180, 800),
    (1100, 720),
    (900, 700),
    (720, 900),
)
DEFAULT_THEMES = ("dark", "light")
SMOKE_SCENARIOS = (
    "views",
    "dialogs",
    "empty",
    "data",
    "running",
    "error",
    "cancelled",
    "loading",
)
DIALOG_SMOKE_KEYS = (
    "pipeline_extract",
    "pipeline_extract_reopen",
    "pipeline_merge",
    "pipeline_merge_reopen",
    "pipeline_translate",
    "pipeline_bundle",
    "pipeline_one_click",
    "extractor_run",
    "extractor_preview",
    "merge_summary",
)
DIALOG_SCROLL_CASES: dict[str, str] = {
    "pipeline_merge": "pipeline_merge_bottom",
    "pipeline_merge_reopen": "pipeline_merge_reopen_bottom",
}
SCENARIO_NOTES = {
    "cancelled": (
        "此案例截取取消要求已送出但 worker 尚未結束的畫面；"
        "TaskSession / TaskManager 目前沒有獨立的終止 CANCELLED 狀態。"
    ),
    "dialogs": "截圖需人工檢查 Dialog 是否超出視窗；CanvasKit canvas 不提供可靠 DOM bounds。",
}
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
    """尋找本機已安裝的 Chromium 系瀏覽器，不會隱含下載。"""
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
    """回傳一個可用的 localhost 連接埠。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", preferred))
        return int(sock.getsockname()[1])


def parse_viewports(values: list[str] | None) -> tuple[tuple[int, int], ...]:
    """解析 WIDTHxHEIGHT 格式的視窗尺寸。"""
    if not values:
        return DEFAULT_VIEWPORTS
    parsed = []
    for value in values:
        match = re.fullmatch(r"(\d+)x(\d+)", value.lower())
        if not match:
            raise ValueError(f"無效 viewport：{value}，格式應為 WIDTHxHEIGHT")
        parsed.append((int(match.group(1)), int(match.group(2))))
    return tuple(parsed)


def _title_matches(title: str, expected: str, *, exact: bool) -> bool:
    """依明確指定的 exact 或 prefix 契約比對 smoke 標題。"""
    return title == expected if exact else title.startswith(expected)


def wait_for_title(
    page: Page,
    expected: str,
    timeout_ms: int,
    *,
    exact: bool = True,
) -> str:
    """等待 Flet session 發布完整 smoke 標題或明確指定的前綴。

    Args:
        page: 顯示隔離 smoke app 的 Playwright 頁面。
        expected: 預期的完整標題，或 prefix 模式的固定標題開頭。
        timeout_ms: 等待狀態的最長毫秒數。
        exact: True 時只接受完全相等；False 時接受標題前綴。

    Returns:
        與預期狀態相符的完整頁面標題。

    Raises:
        TimeoutError: 在期限內未出現預期標題。
        RuntimeError: smoke app 發布錯誤狀態，或回傳標題不符合契約。
    """
    try:
        title_handle = page.wait_for_function(
            "({ expected, exact }) => { const title = document.title; "
            "const matched = exact ? title === expected : title.startsWith(expected); "
            "return matched || title.startsWith('SMOKE:ERROR:') ? title : false; }",
            arg={"expected": expected, "exact": exact},
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError as exc:
        raise TimeoutError(
            f"等待 smoke 標題逾時：expected={expected!r}, current={page.title()!r}"
        ) from exc
    title = str(title_handle.json_value())
    if title.startswith("SMOKE:ERROR:"):
        raise RuntimeError(f"smoke app 情境失敗：{title}")
    if not _title_matches(title, expected, exact=exact):
        raise RuntimeError(
            f"smoke 標題違反比對契約：expected={expected!r}, current={title!r}, exact={exact}"
        )
    return title


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
    """以單一共用期限等待 HTTP 就緒。

    Args:
        process: 提供 ``poll()`` 的伺服器行程。
        base_url: 本機就緒檢查用的 URL。
        server_log: 提早結束或逾時時，用於診斷的 log 路徑。
        timeout_seconds: 等待就緒的最長秒數。
        poll_interval: 探測失敗後的等待間隔。
        monotonic: 可注入的時鐘，供確定性測試使用。
        sleep: 可注入的 sleep 函式，供確定性測試使用。
        urlopen: 可注入的 HTTP 探測函式。

    Raises:
        RuntimeError: 伺服器在就緒前就結束。
        TimeoutError: 在期限內未觀察到就緒。
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
            # 在共用期限到期前，探測失敗視為暫時性。
            pass
        if monotonic() >= deadline:
            raise TimeoutError(f"Flet smoke server 45 秒內未就緒：{server_log}")
        sleep(poll_interval)


def _safe_name(value: str) -> str:
    """把 case 識別值轉成跨平台安全的檔名片段。"""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def _git_commit() -> str:
    """取得量測工作目錄的 commit，失敗時保留明確未知值。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def _git_dirty() -> bool | None:
    """記錄工作樹是否含尚未提交的量測變更。"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def _expected_case_keys(
    scenario: str,
    themes: tuple[str, ...],
    viewports: tuple[tuple[int, int], ...],
) -> set[tuple[str, str, str, str]]:
    """建立此 scenario 應產生的完整 case key 集合。"""
    expected: set[tuple[str, str, str, str]] = set()
    for theme in themes:
        for width, height in viewports:
            viewport = f"{width}x{height}"
            if scenario == "views":
                expected.update(
                    (kind, theme, viewport, key)
                    for kind, keys in (
                        ("view", VIEW_KEYS),
                        ("view_revisit", VIEW_KEYS),
                        (
                            "dialog",
                            ("command_palette", "command_palette_reopen", "snackbar"),
                        ),
                    )
                    for key in keys
                )
            elif scenario == "dialogs":
                expected.update(
                    ("dialog_gallery", theme, viewport, key)
                    for key in DIALOG_SMOKE_KEYS
                )
                expected.update(
                    ("dialog_scroll", theme, viewport, key)
                    for key in DIALOG_SCROLL_CASES.values()
                )
            else:
                expected.add(("state", theme, viewport, scenario))
    return expected


def _capture_case(page: Page, output_dir: Path, filename: str) -> str:
    """擷取單一畫面，確認截圖已寫入後回傳相對檔名。

    Args:
        page: 要擷取的 Playwright 頁面。
        output_dir: 此次 smoke run 的截圖目錄。
        filename: 唯一的截圖檔名。

    Returns:
        已寫入截圖的相對檔名。

    Raises:
        RuntimeError: 截圖檔不存在或為空時引發。
    """
    screenshot_path = output_dir / filename
    page.screenshot(path=str(screenshot_path), full_page=True)
    if not screenshot_path.is_file() or screenshot_path.stat().st_size == 0:
        raise RuntimeError(f"截圖未完整寫入：{screenshot_path}")
    return filename


def _ack_capture(runtime_root: Path, token: str) -> None:
    """通知隔離 smoke app 已完成此案例截圖，允許它切到下一狀態。"""
    marker = runtime_root / ".smoke-acks" / f"{token}.done"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("captured", encoding="utf-8")


def _run_view_scenario(
    page: Page,
    output_dir: Path,
    runtime_root: Path,
    cases: list[dict],
    behavior_checks: list[dict],
    *,
    theme: str,
    viewport: str,
    startup_ms: float,
    browser_ready_ms: float,
) -> None:
    """擷取所有頁面的首次建構、回訪、Command Palette 與 Snackbar。"""
    for index, view_key in enumerate(VIEW_KEYS):
        title = wait_for_title(
            page, f"SMOKE:VIEW:{index}:{view_key}:", 30000, exact=False
        )
        filename = f"{theme}-{viewport}-{index:02d}-{_safe_name(view_key)}.png"
        page.wait_for_timeout(250)
        cases.append(
            {
                "kind": "view",
                "theme": theme,
                "viewport": viewport,
                "view": view_key,
                "startup_ms": startup_ms,
                "browser_ready_ms": round(browser_ready_ms, 3),
                "server_build_ms": round(float(title.rsplit(":", 1)[1]), 3),
                "screenshot": _capture_case(page, output_dir, filename),
                "needs_visual_review": True,
            }
        )
        _ack_capture(runtime_root, f"{theme}-{viewport}-view-{index}")

    for dialog in ("command_palette", "command_palette_reopen", "snackbar"):
        wait_for_title(page, f"SMOKE:DIALOG:{dialog}", 30000, exact=True)
        page.wait_for_timeout(250)
        filename = f"{theme}-{viewport}-dialog-{dialog}.png"
        cases.append(
            {
                "kind": "dialog",
                "theme": theme,
                "viewport": viewport,
                "view": dialog,
                "screenshot": _capture_case(page, output_dir, filename),
                "needs_visual_review": True,
            }
        )
        _ack_capture(runtime_root, f"{theme}-{viewport}-dialog-{dialog}")

    for index, view_key in enumerate(VIEW_KEYS):
        title = wait_for_title(
            page, f"SMOKE:REVISIT:{index}:{view_key}:", 30000, exact=False
        )
        page.wait_for_timeout(100)
        filename = f"{theme}-{viewport}-revisit-{index:02d}-{_safe_name(view_key)}.png"
        cases.append(
            {
                "kind": "view_revisit",
                "theme": theme,
                "viewport": viewport,
                "view": view_key,
                "navigate_ms": round(float(title.rsplit(":", 1)[1]), 3),
                "screenshot": _capture_case(page, output_dir, filename),
                "needs_visual_review": True,
            }
        )
        _ack_capture(runtime_root, f"{theme}-{viewport}-view_revisit-{index}")
    wait_for_title(page, "SMOKE:LIFECYCLE:DISPOSED", 10000, exact=True)
    behavior_checks.append(
        {
            "scenario": "views",
            "check": "dispose_then_late_task_event",
            "result": "passed",
        }
    )
    wait_for_title(page, "SMOKE:DONE", 30000, exact=True)


def _run_dialog_scenario(
    page: Page,
    output_dir: Path,
    runtime_root: Path,
    cases: list[dict],
    behavior_checks: list[dict],
    *,
    theme: str,
    viewport: str,
) -> None:
    """擷取正式 workflow Dialog，並實際捲動 Merge body 到 Patchouli 設定。

    Args:
        page: 驅動 Flet smoke app 的 Playwright 頁面。
        output_dir: 截圖輸出目錄。
        runtime_root: 一次性 smoke app 狀態與 ACK 目錄。
        cases: 收集各 Dialog 截圖案例的報告清單。
        behavior_checks: 收集頁面生命週期驗收結果的報告清單。
        theme: 目前套用的色彩主題。
        viewport: WIDTHxHEIGHT 格式的視窗尺寸。
    """
    for dialog_key in DIALOG_SMOKE_KEYS:
        wait_for_title(page, f"SMOKE:DIALOG:{dialog_key}:OPEN", 30000, exact=True)
        page.wait_for_timeout(250)
        filename = f"{theme}-{viewport}-dialog-{_safe_name(dialog_key)}.png"
        cases.append(
            {
                "kind": "dialog_gallery",
                "theme": theme,
                "viewport": viewport,
                "view": dialog_key,
                "screenshot": _capture_case(page, output_dir, filename),
                "needs_visual_review": True,
            }
        )
        scroll_case_key = DIALOG_SCROLL_CASES.get(dialog_key)
        if scroll_case_key:
            viewport_width, viewport_height = map(int, viewport.split("x"))
            # 在 body 中央送出真實 wheel event，讓截圖驗證 scroll owner 而非靜態結構。
            page.mouse.move(viewport_width // 2, viewport_height // 2)
            page.mouse.wheel(0, viewport_height * 2)
            page.wait_for_timeout(250)
            scroll_filename = f"{theme}-{viewport}-dialog-{scroll_case_key}.png"
            cases.append(
                {
                    "kind": "dialog_scroll",
                    "theme": theme,
                    "viewport": viewport,
                    "view": scroll_case_key,
                    "screenshot": _capture_case(page, output_dir, scroll_filename),
                    "needs_visual_review": True,
                }
            )
        _ack_capture(runtime_root, f"{theme}-{viewport}-dialog_gallery-{dialog_key}")
    wait_for_title(page, "SMOKE:LIFECYCLE:DISPOSED", 10000, exact=True)
    behavior_checks.append(
        {
            "scenario": "dialogs",
            "check": "dispose_then_late_task_event",
            "result": "passed",
        }
    )
    wait_for_title(page, "SMOKE:DIALOGS:DONE", 30000, exact=True)


def _run_state_scenario(
    page: Page,
    output_dir: Path,
    runtime_root: Path,
    cases: list[dict],
    behavior_checks: list[dict],
    *,
    scenario: str,
    theme: str,
    viewport: str,
) -> None:
    """擷取單一確定性工作台狀態案例。"""
    wait_for_title(page, f"SMOKE:STATE:{scenario}", 30000, exact=True)
    page.wait_for_timeout(250)
    filename = f"{theme}-{viewport}-state-{_safe_name(scenario)}.png"
    cases.append(
        {
            "kind": "state",
            "theme": theme,
            "viewport": viewport,
            "view": scenario,
            "screenshot": _capture_case(page, output_dir, filename),
            "needs_visual_review": True,
        }
    )
    _ack_capture(runtime_root, f"{theme}-{viewport}-state-{scenario}")
    wait_for_title(page, "SMOKE:LIFECYCLE:DISPOSED", 10000, exact=True)
    behavior_checks.append(
        {
            "scenario": scenario,
            "check": "dispose_then_late_task_event",
            "result": "passed",
        }
    )
    wait_for_title(page, "SMOKE:STATE:DONE", 30000, exact=True)


def run_smoke(
    *,
    output_dir: Path,
    browser_executable: Path,
    viewports: tuple[tuple[int, int], ...] = DEFAULT_VIEWPORTS,
    themes: tuple[str, ...] = DEFAULT_THEMES,
    interval: float = 2.5,
    port: int = 0,
    scenario: str = "views",
) -> dict:
    """執行單一可重現情境，並驗證報告涵蓋全部預期截圖案例。

    Args:
        output_dir: 截圖與 report.json 的輸出目錄。
        browser_executable: 已安裝的 Chrome / Edge 執行檔。
        viewports: 需要驗收的視窗尺寸。
        themes: 需要驗收的主題。
        interval: smoke app 切換操作間的等待秒數。
        port: 優先使用的本機連接埠；0 代表選擇可用連接埠。
        scenario: views、dialogs 或單一工作台狀態案例。

    Returns:
        含環境、完整案例清單、錯誤與缺漏項目的報告。
    """
    if scenario not in SMOKE_SCENARIOS:
        raise ValueError(f"未知 UI smoke scenario：{scenario}")
    output_dir.mkdir(parents=True, exist_ok=True)
    port = choose_port(port)
    server_log = output_dir / "server.log"
    viewport_names = [f"{w}x{h}" for w, h in viewports]
    report: dict = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "browser": str(browser_executable),
        "scenario": scenario,
        "scenario_note": SCENARIO_NOTES.get(scenario, ""),
        "port": port,
        "themes": list(themes),
        "viewports": viewport_names,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "flet": metadata.version("flet"),
            "playwright": metadata.version("playwright"),
            "git_commit": _git_commit(),
            "git_worktree_dirty": _git_dirty(),
        },
        "cases": [],
        "behavior_checks": [],
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
        env["MINECRAFT_TRANSLATOR_SMOKE_SCENARIO"] = scenario
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
                    report["environment"]["browser_version"] = browser.version
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
                                start_view = (
                                    "pipeline" if scenario == "dialogs" else "dashboard"
                                )
                                url = (
                                    f"{base_url}/?theme={theme}&view={start_view}"
                                    f"&interval={interval}&scenario={scenario}"
                                    f"&viewport={width}x{height}"
                                )
                                started = time.perf_counter()
                                page.goto(
                                    url, wait_until="domcontentloaded", timeout=45000
                                )
                                ready_title = wait_for_title(
                                    page, f"SMOKE:READY:{theme}:", 45000, exact=False
                                )
                                applied_theme = ready_title.split(":", 3)[2]
                                if applied_theme != theme:
                                    raise RuntimeError(
                                        f"主題未套用：requested={theme}, applied={applied_theme}"
                                    )
                                browser_ready_ms = (
                                    time.perf_counter() - started
                                ) * 1000
                                startup_ms = float(ready_title.rsplit(":", 1)[1])
                                viewport = f"{width}x{height}"
                                if scenario == "views":
                                    _run_view_scenario(
                                        page,
                                        output_dir,
                                        runtime_root,
                                        report["cases"],
                                        report["behavior_checks"],
                                        theme=theme,
                                        viewport=viewport,
                                        startup_ms=startup_ms,
                                        browser_ready_ms=browser_ready_ms,
                                    )
                                elif scenario == "dialogs":
                                    _run_dialog_scenario(
                                        page,
                                        output_dir,
                                        runtime_root,
                                        report["cases"],
                                        report["behavior_checks"],
                                        theme=theme,
                                        viewport=viewport,
                                    )
                                else:
                                    _run_state_scenario(
                                        page,
                                        output_dir,
                                        runtime_root,
                                        report["cases"],
                                        report["behavior_checks"],
                                        scenario=scenario,
                                        theme=theme,
                                        viewport=viewport,
                                    )
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

    expected_cases = _expected_case_keys(scenario, themes, viewports)
    actual_case_keys = [
        (
            case["kind"],
            case["theme"],
            case["viewport"],
            case["view"],
        )
        for case in report["cases"]
    ]
    case_counts = Counter(actual_case_keys)
    actual_cases = set(actual_case_keys)
    report["duplicate_cases"] = [
        [*case, count] for case, count in sorted(case_counts.items()) if count > 1
    ]
    report["missing_cases"] = [
        list(case) for case in sorted(expected_cases - actual_cases)
    ]
    report["unexpected_cases"] = [
        list(case) for case in sorted(actual_cases - expected_cases)
    ]
    report["artifact_errors"] = [
        case["screenshot"]
        for case in report["cases"]
        if not (output_dir / case["screenshot"]).is_file()
        or (output_dir / case["screenshot"]).stat().st_size == 0
    ]
    report["visual_review_required"] = any(
        case.get("needs_visual_review", False) for case in report["cases"]
    )
    report["screenshot_count"] = len(report["cases"])
    report["expected_case_count"] = len(expected_cases)
    report["behavior_check_errors"] = []
    if len(report["behavior_checks"]) != len(themes) * len(viewports):
        report["behavior_check_errors"].append("lifecycle check matrix incomplete")
    report["ok"] = not any(
        (
            report["console_errors"],
            report["page_errors"],
            report["missing_cases"],
            report["unexpected_cases"],
            report["duplicate_cases"],
            report["artifact_errors"],
            report["behavior_check_errors"],
        )
    )
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    """命令列進入點。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--browser-executable")
    parser.add_argument("--viewport", action="append")
    parser.add_argument("--theme", action="append", choices=DEFAULT_THEMES)
    parser.add_argument("--scenario", choices=SMOKE_SCENARIOS, default="views")
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
            scenario=args.scenario,
        )
    except Exception as exc:  # noqa: BLE001 - CLI 邊界，回報可處理的失敗
        print(f"UI smoke failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
