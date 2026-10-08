"""Step 4 bundle controls used by the one-click pipeline dialog."""

from __future__ import annotations

import flet as ft

from app.ui.design import C
from app.ui.sync_text_field import SyncTextField


def build_step4_version_widgets(ctx, version_data: list[str]):
    """Build the bundle output, description, cover and version controls."""
    bundle_input_field, zip_output_field, desc_field, pack_image_field = (
        _build_bundle_fields(ctx)
    )
    version_toggle_label = ft.Text(
        ctx.state["version"] or "點擊選擇版本",
        expand=True,
        size=12,
        color=C.MUTED,
    )
    version_list = _build_version_list(ctx, version_data, version_toggle_label)
    version_dropdown = ft.Container(
        content=version_list,
        height=140,
        border=ft.Border.all(1, C.DIM),
        border_radius=6,
        padding=4,
        visible=False,
    )
    version_expanded = False

    def toggle_version(_event=None):
        nonlocal version_expanded
        version_expanded = not version_expanded
        version_dropdown.visible = version_expanded
        ctx.page.update()

    return (
        toggle_version,
        bundle_input_field,
        desc_field,
        ft.ListView(height=60, spacing=2),
        pack_image_field,
        version_dropdown,
        version_toggle_label,
        zip_output_field,
    )


def _build_bundle_fields(ctx):
    """Build the step 4 text fields and bind their edits to the wizard state."""

    def on_description(event):
        ctx.state["description"] = event.control.value

    def on_zip_output(event):
        ctx.state["zip_output"] = event.control.value

    return (
        SyncTextField(
            read_only=True,
            label="輸入來源",
            hint_text="自動帶入翻譯完成後的輸出",
            value=ctx.state["bundle_input"],
            expand=True,
            border_color=C.ENCH,
        ),
        SyncTextField(
            on_change=on_zip_output,
            label="輸出 ZIP 檔案",
            value=ctx.state["zip_output"],
            expand=True,
            border_color=C.ENCH,
            path_input=True,
        ),
        SyncTextField(
            on_change=on_description,
            label="檔案敘述",
            hint_text="直接輸入文字，或使用 § 顏色代碼",
            value=ctx.state["description"],
            expand=True,
            border_color=C.ENCH,
        ),
        SyncTextField(
            label="封面圖片（可留空）",
            value=ctx.state["pack_image"] or "",
            expand=True,
            border_color=C.ENCH,
            read_only=True,
        ),
    )


def _build_version_list(ctx, version_data, version_toggle_label):
    """Bind supported pack-format choices to the wizard's version state."""
    version_list = ft.ListView(expand=True, height=140, spacing=4)

    def select_version(version: str):
        ctx.state["version"] = version
        version_toggle_label.value = version
        version_toggle_label.color = None
        ctx.page.update()

    for version in version_data:
        version_list.controls.append(
            ft.Container(
                content=ft.Text(version, size=13),
                padding=8,
                border=ft.Border.all(1, C.DIM),
                border_radius=6,
                on_click=lambda event, selected=version: select_version(selected),
            )
        )
    if not version_data:
        version_list.controls.append(ft.Text("無可用版本", size=12, color=C.DIM))
    return version_list
