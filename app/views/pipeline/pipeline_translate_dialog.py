"""Step 3 - 啟動翻譯對話框

用途：
- 選擇翻譯輸入資料夾（自動帶入整理後的待翻譯資料夾）
- 設定輸出目錄
- 設定執行選項（Dry Run、寫入新快取）
- API Keys 可在對話框內臨時新增
- 執行翻譯（背景執行緒 + 進度輪詢）
"""

import flet as ft
import os

from app.ui.theme import (
    BLUE_600, BLUE_700, GREEN_700, TEAL_700, PURPLE_700,
    GREY_500, GREY_600, CYAN_700, GREEN_600,
    WHITE, BLUE_50, GREY_200, BLUE_400,
    GREEN_50, RED_400, RED_50,
)
from translation_tool.utils.config_manager import load_config


def open_translate_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str,
    output_path: str,
    on_start_translate,
    show_snack_bar,
):
    """打開啟動翻譯設定對話框。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: 預填的翻譯目標資料夾路徑
        output_path: 預填的輸出目錄路徑
        on_start_translate: 回調函式，簽名：
            on_start_translate(input_dir, output_dir, dry_run, write_new_cache, api_keys)
        show_snack_bar: 回調：(message: str, color: str = RED_400) -> void
    """
    dialog_width = int(page.width * 0.6)

    cfg = load_config()
    lang_merger_cfg = cfg.get("lang_merger", {})
    organized_folder = lang_merger_cfg.get("pending_organized_folder_name", "待翻譯整理需翻譯")

    default_input = os.path.join(output_path, "locale_sort", "_整理輸出", organized_folder) if output_path else ""
    default_output = os.path.join(output_path, "lm_translate") if output_path else ""

    translate_input_field = ft.TextField(
        label="翻譯目標",
        hint_text=f"自動帶入：{default_input}" if default_input else "留空自動帶入整理後的待翻譯資料夾",
        value=input_path or default_input,
        expand=True,
        border_color=BLUE_700,
    )
    translate_output_field = ft.TextField(
        label="輸出目錄",
        hint_text=f"自動帶入：{default_output}" if default_output else "留空自動帶入 lm_translate",
        value=output_path or default_output,
        expand=True,
        border_color=BLUE_700,
    )

    dry_run_switch = ft.Switch(label="Dry Run（只分析不翻譯）", value=False)
    write_new_cache_switch = ft.Switch(label="寫入新快取（每次回傳單獨快取）", value=True)

    api_keys_container = ft.Column(spacing=8)
    api_keys = []

    def add_key_field(initial_value=""):
        row = ft.Row(spacing=10)
        key_tf = ft.TextField(
            value=initial_value,
            hint_text="輸入 API Key",
            expand=True,
            text_size=12,
            border_color=BLUE_700,
            password=True,
        )
        del_btn = ft.IconButton(
            icon=ft.Icons.DELETE,
            icon_color=RED_400,
            on_click=lambda _: delete_key_field(row),
        )
        row.controls = [key_tf, del_btn]
        api_keys_container.controls.append(row)
        api_keys.append(key_tf)
        page.update()

    def delete_key_field(row):
        api_keys_container.controls.remove(row)
        if row.controls[0] in api_keys:
            api_keys.remove(row.controls[0])
        page.update()

    add_key_field()

    def close_dialog(dialog):
        dialog.open = False
        page.update()

    def start_translate(dialog):
        input_dir = (translate_input_field.value or "").strip()
        output_dir = (translate_output_field.value or "").strip()

        if input_dir and not os.path.isdir(input_dir):
            show_snack_bar("⚠️ 翻譯目標資料夾不存在")
            return

        close_dialog(dialog)
        on_start_translate(
            input_dir=input_dir or default_input,
            output_dir=output_dir or default_output,
            dry_run=dry_run_switch.value,
            write_new_cache=write_new_cache_switch.value,
        )

    def pick_input_dir(e=None):
        async def do_pick():
            result = await file_picker.get_directory_path()
            if result:
                translate_input_field.value = result
                page.update()
        page.run_task(do_pick)

    def browse_input_dir(e=None):
        path = (translate_input_field.value or "").strip()
        if path and os.path.isdir(path):
            os.startfile(path)
        elif not path:
            show_snack_bar("⚠️ 請先選擇資料夾")
        else:
            show_snack_bar("⚠️ 路徑不存在")

    def pick_output_dir(e=None):
        async def do_pick():
            result = await file_picker.get_directory_path()
            if result:
                translate_output_field.value = result
                page.update()
        page.run_task(do_pick)

    def browse_output_dir(e=None):
        path = (translate_output_field.value or "").strip()
        if path and os.path.isdir(path):
            os.startfile(path)
        elif not path:
            show_snack_bar("⚠️ 請先選擇資料夾")
        else:
            show_snack_bar("⚠️ 路徑不存在")

    def show_preview_result(dialog):
        input_dir = (translate_input_field.value or "").strip() or default_input
        if not input_dir or not os.path.isdir(input_dir):
            show_snack_bar("⚠️ 翻譯目標資料夾不存在")
            return
        show_snack_bar("🔍 預覽功能待實作")
        close_dialog(dialog)

    content = ft.Column([
        ft.Text("輸入來源", weight="bold", size=13),
        ft.Text("留空自動帶入前一步驟輸出", size=10, color=GREY_600),
        ft.Row([
            translate_input_field,
            ft.Button("選擇資料夾", icon=ft.Icons.FOLDER, on_click=pick_input_dir),
            ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_input_dir),
        ]),
        ft.Text("輸出目錄", weight="bold", size=13),
        ft.Text(f"輸出說明：→ {{output}}/lm_translate/", size=10, color=GREY_600),
        ft.Row([
            translate_output_field,
            ft.Button("選擇資料夾", icon=ft.Icons.FOLDER_SPECIAL, on_click=pick_output_dir),
            ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_output_dir),
        ]),
        ft.Divider(),
        ft.Text("執行選項", weight="bold", size=13),
        dry_run_switch,
        write_new_cache_switch,
    ], spacing=10, tight=False)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("🔄 啟動翻譯設定"),
        content=ft.Container(content=content, width=dialog_width),
        actions=[
            ft.TextButton("取消", on_click=lambda e: close_dialog(dialog)),
            ft.OutlinedButton("預覽結果", icon=ft.Icons.PREVIEW, on_click=lambda e: show_preview_result(dialog)),
            ft.Button("確定執行", icon=ft.Icons.CHECK, bgcolor=BLUE_700, color=WHITE,
                      on_click=lambda e: start_translate(dialog)),
        ],
    )

    page.overlay.append(dialog)
    dialog.open = True
    page.update()