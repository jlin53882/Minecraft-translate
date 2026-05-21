"""Step 4 - 打包資源對話框

用途：
- 選擇翻譯後的輸入資料夾（自動帶入 lm_translate 輸出）
- 設定輸出 ZIP 檔案路徑
- 設定檔案敘述、Minecraft 版本、封面圖片
- 新增其他指定資料夾
- 執行打包（背景執行緒 + 進度輪詢）
"""

import flet as ft
import os
import json

from app.ui.theme import (
    BLUE_600, BLUE_700, GREEN_700, TEAL_700, PURPLE_700,
    GREY_500, GREY_600, CYAN_700, GREEN_600,
    WHITE, BLUE_50, GREY_200, BLUE_400,
    GREEN_50, RED_400, RED_50,
)
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
        except Exception:
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
    dialog_width = int(page.width * 0.6)

    cfg = load_config()
    bundler_cfg = cfg.get("output_bundler", {})
    output_zip_name = bundler_cfg.get("output_zip_name", "可使用翻譯.zip")
    lang_merger_cfg = cfg.get("lang_merger", {})
    translate_output_subfolder = lang_merger_cfg.get("lm_translate_folder_name", "_翻譯輸出")

    default_input = os.path.join(output_path, "lm_translate", translate_output_subfolder) if output_path else ""
    default_output_zip = os.path.join(output_path, output_zip_name) if output_path else ""

    bundle_input_field = ft.TextField(
        label="輸入來源",
        hint_text=f"自動帶入：{default_input}" if default_input else "留空自動帶入翻譯完成後的輸出",
        value=input_path or default_input,
        expand=True,
        border_color=PURPLE_700,
    )
    bundle_output_zip_field = ft.TextField(
        label="輸出 ZIP 檔案",
        hint_text=f"自動帶入：{default_output_zip}" if default_output_zip else "留空自動帶入可使用翻譯.zip",
        value="",
        expand=True,
        border_color=PURPLE_700,
    )
    description_field = ft.TextField(
        label="檔案敘述",
        hint_text="直接輸入文字，或使用 § 顏色代碼",
        expand=True,
        border_color=PURPLE_700,
    )

    version_data = _load_version_data()
    version_list = ft.ListView(expand=True, height=160, spacing=4, auto_scroll=False)
    version_expanded = False

    version_toggle_label = ft.Text("", size=12, expand=True)
    version_search = ft.TextField(
        label="搜尋版本",
        hint_text="輸入版本關鍵字...",
        expand=True,
        border_color=PURPLE_700,
        dense=True,
        on_change=lambda e: _refresh_version_list(e.control.value or ""),
    )

    def _refresh_version_list(search_text: str):
        version_list.controls.clear()
        filtered = [v for v in version_data.keys() if search_text.lower() in v.lower()]
        if not filtered:
            version_list.controls.append(
                ft.Text("無可用版本", size=12, color=GREY_500)
            )
        for version_key in filtered:
            item = ft.Container(
                content=ft.Text(version_key, size=13),
                padding=8,
                border=ft.Border.all(1, GREY_500),
                border_radius=6,
                on_click=lambda e, v=version_key: _select_version(v),
            )
            version_list.controls.append(item)
        page.update()

    def _select_version(version: str):
        version_toggle_label.value = version
        page.update()

    _refresh_version_list("")

    def _toggle_version_expand(e=None):
        nonlocal version_expanded
        version_expanded = not version_expanded
        version_dropdown_container.visible = version_expanded
        page.update()

    version_dropdown_container = ft.Container(
        content=version_list,
        height=160,
        border=ft.Border.all(1, GREY_500),
        border_radius=6,
        padding=4,
        visible=False,
    )

    pack_image_field = ft.TextField(
        label="封面圖片（可留空）",
        hint_text="選擇 pack.png 圖片",
        expand=True,
        border_color=PURPLE_700,
        read_only=True,
    )
    extra_folders_view = ft.ListView(height=80, spacing=4, auto_scroll=False)
    extra_folders: list[str] = []

    def close_dialog(dialog):
        dialog.open = False
        page.update()

    def start_bundle(dialog):
        input_dir = (bundle_input_field.value or "").strip()
        output_zip = (bundle_output_zip_field.value or "").strip()

        if input_dir and not os.path.isdir(input_dir):
            show_snack_bar("⚠️ 輸入資料夾不存在")
            return
        if not output_zip:
            show_snack_bar("⚠️ 輸出 ZIP 檔名不可空白")
            return

        pack_img = (pack_image_field.value or "").strip()
        if pack_img and not os.path.isfile(pack_img):
            ext = os.path.splitext(pack_img)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg"):
                show_snack_bar("⚠️ 封面圖片只支援 .png/.jpg")
                return

        close_dialog(dialog)
        on_start_bundle(
            input_root_dir=input_dir or default_input,
            output_zip_path=output_zip or default_output_zip,
            description=(description_field.value or "").strip(),
            min_format=None,
            max_format=None,
            pack_image_path=pack_img or None,
            extra_folders=list(extra_folders),
        )

    def pick_input_dir(e=None):
        async def do_pick():
            result = await file_picker.get_directory_path()
            if result:
                bundle_input_field.value = result
                page.update()
        page.run_task(do_pick)

    def browse_input_dir(e=None):
        path = (bundle_input_field.value or "").strip()
        if path and os.path.isdir(path):
            os.startfile(path)
        elif not path:
            show_snack_bar("⚠️ 請先選擇資料夾")
        else:
            show_snack_bar("⚠️ 路徑不存在")

    def pick_output_zip(e=None):
        async def do_pick():
            result = await file_picker.get_directory_path()
            if result:
                bundle_output_zip_field.value = result
                page.update()
        page.run_task(do_pick)

    def pick_pack_image(e=None):
        async def do_pick():
            result = await file_picker.pick_files(
                dialog_title="選擇封面圖片",
                allowed_extensions=["png", "jpg", "jpeg"],
            )
            if result and result.files:
                pack_image_field.value = result.files[0].path
                page.update()
        page.run_task(do_pick)

    def add_extra_folder(e=None):
        async def do_pick():
            result = await file_picker.get_directory_path()
            if result and result not in extra_folders:
                extra_folders.append(result)
                _refresh_extra_folders()
                page.update()
        page.run_task(do_pick)

    def _refresh_extra_folders():
        extra_folders_view.controls.clear()
        for path in extra_folders:
            name = os.path.basename(path)
            extra_folders_view.controls.append(
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(f"{name}", expand=True, size=12),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            tooltip="移除",
                            icon_size=16,
                            on_click=lambda e, p=path: _remove_extra_folder(p),
                        ),
                    ],
                )
            )

    def _remove_extra_folder(path: str):
        if path in extra_folders:
            extra_folders.remove(path)
            _refresh_extra_folders()
            page.update()

    def show_preview_result(dialog):
        input_dir = (bundle_input_field.value or "").strip() or default_input
        if not input_dir or not os.path.isdir(input_dir):
            show_snack_bar("⚠️ 輸入資料夾不存在")
            return
        show_snack_bar("🔍 預覽功能待實作")
        close_dialog(dialog)

    content = ft.Column([
        ft.Text("輸入來源", weight="bold", size=13),
        ft.Text("留空自動帶入，翻譯完成後再使用", size=10, color=GREY_600),
        ft.Row([
            bundle_input_field,
            ft.Button("選擇資料夾", icon=ft.Icons.FOLDER, on_click=pick_input_dir),
            ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_input_dir),
        ]),
        ft.Text("輸出 ZIP 檔案", weight="bold", size=13),
        ft.Row([
            bundle_output_zip_field,
            ft.Button("選擇儲存位置", icon=ft.Icons.SAVE, on_click=pick_output_zip),
        ]),
        ft.Text("檔案敘述", weight="bold", size=13),
        description_field,
        ft.Text("Minecraft 版本", weight="bold", size=13),
        version_search,
        ft.Container(
            content=ft.Row([
                ft.Text("已選擇：", size=11, color=GREY_600),
                version_toggle_label,
                ft.Icon(ft.Icons.EXPAND_MORE, size=18),
            ]),
            padding=8,
            border=ft.Border.all(1, GREY_500),
            border_radius=6,
            on_click=_toggle_version_expand,
        ),
        version_dropdown_container,
        ft.Text("封面圖片（可留空）", weight="bold", size=13),
        ft.Row([
            pack_image_field,
            ft.Button("選擇檔案...", icon=ft.Icons.IMAGE, on_click=pick_pack_image),
            ft.Button("移除", icon=ft.Icons.DELETE, on_click=lambda e: setattr(pack_image_field, 'value', '') or page.update()),
        ]),
        ft.Text("其他指定資料夾", weight="bold", size=13),
        ft.Container(content=extra_folders_view, border=ft.Border.all(1, GREY_500), border_radius=8, padding=4),
        ft.Button("+ 新增資料夾", icon=ft.Icons.FOLDER_OPEN, on_click=add_extra_folder),
    ], spacing=10, tight=False)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("📦 打包資源設定"),
        content=ft.Container(content=content, width=dialog_width),
        actions=[
            ft.TextButton("取消", on_click=lambda e: close_dialog(dialog)),
            ft.OutlinedButton("預覽結果", icon=ft.Icons.PREVIEW, on_click=lambda e: show_preview_result(dialog)),
            ft.Button("確定執行", icon=ft.Icons.CHECK, bgcolor=PURPLE_700, color=WHITE,
                      on_click=lambda e: start_bundle(dialog)),
        ],
    )

    page.overlay.append(dialog)
    dialog.open = True
    page.update()