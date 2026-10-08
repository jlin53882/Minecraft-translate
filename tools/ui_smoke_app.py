"""視覺 smoke harness 使用的確定性 Flet 應用程式。

本模組提供真實的 AppShell 與真實的惰性建構頁面，但會把 runtime 資料
導向可拋棄的目錄，並注入固定的環境與任務資料。它不是第二個產品進入點。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import inspect
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
    "dialog-click-probe",
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


def _install_smoke_dialog_tracking(page: ft.Page) -> list[ft.DialogControl]:
    """Track Flet native dialogs via the public show_dialog boundary (smoke only)."""
    dialogs: list[ft.DialogControl] = []
    show_dialog = page.show_dialog

    def tracked_show_dialog(dialog: ft.DialogControl) -> None:
        show_dialog(dialog)
        dialogs.append(dialog)

    page.show_dialog = tracked_show_dialog
    return dialogs


def _top_open_smoke_dialog(page: ft.Page, native_dialogs) -> ft.DialogControl | None:
    """Return the newest open smoke dialog, whether native or overlay based."""
    for dialog in reversed(native_dialogs):
        if dialog.open:
            return dialog
    return next(
        (
            control
            for control in reversed(page.overlay)
            if isinstance(control, ft.DialogControl) and control.open
        ),
        None,
    )


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
    while True:
        try:
            marker.unlink()
            return
        except FileNotFoundError:
            # 允許一次性 marker 被其他 cleanup 路徑先消費。
            return
        except PermissionError:
            # Windows 可能在檔案剛寫完時短暫保留 handle；bounded retry
            # 保持 OPEN -> capture -> ACK -> advance 順序，又不會無限等待。
            if time.monotonic() >= deadline:
                raise TimeoutError(f"清理 Playwright 截圖確認逾時：{token}")
            await asyncio.sleep(poll_interval)


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


def _dismiss_top_dialog(page: ft.Page, native_dialogs) -> None:
    """只關閉本次 smoke 最上層 Dialog，保留 overlay 等待反向動畫。

    Args:
        page: 持有 Flet Dialog stack 與 overlay 的 smoke Page。

    Side Effects:
        關閉一個 smoke 追蹤的 native 或 overlay Dialog，並更新頁面。
    """
    dialog = _top_open_smoke_dialog(page, native_dialogs)
    if dialog is not None:
        dialog.open = False
        dialog.update()
    page.update()


def _remove_closed_overlay_dialogs(page: ft.Page) -> None:
    """在 Flutter reverse transition 後移除已關閉的 legacy overlay Dialog。

    Args:
        page: 持有 smoke Dialog overlay 的 Flet Page。

    Side Effects:
        移除已關閉的 AlertDialog 控件並更新頁面；仍開啟者會保留。
    """
    closed_dialogs = tuple(
        control
        for control in page.overlay
        if isinstance(control, ft.AlertDialog) and not control.open
    )
    for dialog in closed_dialogs:
        page.overlay.remove(dialog)
    if closed_dialogs:
        page.update()


async def _run_dialog_sequence(
    page: ft.Page,
    shell,
    interval: float,
    runtime_root: Path,
    theme: str,
    viewport: str,
    native_dialogs,
) -> None:
    """透過正式 workflow entry points 展示 Dialog，並重開 Pipeline Merge。"""
    from app.views.extractor.extractor_dialog import (
        open_extractor_dialog,
        open_preview_dialog,
    )

    pipeline = _view_content(shell, "pipeline")
    input_root = runtime_root / "fixture-input"
    output_root = runtime_root / "fixture-output"
    input_root.mkdir(exist_ok=True)
    output_root.mkdir(exist_ok=True)
    # The standalone translation and bundle dialogs derive their preview input
    # from the pipeline output root. Materialize those safe fixture directories
    # so the real click probe reaches the documented preview stub instead of
    # correctly stopping at the missing-input validation message.
    (
        output_root / "locale_sort" / "_整理輸出" / "lang_output" / "待翻譯整理需翻譯"
    ).mkdir(parents=True, exist_ok=True)
    (output_root / "lm_translate" / "_翻譯輸出").mkdir(parents=True, exist_ok=True)
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
        ("pipeline_merge_reopen", pipeline._on_merge_click),
        ("pipeline_translate", pipeline._on_translate_click),
        ("pipeline_bundle", pipeline._on_bundle_click),
        ("pipeline_one_click", pipeline._on_one_click_click),
        ("pipeline_one_click_reopen", pipeline._on_one_click_click),
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
        if dialog_key == "pipeline_one_click":
            for step in range(2, 5):
                current_dialog = _top_open_smoke_dialog(page, native_dialogs)
                if current_dialog is None:
                    raise RuntimeError("Wizard step transition 沒有 active dialog")
                # Step 1 actions are [下一個, 取消]; steps 2/3 are
                # [上一個, 下一個, 取消], so the penultimate action advances.
                current_dialog.actions[-2].on_click(None)
                await asyncio.sleep(interval)
                page.title = f"SMOKE:DIALOG:pipeline_one_click:STEP:{step}:OPEN"
                page.update()
                await _wait_for_capture_ack(
                    runtime_root,
                    f"{theme}-{viewport}-dialog_wizard-pipeline_one_click_step{step}",
                )
        _dismiss_top_dialog(page, native_dialogs)
        # 等 Flutter modal 的 reverse transition 完成，避免殘影混入下一張截圖。
        await asyncio.sleep(0.35)
        _remove_closed_overlay_dialogs(page)

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


async def _run_dialog_click_probe(
    page: ft.Page,
    shell,
    runtime_root: Path,
    theme: str,
    viewport: str,
    native_dialogs,
) -> None:
    """Drive real CanvasKit clicks through standalone dialogs and the wizard."""
    from app.ui.dialogs import close_page_dialog, dialog_dimensions
    from app.views.pipeline.pipeline_forms import dialog_field_width

    pipeline = _view_content(shell, "pipeline")
    input_root = runtime_root / "fixture-input"
    output_root = runtime_root / "fixture-output"
    input_root.mkdir(exist_ok=True)
    output_root.mkdir(exist_ok=True)
    (
        output_root / "locale_sort" / "_整理輸出" / "lang_output" / "待翻譯整理需翻譯"
    ).mkdir(parents=True, exist_ok=True)
    (output_root / "lm_translate" / "_翻譯輸出").mkdir(parents=True, exist_ok=True)
    pipeline.input_path_text.value = str(input_root)
    pipeline.output_path_text.value = str(output_root)
    standalone_launches: dict[str, list] = {}

    def top_dialog():
        return _top_open_smoke_dialog(page, native_dialogs)

    def probe_texts(control):
        return [
            str(getattr(item, "value", ""))
            for item in _smoke_control_tree(control)
            if type(item).__name__ == "Text"
        ]

    async def gate(
        key: str, control, handler_attr="on_click", verify=None, observed=None
    ):
        original = getattr(control, handler_attr, None)

        def mark_done():
            passed = bool(verify()) if verify is not None else True
            result_title = f"SMOKE:CLICK:{key}:DONE:{'PASS' if passed else 'FAIL'}"
            if not passed:
                top = top_dialog()
                details = {
                    "open": getattr(observed, "open", None),
                    "texts": probe_texts(observed) if observed is not None else [],
                    "top_dialog_texts": probe_texts(top) if top is not None else [],
                    "launch_counts": {
                        name: len(calls) for name, calls in standalone_launches.items()
                    },
                }
                result_title += ":" + json.dumps(details, ensure_ascii=True)
                print(
                    "[UI_SMOKE] " + result_title,
                    flush=True,
                )
            page.title = result_title
            page.update()

        if inspect.iscoroutinefunction(original):

            async def observe(event):
                await original(event)
                mark_done()

        else:

            def observe(event):
                if original is not None:
                    original(event)
                mark_done()

        setattr(control, handler_attr, observe)
        page.title = f"SMOKE:CLICK:{key}:READY"
        page.update()
        await _wait_for_capture_ack(
            runtime_root, f"{theme}-{viewport}-click-probe-{key}"
        )

    async def capture_gate(key: str):
        page.title = f"SMOKE:CLICK:{key}:READY"
        page.update()
        await _wait_for_capture_ack(
            runtime_root, f"{theme}-{viewport}-click-probe-{key}"
        )

    async def raw_click_gate(key: str):
        """Synchronize a real control click; assert its effect at a later boundary."""
        page.title = f"SMOKE:CLICK:{key}:READY"
        page.update()
        await _wait_for_capture_ack(
            runtime_root, f"{theme}-{viewport}-click-probe-{key}"
        )

    def action(dialog, label: str):
        for button in dialog.actions or []:
            if _smoke_button_label(button) == label:
                return button
        raise RuntimeError(f"Dialog action not found: {label}")

    def text_field(dialog, label: str):
        for control in _smoke_control_tree(dialog.content):
            if (
                isinstance(control, ft.TextField)
                and getattr(control, "label", None) == label
            ):
                return control
        raise RuntimeError(f"Dialog field not found: {label}")

    def checkboxes(dialog):
        return [
            control
            for control in _smoke_control_tree(dialog.content)
            if isinstance(control, ft.Checkbox)
        ]

    def bundle_version_picker(dialog):
        controls = list(_smoke_control_tree(dialog.content))
        search = next(
            (
                control
                for control in controls
                if isinstance(control, ft.TextField)
                and getattr(control, "label", None) == "搜尋版本"
            ),
            None,
        )
        selection = next(
            (
                control
                for control in controls
                if isinstance(control, ft.Container)
                and isinstance(getattr(control, "content", None), ft.Row)
                and any(
                    isinstance(child, ft.Icon) and child.icon == ft.Icons.EXPAND_MORE
                    for child in control.content.controls
                )
            ),
            None,
        )
        list_container = next(
            (
                control
                for control in controls
                if isinstance(control, ft.Container)
                and getattr(control, "height", None) == 196
                and isinstance(getattr(control, "content", None), ft.ListView)
            ),
            None,
        )
        selected_label = (
            next(
                (
                    child
                    for child in _smoke_control_tree(selection)
                    if isinstance(child, ft.Text)
                    and child.value not in {"已選擇：", ""}
                ),
                None,
            )
            if selection is not None
            else None
        )
        if any(
            value is None
            for value in (search, selection, list_container, selected_label)
        ):
            raise RuntimeError("bundle dialog 搜尋版本選擇器結構不完整")
        return search, selection, selected_label, list_container

    def all_dialog_feedback(dialog, needle: str) -> bool:
        return dialog.open and any(needle in text for text in probe_texts(dialog))

    # The extraction cancel is the first browser click and its screenshot is
    # retained by the Playwright report.
    extraction_calls = []
    pipeline._run_extraction = lambda *args, **kwargs: extraction_calls.append(
        (args, kwargs)
    )
    pipeline._on_extract_click()
    parent = top_dialog()
    if parent is None or not parent.actions:
        raise RuntimeError("pipeline_extract action probe 找不到 open Dialog")
    await gate(
        "extract-cancel",
        action(parent, "取消"),
        verify=lambda: not parent.open and top_dialog() is None,
    )

    # Invalid preview must keep the parent open and expose actionable inline feedback.
    pipeline._on_extract_click()
    parent = top_dialog()
    input_field = text_field(parent, "Mod 來源")
    input_field.value = ""
    page.update()
    await gate(
        "extract-invalid-preview",
        action(parent, "預覽結果"),
        verify=lambda: all_dialog_feedback(parent, "有效的 Mod 來源"),
    )

    # Valid preview uses a real empty fixture directory: scan completes without
    # touching user files, and the nested result modal must leave its parent intact.
    input_field.value = str(input_root)
    page.update()
    await gate(
        "extract-valid-preview",
        action(parent, "預覽結果"),
        verify=lambda: top_dialog() is not parent and bool(top_dialog().open),
    )
    child = top_dialog()
    deadline = asyncio.get_running_loop().time() + 15
    while asyncio.get_running_loop().time() < deadline:
        if child.actions and _smoke_button_label(child.actions[0]) == "確定":
            break
        await asyncio.sleep(0.05)
    else:
        raise TimeoutError("extract preview worker did not publish its result")
    await capture_gate("extract-preview-result")
    close_page_dialog(page, child)
    await asyncio.sleep(0.35)
    if top_dialog() is not parent or not parent.open:
        raise RuntimeError("closing the nested preview changed the parent dialog")

    # Invalid then valid confirm clicks prove validation is visible and callback is once-only.
    language_checks = checkboxes(parent)
    if not language_checks:
        raise RuntimeError("extract dialog has no language checkboxes")
    for checkbox in language_checks:
        checkbox.value = False
    page.update()
    run_button = action(parent, "確定執行")
    await gate(
        "extract-invalid-confirm",
        run_button,
        verify=lambda: (
            all_dialog_feedback(parent, "至少選擇一個語言") and not extraction_calls
        ),
    )
    language_checks[0].value = True
    page.update()
    await gate(
        "extract-valid-confirm",
        run_button,
        verify=lambda: not parent.open and len(extraction_calls) == 1,
    )

    standalone_launches.update({"merge": [], "translate": [], "bundle": []})
    pipeline._run_merge = lambda *args, **kwargs: standalone_launches["merge"].append(
        (args, kwargs)
    )
    pipeline._run_translate = lambda *args, **kwargs: standalone_launches[
        "translate"
    ].append((args, kwargs))
    pipeline._run_bundle = lambda *args, **kwargs: standalone_launches["bundle"].append(
        (args, kwargs)
    )

    # Remaining standalone dialogs: preview, invalid confirmation, successful
    # confirmation, and cancel are real clicks. Run callbacks are intercepted so
    # this UI probe never launches long-running pipeline work.
    for key, open_dialog in (
        ("merge", pipeline._on_merge_click),
        ("translate", pipeline._on_translate_click),
        ("bundle", pipeline._on_bundle_click),
    ):
        open_dialog()
        current = top_dialog()
        if current is None:
            raise RuntimeError(f"pipeline_{key} did not open")
        if key == "bundle":
            search, selection, selected_label, list_container = bundle_version_picker(
                current
            )
            await gate(
                "bundle-version-expand",
                selection,
                verify=lambda list_container=list_container: bool(
                    list_container.visible
                ),
                observed=current,
            )
            await raw_click_gate("bundle-version-wheel")
            await raw_click_gate("bundle-version-search")
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline:
                version_labels = [
                    item.content.value
                    for item in list_container.content.controls
                    if isinstance(item, ft.Container)
                    and isinstance(getattr(item, "content", None), ft.Text)
                ]
                if (
                    (search.value or "").strip() == "1.20"
                    and version_labels
                    and all("1.20" in value for value in version_labels)
                ):
                    break
                await asyncio.sleep(0.05)
            filtered = (
                (search.value or "").strip() == "1.20"
                and bool(version_labels)
                and all("1.20" in value for value in version_labels)
            )
            page.title = (
                "SMOKE:CLICK:bundle-version-search:DONE:PASS"
                if filtered
                else "SMOKE:CLICK:bundle-version-search:DONE:FAIL"
            )
            page.update()
            await _wait_for_capture_ack(
                runtime_root,
                f"{theme}-{viewport}-click-probe-bundle-version-search-verified",
            )
            if not filtered:
                raise RuntimeError(
                    "bundle version picker search did not filter to 1.20 entries"
                )
            if not list_container.content.controls:
                raise RuntimeError("bundle version picker search returned no item")
            first_version = list_container.content.controls[0]
            expected_version = first_version.content.value
            await gate(
                "bundle-version-select",
                first_version,
                verify=lambda selected_label=selected_label, expected_version=expected_version, list_container=list_container: (
                    selected_label.value == expected_version
                    and not list_container.visible
                ),
                observed=current,
            )

            bundle_input = text_field(current, "輸入來源")
            for resize_key, expected_viewport in (
                ("bundle-resize-narrow", 390),
                ("bundle-resize-restore", 1360),
            ):
                await raw_click_gate(resize_key)
                deadline = asyncio.get_running_loop().time() + 8
                resized = False
                while asyncio.get_running_loop().time() < deadline:
                    expected_width, expected_height = dialog_dimensions(page)
                    expected_field_width = dialog_field_width(page)
                    if (
                        abs(int(page.width or 0) - expected_viewport) <= 4
                        and current.content.width == expected_width
                        and current.content.height == expected_height
                        and bundle_input.width == expected_field_width
                    ):
                        resized = True
                        break
                    await asyncio.sleep(0.05)
                page.title = (
                    f"SMOKE:CLICK:{resize_key}:DONE:PASS"
                    if resized
                    else f"SMOKE:CLICK:{resize_key}:DONE:FAIL"
                )
                page.update()
                await _wait_for_capture_ack(
                    runtime_root,
                    f"{theme}-{viewport}-click-probe-{resize_key}-verified",
                )
                if not resized:
                    raise RuntimeError(
                        f"open bundle dialog did not resize for viewport {expected_viewport}"
                    )
        await gate(
            f"{key}-preview",
            action(current, "預覽結果"),
            verify=lambda current=current: (
                current.open
                and any(
                    marker in text
                    for text in probe_texts(current)
                    for marker in ("尚未支援", "不存在")
                )
            ),
            observed=current,
        )
        await gate(
            f"{key}-cancel",
            action(current, "取消"),
            verify=lambda current=current: not current.open and top_dialog() is None,
        )
        open_dialog()
        current = top_dialog()
        if current is None:
            raise RuntimeError(f"pipeline_{key} did not reopen after cancel")
        input_label = {
            "merge": "Mod 來源",
            "translate": "翻譯目標",
            "bundle": "輸入來源",
        }[key]
        input_control = text_field(current, input_label)
        original_input = input_control.value
        input_control.value = str(runtime_root / f"missing-{key}-input")
        page.update()
        await gate(
            f"{key}-invalid-confirm",
            action(current, "確定執行"),
            verify=lambda current=current, key=key: (
                current.open
                and any("不存在" in text for text in probe_texts(current))
                and not standalone_launches[key]
            ),
            observed=current,
        )
        input_control.value = original_input
        page.update()
        await gate(
            f"{key}-valid-confirm",
            action(current, "確定執行"),
            verify=lambda current=current, key=key: (
                not current.open
                and top_dialog() is None
                and len(standalone_launches[key]) == 1
            ),
            observed=current,
        )

    # Wizard cancellation/reopen, all four steps, invalid confirmation, back
    # navigation, then a single valid callback. The runner is intercepted only
    # after PipelineView validation so this scenario never launches real work.
    wizard_execution_configs = []
    original_one_click_execute = pipeline._on_one_click_execute

    def capture_one_click_execute(config):
        wizard_execution_configs.append(copy.deepcopy(config))
        return original_one_click_execute(config)

    pipeline._on_one_click_execute = capture_one_click_execute
    pipeline._on_one_click_click()
    wizard = top_dialog()
    page.title = "SMOKE:CLICK:wizard-step1-open:READY"
    page.update()
    await _wait_for_capture_ack(
        runtime_root, f"{theme}-{viewport}-click-probe-wizard-step1-open"
    )
    if wizard is None or "1/4" not in probe_texts(wizard):
        raise RuntimeError(
            "one-click wizard did not become the top dialog: "
            f"top={type(wizard).__name__ if wizard else None}, "
            f"texts={probe_texts(wizard) if wizard else []}, "
            f"native_open={[probe_texts(item) for item in native_dialogs if item.open]}"
        )
    await gate(
        "wizard-cancel-step1",
        action(wizard, "取消"),
        verify=lambda: top_dialog() is None,
    )
    pipeline._on_one_click_click()
    wizard = top_dialog()
    if wizard is None or "1/4" not in probe_texts(wizard):
        raise RuntimeError(
            "reopened one-click wizard did not become the top dialog: "
            f"top={type(wizard).__name__ if wizard else None}, "
            f"texts={probe_texts(wizard) if wizard else []}, "
            f"native_open={[probe_texts(item) for item in native_dialogs if item.open]}"
        )
    options = checkboxes(wizard)
    if len(options) != 3:
        raise RuntimeError(
            f"expected three wizard language options, got {len(options)}"
        )
    for key in (
        "wizard-deselect-en",
        "wizard-deselect-zh-cn",
        "wizard-deselect-zh-tw",
    ):
        await raw_click_gate(key)

    wizard_run_calls = []
    original_start_sequence = pipeline.runner.start_sequence
    pipeline.runner.start_sequence = lambda steps, on_complete: wizard_run_calls.append(
        (steps, on_complete)
    )
    wizard_selected_version = None
    try:
        wizard = top_dialog()
        await gate(
            "wizard-step1-next",
            action(wizard, "下一個"),
            verify=lambda wizard=wizard: top_dialog() is not wizard,
        )
        wizard = top_dialog()
        await gate(
            "wizard-step2-next",
            action(wizard, "下一個"),
            verify=lambda wizard=wizard: top_dialog() is not wizard,
        )
        wizard = top_dialog()
        await gate(
            "wizard-step3-next",
            action(wizard, "下一個"),
            verify=lambda wizard=wizard: top_dialog() is not wizard,
        )
        wizard = top_dialog()
        search, selection, selected_label, list_container = bundle_version_picker(
            wizard
        )
        await gate(
            "wizard-version-expand",
            selection,
            verify=lambda list_container=list_container: bool(list_container.visible),
            observed=wizard,
        )
        await raw_click_gate("wizard-version-wheel")
        await raw_click_gate("wizard-version-search")
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            version_labels = [
                item.content.value
                for item in list_container.content.controls
                if isinstance(item, ft.Container)
                and isinstance(getattr(item, "content", None), ft.Text)
            ]
            if (
                (search.value or "").strip() == "1.20"
                and version_labels
                and all("1.20" in value for value in version_labels)
            ):
                break
            await asyncio.sleep(0.05)
        filtered = (
            (search.value or "").strip() == "1.20"
            and bool(version_labels)
            and all("1.20" in value for value in version_labels)
        )
        page.title = (
            "SMOKE:CLICK:wizard-version-search:DONE:PASS"
            if filtered
            else "SMOKE:CLICK:wizard-version-search:DONE:FAIL"
        )
        page.update()
        await _wait_for_capture_ack(
            runtime_root,
            f"{theme}-{viewport}-click-probe-wizard-version-search-verified",
        )
        if not filtered or not list_container.content.controls:
            raise RuntimeError("wizard Minecraft version search failed")
        first_version = list_container.content.controls[0]
        wizard_selected_version = first_version.content.value
        await gate(
            "wizard-version-select",
            first_version,
            verify=lambda selected_label=selected_label, expected_version=wizard_selected_version, list_container=list_container: (
                selected_label.value == expected_version and not list_container.visible
            ),
            observed=wizard,
        )
        wizard = top_dialog()
        await gate(
            "wizard-invalid-confirm",
            action(wizard, "確定執行"),
            verify=lambda wizard=wizard: (
                all_dialog_feedback(wizard, "至少勾選一個語言代碼")
                and not wizard_run_calls
                and not wizard_execution_configs
            ),
        )

        wizard = top_dialog()
        await gate(
            "wizard-step4-prev",
            action(wizard, "上一個"),
            verify=lambda wizard=wizard: top_dialog() is not wizard,
        )
        wizard = top_dialog()
        await gate(
            "wizard-step3-prev",
            action(wizard, "上一個"),
            verify=lambda wizard=wizard: top_dialog() is not wizard,
        )
        wizard = top_dialog()
        await gate(
            "wizard-step2-prev",
            action(wizard, "上一個"),
            verify=lambda wizard=wizard: top_dialog() is not wizard,
        )
        wizard = top_dialog()
        await raw_click_gate("wizard-select-en")
        for key in (
            "wizard-step1-next-valid",
            "wizard-step2-next-valid",
            "wizard-step3-next-valid",
        ):
            current = top_dialog()
            await gate(
                key,
                action(current, "下一個"),
                verify=lambda current=current: top_dialog() is not current,
            )
        wizard = top_dialog()
        await gate(
            "wizard-valid-confirm",
            action(wizard, "確定執行"),
            verify=lambda: (
                top_dialog() is None
                and len(wizard_run_calls) == 1
                and len(wizard_execution_configs) == 1
                and wizard_execution_configs[0]["lang_codes"] == ["en_us"]
                and wizard_execution_configs[0]["version"] == wizard_selected_version
            ),
        )
        pipeline._end_run()
    finally:
        pipeline.runner.start_sequence = original_start_sequence

    _dispose_and_probe_late_task(page, shell)
    await asyncio.sleep(0.2)
    page.title = "SMOKE:DIALOG_CLICK_PROBE:DONE"
    page.update()


def _smoke_control_tree(root):
    """Yield a Flet control tree without walking parent cycles."""
    pending = [root]
    seen = set()
    while pending:
        control = pending.pop()
        if id(control) in seen:
            continue
        seen.add(id(control))
        yield control
        for name in ("controls", "content", "actions", "title"):
            children = getattr(control, name, None)
            if isinstance(children, (list, tuple)):
                pending.extend(children)
            elif children is not None and not isinstance(children, str):
                pending.append(children)


def _smoke_button_label(button) -> str:
    """Get the visible title from standard Flet buttons."""
    content = getattr(button, "content", None)
    if isinstance(content, str):
        return content
    value = getattr(content, "value", None)
    return str(value if value is not None else getattr(button, "text", ""))


async def _run_sequence(
    page: ft.Page,
    shell,
    interval: float,
    scenario: str,
    runtime_root: Path,
    theme: str,
    viewport: str,
    native_dialogs,
) -> None:
    """依所選驗收情境驅動正式外殼，結束時檢查 teardown。"""
    if scenario == "views":
        await _run_view_sequence(page, shell, interval, runtime_root, theme, viewport)
    elif scenario == "dialogs":
        await asyncio.sleep(0.8)
        await _run_dialog_sequence(
            page,
            shell,
            interval,
            runtime_root,
            theme,
            viewport,
            native_dialogs,
        )
    elif scenario == "dialog-click-probe":
        await asyncio.sleep(0.8)
        await _run_dialog_click_probe(
            page, shell, runtime_root, theme, viewport, native_dialogs
        )
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

    native_dialogs = _install_smoke_dialog_tracking(page)

    mode = _query(page, "theme", "dark")
    mode = mode if mode in {"dark", "light"} else "dark"
    start_view = (
        "pipeline"
        if scenario in {"dialogs", "dialog-click-probe"}
        else _query(page, "view", DEFAULT_VIEW_KEY)
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
        _run_sequence,
        page,
        shell,
        interval,
        scenario,
        runtime_root,
        mode,
        viewport,
        native_dialogs,
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
