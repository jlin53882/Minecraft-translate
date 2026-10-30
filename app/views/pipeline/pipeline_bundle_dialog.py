"""Step 4 - 打包資源對話框

用途：
- 選擇翻譯後的輸入資料夾（自動帶入 lm_translate 輸出）
- 設定輸出 ZIP 檔案路徑
- 設定檔案敘述、Minecraft 版本（或 pack_format）、封面圖片
- 新增其他指定資料夾
- 執行打包（背景執行緒 + 進度輪詢）
"""

import functools
import os
import types

import flet as ft

from app.services_impl.pack_format_service import build_pack_mcmeta
from app.services_impl.pipelines.bundle_service import (
    RESOURCE_PACK_MODE,
    ExtraBundleSource,
    normalize_output_zip_name,
)
from app.services_impl.pipelines.extract_service import open_output_folder
from app.ui.design import C
from app.ui.dialogs import close_overlay_dialog, present_dialog, set_dialog_feedback
from app.views.pipeline.extra_bundle_sources import (
    append_extra_source,
    build_extra_source_controls,
    clear_extra_source_controls,
    read_extra_source_controls,
    refresh_extra_source_preview,
    set_extra_source_feedback,
)
from app.views.pipeline.pipeline_config import RESOURCE_PACK_INSTALL_HINT
from app.views.pipeline.pipeline_forms import (
    BundleFormState,
    build_bundle_form,
    build_pipeline_dialog,
    build_version_input,
    dialog_button,
    dialog_dimensions,
    dialog_text_field,
)
from translation_tool.utils.config_manager import (
    get_lm_translate_folder_name,
    load_config,
)


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
            on_start_bundle(input_root_dir, output_zip_path, description, pack_format,
                            max_pack_format, pack_image_path, extra_folders)
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
    version_input = _bundle_build_version_widgets(ctx)
    _bundle_build_extra_widgets(ctx)
    content = _bundle_build_content(ctx, version_input)

    dialog = build_pipeline_dialog(
        page,
        title="打包資源設定",
        icon=ft.Icons.INVENTORY_2_OUTLINED,
        tone="gold",
        content=content,
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
    output_zip_name = normalize_output_zip_name(bundler_cfg.get("output_zip_name"))
    translate_output_subfolder = get_lm_translate_folder_name(cfg)

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
    """檔案敘述與單一「版本或 pack_format」輸入框。"""
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
    ctx.version_input = build_version_input(
        page=ctx.page,
        value="",
        on_change=lambda value: setattr(ctx.bundle_form_state, "version_input", value),
        on_max_change=lambda value: setattr(ctx.bundle_form_state, "max_input", value),
        border_color=C.ENCH,
    )
    return ctx.version_input


def _bundle_build_extra_widgets(ctx) -> None:
    """封面圖片與額外資料夾控制項、handler 綁定。"""

    ctx.pack_image_field = dialog_text_field(
        ctx.page,
        label="封面圖片（可留空）",
        hint_text="選擇 pack.png 圖片",
        border_color=C.ENCH,
        read_only=True,
    )
    ctx.extra_folders = ctx.bundle_form_state.extra_folders
    ctx.add_extra_folder = functools.partial(_bundle_add_extra_folder, ctx)
    ctx.extra_source_controls = build_extra_source_controls(
        on_pick=ctx.add_extra_folder,
        on_manual_add=lambda e=None: _bundle_add_extra_folder_manually(ctx, e),
        picker_label="+ 新增資料夾",
        list_height=110,
    )
    ctx.extra_folders_view = ctx.extra_source_controls.source_list

    ctx.close_dialog = functools.partial(_bundle_close_dialog, ctx)

    ctx.start_bundle = functools.partial(_bundle_start_bundle, ctx)

    ctx.pick_input_dir = functools.partial(_bundle_pick_input_dir, ctx)

    ctx.browse_input_dir = functools.partial(_bundle_browse_input_dir, ctx)

    ctx.pick_output_zip = functools.partial(_bundle_pick_output_zip, ctx)

    ctx.pick_pack_image = functools.partial(_bundle_pick_pack_image, ctx)

    ctx._refresh_extra_folders = functools.partial(_bundle__refresh_extra_folders, ctx)

    ctx._remove_extra_folder = functools.partial(_bundle__remove_extra_folder, ctx)

    ctx.show_preview_result = functools.partial(_bundle_show_preview_result, ctx)


def _bundle_build_content(ctx, _version_input):
    """Build editable standalone paths and compose the shared bundle form."""
    path_section = ft.Column(
        [
            ft.Text("輸入來源", weight="bold", size=13),
            ft.Text("留空自動帶入，翻譯完成後再使用", size=10, color=C.MUTED),
            ft.Row(
                [
                    ctx.bundle_input_field,
                    dialog_button(
                        "選擇資料夾",
                        icon=ft.Icons.FOLDER,
                        on_click=ctx.pick_input_dir,
                        role="preview",
                    ),
                    dialog_button(
                        "瀏覽",
                        icon=ft.Icons.SEARCH,
                        on_click=ctx.browse_input_dir,
                    ),
                ],
                wrap=True,
            ),
            ft.Text("輸出 ZIP 檔案", weight="bold", size=13),
            ft.Row(
                [
                    ctx.bundle_output_zip_field,
                    dialog_button(
                        "選擇儲存位置",
                        icon=ft.Icons.SAVE,
                        on_click=ctx.pick_output_zip,
                        role="preview",
                    ),
                ],
                wrap=True,
            ),
            ft.Text(RESOURCE_PACK_INSTALL_HINT, size=10, color=C.MUTED),
        ],
        spacing=8,
    )
    image_row = ft.Row(
        [
            ctx.pack_image_field,
            dialog_button(
                "選擇檔案...",
                icon=ft.Icons.IMAGE,
                on_click=ctx.pick_pack_image,
                role="preview",
            ),
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
            ctx.extra_source_controls.content,
        ],
        spacing=6,
    )
    return build_bundle_form(
        path_section=path_section,
        description_field=ctx.description_field,
        version_input=ctx.version_input,
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
    try:
        pack_format = ctx.version_input.pack_format()
        max_pack_format = ctx.version_input.max_pack_format()
        build_pack_mcmeta("", pack_format, max_pack_format)
    except ValueError as exc:
        set_dialog_feedback(ctx.page, ctx.feedback, f"⚠️ {exc}", C.GOLD)
        return

    if not ctx.close_dialog(dialog):
        return
    ctx.run_started = True
    ctx.on_start_bundle(
        input_root_dir=input_dir or ctx.default_input,
        output_zip_path=output_zip or ctx.default_output_zip,
        description=(ctx.description_field.value or "").strip(),
        pack_format=pack_format,
        max_pack_format=max_pack_format,
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
        if result:
            controls = ctx.extra_source_controls
            mode = controls.mode_field.value or RESOURCE_PACK_MODE
            if (
                mode == RESOURCE_PACK_MODE
                and not (controls.target_field.value or "").strip()
            ):
                value: object = result
            else:
                value = ExtraBundleSource(
                    result,
                    mode=mode,
                    recursive=bool(controls.recursive_field.value),
                    target=(controls.target_field.value or "").strip(),
                )
            added, message = append_extra_source(
                ctx.extra_folders, value, allow_unvalidated=True
            )
            if not added:
                set_extra_source_feedback(controls, message, error=True)
                ctx.page.update()
                return
            clear_extra_source_controls(controls)
            ctx._refresh_extra_folders()
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _bundle__refresh_extra_folders(ctx):
    refresh_extra_source_preview(
        ctx.extra_source_controls,
        ctx.extra_folders,
        ctx._remove_extra_folder,
    )


def _bundle_add_extra_folder_manually(ctx, e=None):
    del e
    controls = ctx.extra_source_controls
    try:
        value = read_extra_source_controls(controls)
        added, message = append_extra_source(ctx.extra_folders, value)
    except ValueError as exc:
        added, message = False, str(exc)
    if not added:
        set_extra_source_feedback(controls, message, error=True)
        ctx.page.update()
        return
    clear_extra_source_controls(controls)
    ctx._refresh_extra_folders()
    ctx.page.update()


def _bundle__remove_extra_folder(ctx, path: object):
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
