"""共用的「額外打包來源」UI 控制項。

單獨打包、流水線打包與一鍵流程都使用同一組來源模型與預覽文案，避免某個
入口把資料夾當 Resource Pack、另一個入口卻把它當一般附件。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from translation_tool.core.bundle_sources import (
    RAW_ATTACHMENT_MODE,
    RESOURCE_PACK_MODE,
    ExtraBundleSource,
    coerce_extra_bundle_source,
    source_identity,
)
from translation_tool.core.output_bundler import inspect_extra_bundle_sources


def _human_size(size: int) -> str:
    value = float(max(size, 0))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _source_title(value: object) -> str:
    spec = coerce_extra_bundle_source(value)
    return os.path.basename(os.path.normpath(spec.source)) or spec.source


def _source_detail(value: object, report: dict | None = None) -> str:
    spec = coerce_extra_bundle_source(value)
    mode = "原樣附加" if spec.mode == RAW_ATTACHMENT_MODE else "Resource Pack"
    target = spec.target or "（來源名稱）"
    detail = f"{mode}｜遞迴：{'是' if spec.recursive else '否'}｜ZIP 目標：{target}"
    if report is not None:
        if report.get("valid"):
            detail += f"｜{report.get('files', 0):,} 檔案／{_human_size(report.get('bytes', 0))}"
        else:
            detail += f"｜來源錯誤：{report.get('error') or '無法讀取'}"
    if spec.mode == RAW_ATTACHMENT_MODE:
        detail += "｜不翻譯、不解析"
    return detail


@dataclass
class ExtraBundleSourceControls:
    """三個打包入口共用的控制項集合。"""

    source_field: ft.TextField
    mode_field: ft.Dropdown
    recursive_field: ft.Checkbox
    target_field: ft.TextField
    source_list: ft.ListView
    source_list_container: ft.Container
    preview_text: ft.Text
    feedback_text: ft.Text
    content: ft.Column


def build_extra_source_controls(
    *,
    on_pick: Callable,
    on_manual_add: Callable,
    picker_label: str,
    list_height: int = 110,
) -> ExtraBundleSourceControls:
    """建立來源路徑、模式、遞迴、目標與統計預覽控制項。"""
    source_field = kit.text_field(
        label="來源路徑（Web 可直接輸入）",
        hint="執行程式那台電腦上的檔案或資料夾路徑",
        mono=True,
        expand=True,
    )
    mode_field = kit.dropdown(
        label="附加模式",
        value=RESOURCE_PACK_MODE,
        width=190,
        options=[
            ft.dropdown.Option(RESOURCE_PACK_MODE, "Resource Pack（只收 assets/）"),
            ft.dropdown.Option(RAW_ATTACHMENT_MODE, "原樣附加（所有檔案）"),
        ],
    )
    recursive_field = ft.Checkbox(
        label="遞迴子資料夾",
        value=True,
        on_change=lambda _event: None,
    )
    target_field = kit.text_field(
        label="ZIP 內目標路徑",
        hint="留空使用來源名稱；例如 attachments/guide",
        mono=True,
        expand=True,
    )
    source_list = ft.ListView(
        height=list_height,
        spacing=4,
        auto_scroll=False,
        expand=False,
    )
    preview_text = ft.Text(
        "尚未加入來源。Resource Pack 只收 assets/；原樣附加會保留所有一般檔案。",
        size=11,
        color=C.MUTED,
        selectable=True,
    )
    feedback_text = ft.Text("", size=11, color=C.GOLD, visible=False, selectable=True)
    source_list_container = ft.Container(
        height=list_height + 8,
        visible=False,
        content=source_list,
        border=ft.Border.all(1, C.LINE),
        border_radius=design.RADIUS_CONTROL,
        padding=4,
    )
    content = ft.Column(
        [
            ft.Text(
                "可加入 Resource Pack 來源，或以原樣附加模式保留 .md、.txt、.snbt、圖片與二進位檔。原樣附加不翻譯、不解析。",
                size=11,
                color=C.MUTED,
                selectable=True,
            ),
            ft.Row(
                [
                    source_field,
                    ft.IconButton(
                        icon=ft.Icons.FOLDER_OPEN,
                        tooltip="桌面版選擇資料夾；Web 請直接輸入路徑",
                        on_click=on_pick,
                    ),
                ],
                spacing=6,
            ),
            ft.Row([mode_field, recursive_field, target_field], wrap=True, spacing=8),
            ft.Row(
                [
                    ft.Button(
                        picker_label,
                        icon=ft.Icons.FOLDER_OPEN,
                        on_click=on_pick,
                    ),
                    ft.Button(
                        "加入手動來源",
                        icon=ft.Icons.ADD,
                        on_click=on_manual_add,
                    ),
                ],
                wrap=True,
                spacing=8,
            ),
            preview_text,
            feedback_text,
            source_list_container,
        ],
        spacing=8,
        tight=True,
    )
    return ExtraBundleSourceControls(
        source_field,
        mode_field,
        recursive_field,
        target_field,
        source_list,
        source_list_container,
        preview_text,
        feedback_text,
        content,
    )


def read_extra_source_controls(controls: ExtraBundleSourceControls) -> ExtraBundleSource:
    """讀取目前表單並建立來源模型。"""
    return ExtraBundleSource(
        source=(controls.source_field.value or "").strip(),
        mode=controls.mode_field.value or RESOURCE_PACK_MODE,
        recursive=bool(controls.recursive_field.value),
        target=(controls.target_field.value or "").strip(),
    )


def clear_extra_source_controls(controls: ExtraBundleSourceControls) -> None:
    controls.source_field.value = ""
    controls.target_field.value = ""
    controls.feedback_text.value = ""
    controls.feedback_text.visible = False


def set_extra_source_feedback(
    controls: ExtraBundleSourceControls, message: str, *, error: bool = False
) -> None:
    controls.feedback_text.value = message
    controls.feedback_text.color = C.RED if error else C.GOLD
    controls.feedback_text.visible = bool(message)


def refresh_extra_source_preview(
    controls: ExtraBundleSourceControls,
    values: list[object],
    on_remove: Callable[[object], None],
) -> None:
    """刷新來源清單，並顯示與正式打包相同的統計／錯誤狀態。"""
    controls.source_list.controls.clear()
    controls.source_list_container.visible = bool(values)
    reports: list[dict] = []
    for value in values:
        try:
            reports.append(inspect_extra_bundle_sources([value])[0])
        except (OSError, ValueError) as exc:
            reports.append({"valid": False, "error": str(exc), "files": 0, "bytes": 0})

        report = reports[-1]
        controls.source_list.controls.append(
            ft.Container(
                padding=ft.Padding(left=8, top=5, right=4, bottom=5),
                bgcolor=C.PANEL2,
                border=ft.Border.all(1, C.LINE),
                border_radius=design.RADIUS_CONTROL,
                content=ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text(
                                    _source_title(value),
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                ),
                                ft.Text(
                                    _source_detail(value, report),
                                    size=10.5,
                                    color=C.RED if not report.get("valid") else C.MUTED,
                                    selectable=True,
                                ),
                                ft.Text(
                                    coerce_extra_bundle_source(value).source,
                                    size=10,
                                    color=C.DIM,
                                    font_family=design.FONT_MONO,
                                    selectable=True,
                                    no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            icon_size=14,
                            tooltip="移除",
                            on_click=lambda _event, item=value: on_remove(item),
                        )
                    ]
                ),
            )
        )

    valid_count = sum(1 for report in reports if report.get("valid"))
    files = sum(int(report.get("files") or 0) for report in reports)
    size = sum(int(report.get("bytes") or 0) for report in reports)
    errors = [str(report.get("error")) for report in reports if not report.get("valid")]
    if not values:
        controls.preview_text.value = (
            "尚未加入來源。Resource Pack 只收 assets/；原樣附加會保留所有一般檔案。"
        )
    else:
        controls.preview_text.value = (
            f"來源 {valid_count}/{len(values)} 個可讀取｜預計加入 {files:,} 個檔案／"
            f"{_human_size(size)}"
            + (f"｜錯誤：{'；'.join(errors[:2])}" if errors else "")
        )
def append_extra_source(
    values: list[object], value: object, *, allow_unvalidated: bool = False
) -> tuple[bool, str]:
    """去重加入來源；manual path 使用呼叫端的 inspect 做額外驗證。"""
    try:
        identity = source_identity(value)
    except ValueError as exc:
        return False, str(exc)
    if any(source_identity(existing) == identity for existing in values):
        return False, "相同的來源、模式、遞迴設定與 ZIP 目標已加入"
    if not allow_unvalidated:
        report = inspect_extra_bundle_sources([value])[0]
        if not report.get("valid"):
            return False, str(report.get("error") or "來源無法讀取")
    values.append(value)
    return True, ""
