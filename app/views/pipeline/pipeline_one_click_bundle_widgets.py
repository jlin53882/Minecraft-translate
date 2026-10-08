"""Wizard-specific path fields around the shared bundle form controls."""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

from app.ui.design import C
from app.ui.sync_text_field import SyncTextField
from app.views.pipeline.pipeline_forms import (
    VersionPickerControls,
    build_version_picker,
    dialog_text_field,
)


@dataclass
class Step4BundleWidgets:
    input_field: SyncTextField
    zip_output_field: SyncTextField
    description_field: SyncTextField
    pack_image_field: SyncTextField
    extra_folders_view: ft.ListView
    version_picker: VersionPickerControls


def build_step4_version_widgets(ctx, version_data: dict):
    """Build wizard-owned values using the same picker/form controls as standalone."""

    def on_description(event):
        ctx.state.bundle.description = event.control.value

    def on_zip_output(event):
        ctx.state.bundle.zip_output = event.control.value

    input_field = dialog_text_field(
        ctx.page,
        read_only=True,
        label="輸入來源",
        hint_text="自動帶入打包 staging（含合併語言、Patchouli 與 LM 輸出）",
        value=ctx.pipeline_config.bundle_staging_dir,
        border_color=C.ENCH,
    )
    zip_output_field = dialog_text_field(
        ctx.page,
        on_change=on_zip_output,
        label="輸出 ZIP 檔案",
        value=ctx.state.bundle.zip_output,
        border_color=C.ENCH,
        path_input=True,
    )
    description_field = dialog_text_field(
        ctx.page,
        reserved_width=80,
        on_change=on_description,
        label="檔案敘述",
        hint_text="直接輸入文字，或使用 § 顏色代碼",
        value=ctx.state.bundle.description,
        border_color=C.ENCH,
    )
    pack_image_field = dialog_text_field(
        ctx.page,
        label="封面圖片（可留空）",
        value=ctx.state.bundle.pack_image or "",
        border_color=C.ENCH,
        read_only=True,
    )
    extra_folders_view = ft.ListView(height=60, spacing=2, auto_scroll=False)
    picker = build_version_picker(
        page=ctx.page,
        versions=tuple(version_data),
        selected=ctx.state.bundle.version,
        on_select=lambda value: setattr(ctx.state.bundle, "version", value),
        border_color=C.ENCH,
    )
    return Step4BundleWidgets(
        input_field,
        zip_output_field,
        description_field,
        pack_image_field,
        extra_folders_view,
        picker,
    )
