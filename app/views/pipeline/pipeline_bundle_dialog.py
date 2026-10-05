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
from app.ui.dialogs import close_overlay_dialog
from translation_tool.utils.config_manager import load_config


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
        except Exception:  # noqa: BLE001 - 讀不到版本資料時使用空設定，UI 仍可開啟
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
    )
    dialog_width = _bundle_init_state_and_fields(ctx, input_path, output_path)
    version_search = _bundle_build_version_widgets(ctx)
    _bundle_build_extra_widgets(ctx)
    content = _bundle_build_content(ctx, version_search)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("📦 打包資源設定"),
        content=ft.Container(content=content, width=dialog_width),
        actions=[
            ft.TextButton("取消", on_click=lambda e: ctx.close_dialog(dialog)),
            ft.OutlinedButton(
                "預覽結果",
                icon=ft.Icons.PREVIEW,
                on_click=lambda e: ctx.show_preview_result(dialog),
            ),
            ft.Button(
                "確定執行",
                icon=ft.Icons.CHECK,
                bgcolor=C.ENCH,
                color=C.ON_EM,
                on_click=lambda e: ctx.start_bundle(dialog),
            ),
        ],
    )

    ctx.page.overlay.append(dialog)
    dialog.open = True
    ctx.page.update()


def _bundle_init_state_and_fields(ctx, input_path, output_path):
    """打包對話框的路徑、設定預設值與輸入欄位。"""
    dialog_width = int(ctx.page.width * 0.6)

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

    ctx.bundle_input_field = ft.TextField(
        label="輸入來源",
        hint_text=f"自動帶入：{ctx.default_input}"
        if ctx.default_input
        else "留空自動帶入翻譯完成後的輸出",
        value=ctx.default_input,
        expand=True,
        border_color=C.ENCH,
    )
    ctx.bundle_output_zip_field = ft.TextField(
        label="輸出 ZIP 檔案",
        hint_text=f"自動帶入：{ctx.default_output_zip}"
        if ctx.default_output_zip
        else "留空自動帶入可使用翻譯.zip",
        value=ctx.default_output_zip,
        expand=True,
        border_color=C.ENCH,
    )
    return dialog_width


def _bundle_build_version_widgets(ctx):
    """版本選擇相關控制項。"""
    ctx.description_field = ft.TextField(
        label="檔案敘述",
        hint_text="直接輸入文字，或使用 § 顏色代碼",
        expand=True,
        border_color=C.ENCH,
    )

    ctx.version_data = _load_version_data()
    ctx.version_list = ft.ListView(
        expand=True, height=160, spacing=4, auto_scroll=False
    )
    ctx.version_expanded = False
    ctx.selected_version = None

    ctx.version_toggle_label = ft.Text("", size=12, expand=True)
    version_search = ft.TextField(
        label="搜尋版本",
        hint_text="輸入版本關鍵字...",
        expand=True,
        border_color=C.ENCH,
        dense=True,
        on_change=lambda e: ctx._refresh_version_list(e.control.value or ""),
    )

    ctx._refresh_version_list = functools.partial(_bundle__refresh_version_list, ctx)

    ctx._select_version = functools.partial(_bundle__select_version, ctx)

    ctx._refresh_version_list("")

    ctx._toggle_version_expand = functools.partial(_bundle__toggle_version_expand, ctx)

    ctx.version_dropdown_container = ft.Container(
        content=ctx.version_list,
        height=160,
        border=ft.Border.all(1, C.DIM),
        border_radius=6,
        padding=4,
        visible=False,
    )
    return version_search


def _bundle_build_extra_widgets(ctx) -> None:
    """封面圖片與額外資料夾控制項、handler 綁定。"""

    ctx.pack_image_field = ft.TextField(
        label="封面圖片（可留空）",
        hint_text="選擇 pack.png 圖片",
        expand=True,
        border_color=C.ENCH,
        read_only=True,
    )
    ctx.extra_folders_view = ft.ListView(height=80, spacing=4, auto_scroll=False)
    ctx.extra_folders: list[str] = []

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


def _bundle_build_content(ctx, version_search):
    """對話框內容。"""

    content = ft.Column(
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
                ]
            ),
            ft.Text("輸出 ZIP 檔案", weight="bold", size=13),
            ft.Row(
                [
                    ctx.bundle_output_zip_field,
                    ft.Button(
                        "選擇儲存位置", icon=ft.Icons.SAVE, on_click=ctx.pick_output_zip
                    ),
                ]
            ),
            ft.Text("檔案敘述", weight="bold", size=13),
            ctx.description_field,
            ft.Text("Minecraft 版本", weight="bold", size=13),
            version_search,
            ft.Container(
                content=ft.Row(
                    [
                        ft.Text("已選擇：", size=11, color=C.MUTED),
                        ctx.version_toggle_label,
                        ft.Icon(ft.Icons.EXPAND_MORE, size=18),
                    ]
                ),
                padding=8,
                border=ft.Border.all(1, C.DIM),
                border_radius=6,
                on_click=ctx._toggle_version_expand,
            ),
            ctx.version_dropdown_container,
            ft.Text("封面圖片（可留空）", weight="bold", size=13),
            ft.Row(
                [
                    ctx.pack_image_field,
                    ft.Button(
                        "選擇檔案...", icon=ft.Icons.IMAGE, on_click=ctx.pick_pack_image
                    ),
                    ft.Button(
                        "移除",
                        icon=ft.Icons.DELETE,
                        on_click=lambda e: (
                            setattr(ctx.pack_image_field, "value", "")
                            or ctx.page.update()
                        ),
                    ),
                ]
            ),
            ft.Text("其他指定資料夾", weight="bold", size=13),
            ft.Container(
                content=ctx.extra_folders_view,
                border=ft.Border.all(1, C.DIM),
                border_radius=8,
                padding=4,
            ),
            ft.Button(
                "+ 新增資料夾", icon=ft.Icons.FOLDER_OPEN, on_click=ctx.add_extra_folder
            ),
        ],
        spacing=10,
        tight=False,
    )
    return content


def _bundle__refresh_version_list(ctx, search_text: str):
    ctx.version_list.controls.clear()
    filtered = [v for v in ctx.version_data if search_text.lower() in v.lower()]
    if not filtered:
        ctx.version_list.controls.append(ft.Text("無可用版本", size=12, color=C.DIM))
    for version_key in filtered:
        item = ft.Container(
            content=ft.Text(version_key, size=13),
            padding=8,
            border=ft.Border.all(1, C.DIM),
            border_radius=6,
            on_click=lambda e, v=version_key: ctx._select_version(v),
        )
        ctx.version_list.controls.append(item)
    ctx.page.update()


def _bundle__select_version(ctx, version: str):
    ctx.selected_version = version
    ctx.version_toggle_label.value = version  # 「已選擇：」前綴是版面上的固定文字
    ctx.version_expanded = False
    ctx.version_dropdown_container.visible = False
    ctx.page.update()


def _bundle__toggle_version_expand(ctx, e=None):
    ctx.version_expanded = not ctx.version_expanded
    ctx.version_dropdown_container.visible = ctx.version_expanded
    ctx.page.update()


def _bundle_close_dialog(ctx, dialog):
    close_overlay_dialog(ctx.page, dialog)


def _bundle_start_bundle(ctx, dialog):
    input_dir = (ctx.bundle_input_field.value or "").strip()
    output_zip = (ctx.bundle_output_zip_field.value or "").strip()

    if input_dir and not os.path.isdir(input_dir):
        ctx.show_snack_bar("⚠️ 輸入資料夾不存在")
        return
    if not output_zip:
        ctx.show_snack_bar("⚠️ 輸出 ZIP 檔名不可空白")
        return

    pack_img = (ctx.pack_image_field.value or "").strip()
    if pack_img:
        ext = os.path.splitext(pack_img)[1].lower()
        if not os.path.isfile(pack_img):
            ctx.show_snack_bar("⚠️ 封面圖片檔案不存在")
            return
        if ext not in (".png", ".jpg", ".jpeg"):
            ctx.show_snack_bar("⚠️ 封面圖片只支援 .png/.jpg")
            return

    version_info = ctx.version_data.get(ctx.selected_version or "", {})

    ctx.close_dialog(dialog)
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


def _bundle_show_preview_result(ctx, dialog):
    input_dir = (ctx.bundle_input_field.value or "").strip() or ctx.default_input
    if not input_dir or not os.path.isdir(input_dir):
        ctx.show_snack_bar("⚠️ 輸入資料夾不存在")
        return
    ctx.show_snack_bar("🔍 預覽功能待實作")  # 保留對話框，避免丟掉使用者已填的設定
