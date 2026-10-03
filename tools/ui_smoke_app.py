"""視覺 smoke harness 使用的確定性 Flet 應用程式。

本模組提供真實的 AppShell 與真實的惰性建構頁面，但會把 runtime 資料
導向可拋棄的目錄，並注入固定的環境與任務資料。它不是第二個產品進入點。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
import traceback
from pathlib import Path

import flet as ft

SOURCE_ROOT = Path(__file__).resolve().parents[1]
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
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _configure_runtime_root() -> Path:
    """把會被修改的專案相對路徑資料，導向 harness 的 runtime 目錄。"""
    runtime_root = Path(
        os.environ.get("MINECRAFT_TRANSLATOR_SMOKE_ROOT", SOURCE_ROOT / ".artifacts")
    ).resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)

    from translation_tool.utils import config_manager

    config_manager.PROJECT_ROOT = runtime_root
    config_manager.CONFIG_PATH = runtime_root / "config.json"
    config_manager.clear_config_cache()

    from app.services_impl import config_service

    config_service.PROJECT_ROOT = runtime_root
    config_service.CONFIG_PATH = str(runtime_root / "config.json")
    config_service.REPLACE_RULES_PATH = str(runtime_root / "replace_rules.json")
    return runtime_root


def _fixed_config() -> dict:
    """回傳供 UI 渲染使用、穩定且離線的資料。"""
    from translation_tool.utils.config_manager import DEFAULT_CONFIG

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["translator"]["cache_directory"] = "cache"
    config["translator"]["replace_rules_path"] = "replace_rules.json"
    config["species_cache"]["cache_directory"] = "species"
    config["logging"]["log_dir"] = "logs"
    config["lm_translator"]["keys"] = ["SMOKE_FAKE_KEY"]
    return config


def _query(page: ft.Page, key: str, default: str) -> str:
    """讀取單一 query-string 值，缺少時使用穩定的預設值。"""
    try:
        query = page.query
        query()  # Flet 設定 initial route 時未必先送 RouteChangeEvent。
        value = query.get(key)
    except (AttributeError, KeyError, TypeError):
        return default
    return str(value) if value not in (None, "") else default


def _view_content(shell, view_key: str) -> ft.Control:
    """取得已建立 View 的內層控制項，供 smoke 情境呼叫正式入口。"""
    item = next(item for item in shell.registry if item["key"] == view_key)
    view = item["view"]
    return getattr(view, "content", view)


def _prepare_scenario_data(scenario: str) -> None:
    """在隔離 runtime root 內建立空或固定 Dashboard 測試資料。"""
    from app.services_impl.config_service import save_replace_rules

    if scenario in {"empty", "loading"}:
        save_replace_rules([])
    elif scenario == "data":
        save_replace_rules(
            [
                {"from": "fixture.source.one", "to": "固定譯文一"},
                {"from": "fixture.source.two", "to": "固定譯文二"},
            ]
        )
        from translation_tool.utils.cache_manager import add_to_cache

        add_to_cache("lang", "fixture.first", "First", "第一筆")
        add_to_cache("lang", "fixture.second", "Second", "第二筆")


async def _wait_for_capture_ack(
    runtime_root: Path,
    token: str,
    *,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.05,
) -> None:
    """等待 Playwright 擷取完成通知，避免 app 超前覆寫畫面狀態。"""
    marker = runtime_root / ".smoke-acks" / f"{token}.done"
    deadline = time.monotonic() + timeout_seconds
    while not marker.is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待 Playwright 截圖確認逾時：{token}")
        await asyncio.sleep(poll_interval)
    marker.unlink()


def _dispose_and_probe_late_task(page: ft.Page, shell) -> None:
    """拆除外殼訂閱後送出晚到事件，確認不再排程失效 UI 更新。"""
    from app.tasks.task_session import TaskSession

    shell.dispose()
    late_session = TaskSession(name="teardown probe", view_key="pipeline")
    late_session.start()
    late_session.set_progress(0.5)
    late_session.finish()
    page.title = "SMOKE:LIFECYCLE:DISPOSED"
    page.update()


async def _run_view_sequence(
    page: ft.Page,
    shell,
    interval: float,
    runtime_root: Path,
    theme: str,
    viewport: str,
) -> None:
    """真實 event loop 下切換 13 頁、回訪頁面並完成任務。"""
    from app.tasks.task_session import TaskSession
    from app.ui.snack import show_snack
    from app.view_registry import VIEW_SPECS

    await asyncio.sleep(0.8)
    session = TaskSession(name="視覺驗收模擬任務", view_key="pipeline")
    session.start()
    total = len(VIEW_SPECS)

    for index, spec in enumerate(VIEW_SPECS):
        started = time.perf_counter()
        shell.navigate(spec.key)
        build_ms = (time.perf_counter() - started) * 1000
        session.set_progress((index + 1) / (total + 2))
        session.add_log(f"已切換至 {spec.label}", source="ui-smoke")
        page.title = f"SMOKE:VIEW:{index}:{spec.key}:{build_ms:.3f}"
        page.update()
        await asyncio.sleep(interval)
        await _wait_for_capture_ack(runtime_root, f"{theme}-{viewport}-view-{index}")

    shell.open_palette()
    page.title = "SMOKE:DIALOG:command_palette"
    page.update()
    await asyncio.sleep(interval)
    await _wait_for_capture_ack(
        runtime_root, f"{theme}-{viewport}-dialog-command_palette"
    )
    shell.close_palette()

    shell.open_palette()
    page.title = "SMOKE:DIALOG:command_palette_reopen"
    page.update()
    await asyncio.sleep(interval)
    await _wait_for_capture_ack(
        runtime_root, f"{theme}-{viewport}-dialog-command_palette_reopen"
    )
    shell.close_palette()

    show_snack(page, "視覺驗收固定 Snackbar", duration=10000)
    page.title = "SMOKE:DIALOG:snackbar"
    page.update()
    await asyncio.sleep(interval)
    await _wait_for_capture_ack(runtime_root, f"{theme}-{viewport}-dialog-snackbar")

    for index, spec in enumerate(VIEW_SPECS):
        started = time.perf_counter()
        shell.navigate(spec.key)
        navigate_ms = (time.perf_counter() - started) * 1000
        page.title = f"SMOKE:REVISIT:{index}:{spec.key}:{navigate_ms:.3f}"
        page.update()
        await asyncio.sleep(max(interval, 0.8))
        await _wait_for_capture_ack(
            runtime_root, f"{theme}-{viewport}-view_revisit-{index}"
        )

    session.finish()
    shell.refresh_tasks()
    shell.navigate("pipeline")
    _dispose_and_probe_late_task(page, shell)
    await asyncio.sleep(0.2)
    page.title = "SMOKE:DONE"
    page.update()


def _dismiss_top_dialog(page: ft.Page) -> None:
    """關閉目前 smoke gallery 顯示的正式 Dialog。"""
    if page.pop_dialog() is not None:
        return
    for control in reversed(page.overlay):
        if isinstance(control, ft.AlertDialog):
            control.open = False
            page.overlay.remove(control)
            break
    page.update()


async def _run_dialog_sequence(
    page: ft.Page, shell, interval: float, runtime_root: Path, theme: str, viewport: str
) -> None:
    """透過正式 workflow entry points 逐一展示主要 Dialog。"""
    from app.views.extractor.extractor_dialog import (
        open_extractor_dialog,
        open_preview_dialog,
    )

    pipeline = _view_content(shell, "pipeline")
    input_root = runtime_root / "fixture-input"
    output_root = runtime_root / "fixture-output"
    input_root.mkdir(exist_ok=True)
    output_root.mkdir(exist_ok=True)
    pipeline.input_path_text.value = str(input_root)
    pipeline.output_path_text.value = str(output_root)

    def show_merge_summary() -> None:
        """掛載合併頁後，以固定的成功／失敗資料開啟正式摘要 Dialog。"""
        shell.navigate("merge")
        _view_content(shell, "merge")._show_merge_summary(
            {
                "success_folders": 2,
                "failed_folders": 1,
                "failed_folders_list": [
                    {"name": "fixture-mod", "error": "固定 smoke 錯誤"}
                ],
                "output_counts": {"lang_output": 3},
            }
        )

    actions = (
        ("pipeline_extract", pipeline._on_extract_click),
        ("pipeline_extract_reopen", pipeline._on_extract_click),
        ("pipeline_merge", pipeline._on_merge_click),
        ("pipeline_translate", pipeline._on_translate_click),
        ("pipeline_bundle", pipeline._on_bundle_click),
        ("pipeline_one_click", pipeline._on_one_click_click),
        (
            "extractor_run",
            lambda: open_extractor_dialog(
                page,
                shell.file_picker,
                input_path=str(input_root),
                output_path=str(output_root),
            ),
        ),
        (
            "extractor_preview",
            lambda: open_preview_dialog(
                page,
                shell.file_picker,
                input_path=str(input_root),
                output_path=str(output_root),
            ),
        ),
        ("merge_summary", show_merge_summary),
    )

    for dialog_key, open_dialog in actions:
        page.title = f"SMOKE:DIALOG:{dialog_key}:OPENING"
        page.update()
        try:
            open_dialog()
        except Exception:  # noqa: BLE001 - smoke 邊界須記錄正式 Dialog 建構失敗
            traceback.print_exc()
            shell.dispose()
            page.title = f"SMOKE:ERROR:{dialog_key}"
            page.update()
            return
        page.title = f"SMOKE:DIALOG:{dialog_key}:OPEN"
        page.update()
        await asyncio.sleep(interval)
        await _wait_for_capture_ack(
            runtime_root, f"{theme}-{viewport}-dialog_gallery-{dialog_key}"
        )
        _dismiss_top_dialog(page)
        await asyncio.sleep(0.15)

    _dispose_and_probe_late_task(page, shell)
    await asyncio.sleep(0.2)
    page.title = "SMOKE:DIALOGS:DONE"
    page.update()


async def _run_state_scenario(
    page: ft.Page,
    shell,
    scenario: str,
    runtime_root: Path,
    theme: str,
    viewport: str,
) -> None:
    """在正式 Dashboard 上呈現固定的空、資料、任務或載入狀態。"""
    from app.tasks.task_session import TaskSession

    dashboard = _view_content(shell, "dashboard")
    dashboard.reload(sync=True)
    session = None
    if scenario in {"running", "error", "cancelled", "data"}:
        session = TaskSession(name="固定驗收任務", view_key="pipeline")
        session.start()
        session.set_progress(0.42)
        session.add_log("固定驗收日誌", source="ui-smoke")
        if scenario == "error":
            session.set_error()
            session.finish()
        elif scenario == "cancelled":
            # TaskSession 目前只有取消請求旗標，沒有獨立 CANCELLED 狀態。
            session.request_cancel()
            session.add_log("已送出取消要求，等待 worker 停止", level="warning")
        elif scenario == "data":
            session.finish()

    if scenario == "loading":
        dashboard._cache_overview = None
        dashboard._rules_count = None
        dashboard.refresh_view(dashboard._collect())

    page.title = f"SMOKE:STATE:{scenario}"
    page.update()
    await _wait_for_capture_ack(runtime_root, f"{theme}-{viewport}-state-{scenario}")
    if session is not None and scenario in {"running", "cancelled"}:
        session.finish()
    _dispose_and_probe_late_task(page, shell)
    await asyncio.sleep(0.2)
    page.title = "SMOKE:STATE:DONE"
    page.update()


async def _run_sequence(
    page: ft.Page,
    shell,
    interval: float,
    scenario: str,
    runtime_root: Path,
    theme: str,
    viewport: str,
) -> None:
    """依所選驗收情境驅動正式外殼，結束時檢查 teardown。"""
    if scenario == "views":
        await _run_view_sequence(page, shell, interval, runtime_root, theme, viewport)
    elif scenario == "dialogs":
        await asyncio.sleep(0.8)
        await _run_dialog_sequence(page, shell, interval, runtime_root, theme, viewport)
    else:
        await asyncio.sleep(0.8)
        await _run_state_scenario(page, shell, scenario, runtime_root, theme, viewport)


def main(page: ft.Page) -> None:
    """以隔離的確定性測試資料掛載正式環境的外殼。"""
    runtime_root = _configure_runtime_root()
    scenario = os.environ.get("MINECRAFT_TRANSLATOR_SMOKE_SCENARIO") or _query(
        page, "scenario", "views"
    )
    if scenario not in SMOKE_SCENARIOS:
        raise ValueError(f"未知 UI smoke scenario：{scenario}")
    config = _fixed_config()
    (runtime_root / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _prepare_scenario_data(scenario)

    from app.shell import AppShell
    from app.shell.task_manager import TaskManager
    from app.ui import design
    from app.view_registry import DEFAULT_VIEW_KEY

    mode = _query(page, "theme", "dark")
    mode = mode if mode in {"dark", "light"} else "dark"
    start_view = (
        "pipeline" if scenario == "dialogs" else _query(page, "view", DEFAULT_VIEW_KEY)
    )
    try:
        interval = max(0.5, float(_query(page, "interval", "2.5")))
    except ValueError:
        interval = 2.5

    page.fonts = {
        design.FONT_SANS: "fonts/NotoSansTC-Variable.ttf",
        design.FONT_MONO: "fonts/JetBrainsMono-Regular.ttf",
    }
    shell = AppShell(
        page,
        key_snapshot=list,
        config_loader=lambda: copy.deepcopy(config),
        task_manager=TaskManager(),
        initial_mode=mode,
        mode_saver=lambda _mode: True,
        subscribe_config=lambda _callback: lambda: None,
    )
    started = time.perf_counter()
    shell.mount(start_view)
    startup_ms = (time.perf_counter() - started) * 1000
    page.title = f"SMOKE:READY:{mode}:{start_view}:{startup_ms:.3f}"
    page.update()
    viewport = _query(page, "viewport", "unknown")
    page.run_task(
        _run_sequence, page, shell, interval, scenario, runtime_root, mode, viewport
    )


def _serve() -> None:
    """以 ASGI 方式提供 smoke 應用程式，不另外開啟不受管理的瀏覽器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    import uvicorn

    app = ft.run(
        main,
        export_asgi_app=True,
        assets_dir=str(SOURCE_ROOT / "assets"),
        web_renderer=ft.WebRenderer.CANVAS_KIT,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    _serve()
