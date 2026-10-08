"""Step 4 - 打包資源對話框

用途：
- 選擇翻譯後的輸入資料夾（自動帶入 lm_translate 輸出）
- 設定輸出 ZIP 檔案路徑
- 設定檔案敘述、Minecraft 版本、封面圖片
- 新增其他指定資料夾
- 執行打包（背景執行緒 + 進度輪詢）
"""

import functools
import json
import os
import types

import flet as ft

from app.services_impl.pipelines.extract_service import open_output_folder
from app.ui.design import C
from app.ui.dialogs import close_overlay_dialog, present_dialog, set_dialog_feedback
from app.views.pipeline.pipeline_forms import (
    BundleFormState,
    build_bundle_form,
    build_version_picker,
    dialog_button,
    dialog_content,
    dialog_dimensions,
    dialog_text_field,
)
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_warning


def _load_version_data():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "translation_tool",
        "core",
        "resource_pack_version.json",
    )
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001 - 讀不到版本資料時使用空設定，UI 仍可開啟
            log_warning(f"讀取版本資料失敗，使用空設定：{config_path}: {exc!r}")
            return {}
    return {}


def open_bundle_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str,
    output_path: str,
    on_start_bundle,
    show_snack_bar,
):
    """打開打包資源設定對話框。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: 預填的輸入資料夾路徑
        output_path: 預填的輸出目錄路徑
        on_start_bundle: 回調函式，簽名：
            on_start_bundle(input_root_dir, output_zip_path, description, min_format, max_format,
                            pack_image_path, extra_folders)
        show_snack_bar: 回調：(message: str, color: str = RED_400) -> void
    """
    ctx = types.SimpleNamespace(
        page=page,
        file_picker=file_picker,
        on_start_bundle=on_start_bundle,
        show_snack_bar=show_snack_bar,
        dialogs=[],
        run_started=False,
    )
    ctx.feedback = ft.Text("", size=12, color=C.GOLD, visible=False)
    _bundle_init_state_and_fields(ctx, input_path, output_path)
    version_picker = _bundle_build_version_widgets(ctx)
    _bundle_build_extra_widgets(ctx)
    content = _bundle_build_content(ctx, version_picker)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("📦 打包資源設定"),
        content=dialog_content(page, content),
        actions=[
            dialog_button("取消", lambda e: ctx.close_dialog(dialog)),
            dialog_button(
                "預覽結果",
                on_click=lambda e: ctx.show_preview_result(dialog),
                icon=ft.Icons.PREVIEW,
                role="preview",
            ),
            dialog_button(
                "確定執行",
                on_click=lambda e: ctx.start_bundle(dialog),
                icon=ft.Icons.CHECK,
                role="primary",
            ),
        ],
    )

    present_dialog(ctx, dialog)


def _bundle_init_state_and_fields(ctx, input_path, output_path):
    """打包對話框的路徑、設定預設值與輸入欄位。"""
    dialog_width, _ = dialog_dimensions(ctx.page)

    cfg = load_config()
    bundler_cfg = cfg.get("output_bundler", {})
    output_zip_name = bundler_cfg.get("output_zip_name", "可使用翻譯.zip")
    lang_merger_cfg = cfg.get("lang_merger", {})
    translate_output_subfolder = lang_merger_cfg.get(
        "lm_translate_folder_name", "_翻譯輸出"
    )

    ctx.default_input = (
        os.path.join(output_path, "lm_translate", translate_output_subfolder)
        if output_path
        else ""
    )
    ctx.default_output_zip = (
        os.path.join(output_path, output_zip_name) if output_path else ""
    )
    ctx.bundle_form_state = BundleFormState(zip_output=ctx.default_output_zip)

    ctx.bundle_input_field = dialog_text_field(
        ctx.page,
        label="輸入來源",
        hint_text=f"自動帶入：{ctx.default_input}"
        if ctx.default_input
        else "留空自動帶入翻譯完成後的輸出",
        value=ctx.default_input,
        border_color=C.ENCH,
        path_input=True,
    )
    ctx.bundle_output_zip_field = dialog_text_field(
        ctx.page,
        on_change=lambda event: setattr(
            ctx.bundle_form_state, "zip_output", event.control.value
        ),
        label="輸出 ZIP 檔案",
        hint_text=f"自動帶入：{ctx.default_output_zip}"
        if ctx.default_output_zip
        else "留空自動帶入可使用翻譯.zip",
        value=ctx.default_output_zip,
        border_color=C.ENCH,
        path_input=True,
    )
    return dialog_width


def _bundle_build_version_widgets(ctx):
    """Build the shared searchable pack-version selector and description field."""
    ctx.description_field = dialog_text_field(
        ctx.page,
        reserved_width=80,
        on_change=lambda event: setattr(
            ctx.bundle_form_state, "description", event.control.value
        ),
        label="檔案敘述",
        hint_text="直接輸入文字，或使用 § 顏色代碼",
        border_color=C.ENCH,
    )

    ctx.version_data = _load_version_data()
    ctx.version_picker = build_version_picker(
        page=ctx.page,
        versions=tuple(ctx.version_data),
        selected="",
        on_select=lambda value: setattr(ctx.bundle_form_state, "version", value),
        border_color=C.ENCH,
    )
    ctx.version_search = ctx.version_picker.search
    ctx.version_list = ctx.version_picker.list_view
    ctx.version_toggle_label = ctx.version_picker.selected_label
    ctx.version_dropdown_container = ctx.version_picker.list_container
    ctx._refresh_version_list = ctx.version_picker.refresh
    ctx._select_version = ctx.version_picker.select
    ctx._toggle_version_expand = ctx.version_picker.toggle
    return ctx.version_picker


def _bundle_build_extra_widgets(ctx) -> None:
    """封面圖片與額外資料夾控制項、handler 綁定。"""

    ctx.pack_image_field = dialog_text_field(
        ctx.page,
        label="封面圖片（可留空）",
        hint_text="選擇 pack.png 圖片",
        border_color=C.ENCH,
        read_only=True,
    )
    ctx.extra_folders_view = ft.ListView(height=80, spacing=4, auto_scroll=False)
    ctx.extra_folders = ctx.bundle_form_state.extra_folders

    ctx.close_dialog = functools.partial(_bundle_close_dialog, ctx)

    ctx.start_bundle = functools.partial(_bundle_start_bundle, ctx)

    ctx.pick_input_dir = functools.partial(_bundle_pick_input_dir, ctx)

    ctx.browse_input_dir = functools.partial(_bundle_browse_input_dir, ctx)

    ctx.pick_output_zip = functools.partial(_bundle_pick_output_zip, ctx)

    ctx.pick_pack_image = functools.partial(_bundle_pick_pack_image, ctx)

    ctx.add_extra_folder = functools.partial(_bundle_add_extra_folder, ctx)

    ctx._refresh_extra_folders = functools.partial(_bundle__refresh_extra_folders, ctx)

    ctx._remove_extra_folder = functools.partial(_bundle__remove_extra_folder, ctx)

    ctx.show_preview_result = functools.partial(_bundle_show_preview_result, ctx)


def _bundle_build_content(ctx, _version_picker):
    """Build editable standalone paths and compose the shared bundle form."""
    path_section = ft.Column(
        [
            ft.Text("輸入來源", weight="bold", size=13),
            ft.Text("留空自動帶入，翻譯完成後再使用", size=10, color=C.MUTED),
            ft.Row(
                [
                    ctx.bundle_input_field,
                    ft.Button(
                        "選擇資料夾", icon=ft.Icons.FOLDER, on_click=ctx.pick_input_dir
                    ),
                    ft.Button(
                        "瀏覽", icon=ft.Icons.SEARCH, on_click=ctx.browse_input_dir
                    ),
                ],
                wrap=True,
            ),
            ft.Text("輸出 ZIP 檔案", weight="bold", size=13),
            ft.Row(
                [
                    ctx.bundle_output_zip_field,
                    ft.Button(
                        "選擇儲存位置", icon=ft.Icons.SAVE, on_click=ctx.pick_output_zip
                    ),
                ],
                wrap=True,
            ),
        ],
        spacing=8,
    )
    image_row = ft.Row(
        [
            ctx.pack_image_field,
            ft.Button("選擇檔案...", icon=ft.Icons.IMAGE, on_click=ctx.pick_pack_image),
            dialog_button(
                "移除",
                lambda _event: _bundle_clear_pack_image(ctx),
                icon=ft.Icons.DELETE,
            ),
        ],
        wrap=True,
    )
    extra_section = ft.Column(
        [
            ft.Container(
                content=ctx.extra_folders_view,
                border=ft.Border.all(1, C.DIM),
                border_radius=6,
                padding=4,
            ),
            ft.Button(
                "+ 新增資料夾", icon=ft.Icons.FOLDER_OPEN, on_click=ctx.add_extra_folder
            ),
        ],
        spacing=6,
    )
    return build_bundle_form(
        path_section=path_section,
        description_field=ctx.description_field,
        version_picker=ctx.version_picker,
        pack_image_row=image_row,
        extra_folders_section=extra_section,
        feedback=ctx.feedback,
    )


def _bundle_close_dialog(ctx, dialog):
    return close_overlay_dialog(ctx.page, dialog)


def _bundle_start_bundle(ctx, dialog):
    if ctx.run_started or not dialog.open:
        return
    input_dir = (ctx.bundle_input_field.value or "").strip()
    output_zip = (ctx.bundle_output_zip_field.value or "").strip()

    input_dir = input_dir or ctx.default_input
    if not input_dir or not os.path.isdir(input_dir):
        set_dialog_feedback(ctx.page, ctx.feedback, "⚠️ 輸入資料夾不存在", C.GOLD)
        return
    if not output_zip:
        set_dialog_feedback(ctx.page, ctx.feedback, "⚠️ 輸出 ZIP 檔名不可空白", C.GOLD)
        return

    pack_img = (ctx.pack_image_field.value or "").strip()
    if pack_img:
        ext = os.path.splitext(pack_img)[1].lower()
        if not os.path.isfile(pack_img):
            set_dialog_feedback(ctx.page, ctx.feedback, "⚠️ 封面圖片檔案不存在", C.GOLD)
            return
        if ext not in (".png", ".jpg", ".jpeg"):
            set_dialog_feedback(
                ctx.page, ctx.feedback, "⚠️ 封面圖片只支援 .png/.jpg", C.GOLD
            )
            return

    ctx.bundle_form_state.description = (ctx.description_field.value or "").strip()
    ctx.bundle_form_state.zip_output = output_zip
    version_info = ctx.version_data.get(ctx.bundle_form_state.version or "", {})

    if not ctx.close_dialog(dialog):
        return
    ctx.run_started = True
    ctx.on_start_bundle(
        input_root_dir=input_dir or ctx.default_input,
        output_zip_path=output_zip or ctx.default_output_zip,
        description=(ctx.description_field.value or "").strip(),
        min_format=version_info.get("min_format"),
        max_format=version_info.get("max_format"),
        pack_image_path=pack_img or None,
        extra_folders=list(ctx.extra_folders),
    )


def _bundle_pick_input_dir(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.get_directory_path()
        if result:
            ctx.bundle_input_field.value = result
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _bundle_browse_input_dir(ctx, e=None):
    path = (ctx.bundle_input_field.value or "").strip()
    if path and os.path.isdir(path):
        if not open_output_folder(path):
            ctx.show_snack_bar("⚠️ 無法開啟資料夾")
    elif not path:
        ctx.show_snack_bar("⚠️ 請先選擇資料夾")
    else:
        ctx.show_snack_bar("⚠️ 路徑不存在")


def _bundle_pick_output_zip(ctx, e=None):
    async def do_pick():
        # 輸出必須是 ZIP「檔案」路徑：用另存新檔，而不是選資料夾
        result = await ctx.file_picker.save_file(
            dialog_title="選擇輸出 ZIP 檔案",
            file_name=os.path.basename(ctx.default_output_zip) or "可使用翻譯.zip",
            allowed_extensions=["zip"],
        )
        if result:
            if not result.lower().endswith(".zip"):
                result += ".zip"
            ctx.bundle_output_zip_field.value = result
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _bundle_pick_pack_image(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.pick_files(
            dialog_title="選擇封面圖片",
            allowed_extensions=["png", "jpg", "jpeg"],
        )
        # Flet 1.0：pick_files() 直接回傳 list[FilePickerFile]
        if result:
            ctx.bundle_form_state.pack_image = result[0].path
            ctx.pack_image_field.value = result[0].path
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _bundle_add_extra_folder(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.get_directory_path()
        if result and result not in ctx.extra_folders:
            ctx.extra_folders.append(result)
            ctx._refresh_extra_folders()
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _bundle__refresh_extra_folders(ctx):
    ctx.extra_folders_view.controls.clear()
    for path in ctx.extra_folders:
        name = os.path.basename(path)
        ctx.extra_folders_view.controls.append(
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text(f"{name}", expand=True, size=12),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        tooltip="移除",
                        icon_size=16,
                        on_click=lambda e, p=path: ctx._remove_extra_folder(p),
                    ),
                ],
            )
        )


def _bundle__remove_extra_folder(ctx, path: str):
    if path in ctx.extra_folders:
        ctx.extra_folders.remove(path)
        ctx._refresh_extra_folders()
        ctx.page.update()


def _bundle_clear_pack_image(ctx):
    ctx.bundle_form_state.pack_image = None
    ctx.pack_image_field.value = ""
    ctx.page.update()


def _bundle_show_preview_result(ctx, dialog):
    if not dialog.open:
        return
    input_dir = (ctx.bundle_input_field.value or "").strip() or ctx.default_input
    if not input_dir or not os.path.isdir(input_dir):
        set_dialog_feedback(ctx.page, ctx.feedback, "⚠️ 輸入資料夾不存在", C.GOLD)
        return
    set_dialog_feedback(
        ctx.page,
        ctx.feedback,
        "資源打包預覽尚未支援；目前設定已保留，請直接執行或取消。",
        C.GOLD,
    )
