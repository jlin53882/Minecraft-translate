"""Deterministic Flet app used by the visual smoke harness.

The module serves the real AppShell and real lazily-built views, but redirects
runtime data to a disposable directory and injects fixed environment/task data.
It is not a second product entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path

import flet as ft

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _configure_runtime_root() -> Path:
    """Redirect mutable project-relative data to the harness runtime directory."""
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
    """Return stable offline data for UI rendering."""
    from translation_tool.utils.config_manager import DEFAULT_CONFIG

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["translator"]["cache_directory"] = "cache"
    config["translator"]["replace_rules_path"] = "replace_rules.json"
    config["species_cache"]["cache_directory"] = "species"
    config["logging"]["log_dir"] = "logs"
    config["lm_translator"]["keys"] = ["SMOKE_FAKE_KEY"]
    return config


def _query(page: ft.Page, key: str, default: str) -> str:
    """Read one query-string value with a stable fallback."""
    try:
        value = page.query.get(key)
    except (AttributeError, KeyError, TypeError):
        return default
    return str(value) if value not in (None, "") else default


async def _run_sequence(page, shell, interval: float) -> None:
    """Cycle every real view while a deterministic task remains active."""
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

    shell.open_palette()
    page.title = "SMOKE:DIALOG:command_palette"
    page.update()
    await asyncio.sleep(interval)
    shell.close_palette()

    show_snack(page, "視覺驗收固定 Snackbar", duration=10000)
    page.title = "SMOKE:DIALOG:snackbar"
    page.update()
    await asyncio.sleep(interval)

    session.finish()
    shell.refresh_tasks()
    shell.navigate("pipeline")
    page.title = "SMOKE:DONE"
    page.update()


def main(page: ft.Page) -> None:
    """Mount the production shell with isolated deterministic fixture data."""
    runtime_root = _configure_runtime_root()
    config = _fixed_config()
    (runtime_root / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    from app.shell import AppShell
    from app.shell.task_manager import TaskManager
    from app.ui import design
    from app.view_registry import DEFAULT_VIEW_KEY

    mode = _query(page, "theme", "dark")
    mode = mode if mode in {"dark", "light"} else "dark"
    start_view = _query(page, "view", DEFAULT_VIEW_KEY)
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
    page.title = f"SMOKE:READY:{start_view}:{startup_ms:.3f}"
    page.update()
    page.run_task(_run_sequence, page, shell, interval)


def _serve() -> None:
    """Serve the smoke app as ASGI without opening an unmanaged browser."""
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
