"""Step 2 - 語系比對對話框

用途：
- 選擇輸入來源（資料夾 或 ZIP 多選）
- 設定輸出目錄
- 設定語系過濾規則（only_lang、process_zh_cn、Patchouli 進階設定）
- 執行語系比對（背景執行緒 + 進度輪詢）
"""

import flet as ft
import threading
import time
import os
import sys
from pathlib import Path

from app.ui.theme import (
    BLUE_600, BLUE_700, GREEN_700, TEAL_700,
    GREY_500, GREY_600, CYAN_700, GREEN_600,
    WHITE, BLUE_50, GREY_200, BLUE_400,
    GREEN_50, RED_400, RED_50, RED_50,
)
from app.logging.task_session import TaskSession
from translation_tool.utils.config_manager import load_config, save_config
from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service


def open_merge_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str,
    output_path: str,
    lang_code_checks: dict,
    on_run_merge,
    show_snack_bar,
    safe_int,
    safe_float,
):
    """打開語系比對設定對話框。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: 預填的 Mod 來源路徑
        output_path: 預填的輸出目錄路徑
        lang_code_checks: dict[str, ft.Checkbox]，由外層管理
        on_run_merge: 回調函式，簽名：
            on_run_merge(merge_input, output_dir, only_lang, process_zh_cn,
                         patchouli_skip, patchouli_threshold, zh_en_threshold, lang_codes)
        show_snack_bar: 回調：(message: str, color: str = RED_400) -> void
        safe_int: 回調：str -> int | None
        safe_float: 回調：str -> float | None
    """
    dialog_width = int(page.width * 0.6)

    cfg = load_config()
    lang_merger_cfg = cfg.get("lang_merger", {})
    patchouli_skip = lang_merger_cfg.get("patchouli_skip_en_us_when_zh_cn_exists", False)
    patchouli_threshold = str(lang_merger_cfg.get("patchouli_effective_translation_threshold", 0.5))
    zh_en_threshold = str(lang_merger_cfg.get("zh_en_letter_threshold", 2))

    input_mode = "folder"
    merge_folder_field = ft.TextField(
        label="Mod 來源",
        hint_text=f"自動帶入：{input_path}" if input_path else "留空使用上方設定的路徑",
        value=input_path,
        expand=True,
        border_color=TEAL_700,
    )
    merge_zip_field = ft.TextField(
        label="Mod 來源（ZIP）",
        hint_text="選擇 ZIP 檔案（支援多選）",
        expand=True,
        border_color=TEAL_700,
        read_only=True,
        disabled=True,
        visible=False,
    )
    merge_zip_list_view = ft.ListView(
        expand=False,
        height=100,
        spacing=2,
    )
    merge_selected_zips = []
    merge_output_dir_field = ft.TextField(
        label="輸出目錄",
        hint_text=f"自動帶入：{output_path}" if output_path else "留空使用上方設定的輸出目錄",
        value=output_path,
        expand=True,
        border_color=TEAL_700,
    )
    merge_only_lang_checkbox = ft.Checkbox(label="只處理 lang 檔案", value=True)
    merge_process_zh_cn_switch = ft.Switch(label="處理 zh_cn 檔案", value=True)
    merge_patchouli_skip_switch = ft.Switch(
        label="允許 zh_cn 觸發跳過 en_us",
        value=patchouli_skip,
    )
    merge_patchouli_threshold_field = ft.TextField(
        value=patchouli_threshold,
        width=100,
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER,
        hint_text="空白用預設值",
    )
    merge_zh_en_threshold_field = ft.TextField(
        value=zh_en_threshold,
        width=80,
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER,
        hint_text="空白用預設值",
    )

    def on_input_mode_changed(e=None):
        nonlocal input_mode
        input_mode = merge_input_mode_group.value or "folder"
        folder_visible = input_mode == "folder"
        zip_visible = input_mode == "zip"
        folder_input_row.visible = folder_visible
        zip_input_row.visible = zip_visible
        page.update()

    merge_input_mode_group = ft.RadioGroup(
        content=ft.Column([
            ft.Radio(label="資料夾", value="folder"),
            ft.Radio(label="ZIP", value="zip"),
        ], spacing=4),
        value="folder",
        on_change=on_input_mode_changed,
    )

    def close_dialog(dialog):
        dialog.open = False
        page.update()

    def update_patchouli_controls():
        enabled = bool(merge_process_zh_cn_switch.value)
        merge_patchouli_skip_switch.disabled = not enabled
        merge_patchouli_threshold_field.disabled = not enabled
        if not enabled:
            merge_patchouli_skip_switch.value = False

    def on_zh_cn_switch_changed(e):
        update_patchouli_controls()
        page.update()

    merge_process_zh_cn_switch.on_change = on_zh_cn_switch_changed
    update_patchouli_controls()

    def pick_folder_input(e=None):
        async def do_pick():
            result = await file_picker.get_directory_path()
            if result:
                merge_folder_field.value = result
                page.update()
        page.run_task(do_pick)

    def pick_zip_input(e=None):
        show_snack_bar("DEBUG: pick_zip_input called")

        def on_zip_picked(e: ft.FilePickerUploadEvent):
            import sys
            print(f"[DEBUG] on_zip_picked fired! files={e.files}", flush=True, file=sys.stderr)
            if not e.files:
                return
            for f in e.files:
                if f.path and f.path not in merge_selected_zips:
                    merge_selected_zips.append(f.path)
            refresh_zip_list()
            page.update()
            show_snack_bar(f"選了 {len(merge_selected_zips)} 個 ZIP")

        file_picker.on_upload = on_zip_picked

        async def _async_pick_zip():
            await file_picker.pick_files(
                dialog_title="選擇 ZIP 檔案",
                allow_multiple=True,
                allowed_extensions=["zip"],
            )
        page.run_task(_async_pick_zip)

    def refresh_zip_list():
        print(f"[Step2] Refresh zip list, count={len(merge_selected_zips)}, list={merge_selected_zips}", flush=True, file=sys.stderr)
        merge_zip_list_view.controls.clear()
        for path in merge_selected_zips:
            name = Path(path).name
            print(f"[Step2] Adding zip row: {name}", flush=True, file=sys.stderr)
            merge_zip_list_view.controls.append(
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(name, expand=True, size=12),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            tooltip="移除",
                            icon_size=16,
                            on_click=lambda e, p=path: remove_merge_zip(p),
                        ),
                    ],
                )
            )

    def remove_merge_zip(path: str):
        if path in merge_selected_zips:
            merge_selected_zips.remove(path)
            refresh_zip_list()
            page.update()

    def browse_folder_input(e=None):
        path = (merge_folder_field.value or "").strip()
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
                merge_output_dir_field.value = result
                page.update()
        page.run_task(do_pick)

    def browse_output_dir(e=None):
        path = (merge_output_dir_field.value or "").strip()
        if path and os.path.isdir(path):
            os.startfile(path)
        elif not path:
            show_snack_bar("⚠️ 請先選擇資料夾")
        else:
            show_snack_bar("⚠️ 路徑不存在")

    def show_preview_result(dialog):
        if input_mode == "folder":
            input_src = (merge_folder_field.value or "").strip()
        else:
            input_src = ",".join(merge_selected_zips)
        output = (merge_output_dir_field.value or "").strip()
        if not input_src:
            show_snack_bar("⚠️ 請填寫輸入來源")
            return
        if not output:
            show_snack_bar("⚠️ 請填寫輸出目錄")
            return
        show_snack_bar("🔍 預覽功能待實作")
        close_dialog(dialog)

    def start_merge(dialog):
        if input_mode == "folder":
            input_src = (merge_folder_field.value or "").strip()
            if not input_src:
                show_snack_bar("⚠️ 輸入來源為必填欄位")
                return
            if not os.path.isdir(input_src):
                show_snack_bar("⚠️ 輸入來源資料夾不存在")
                return
            merge_input = input_src
        else:
            if not merge_selected_zips:
                show_snack_bar("⚠️ 請選擇 ZIP 檔案")
                return
            merge_input = merge_selected_zips

        output = (merge_output_dir_field.value or "").strip()
        if not output:
            show_snack_bar("⚠️ 輸出目錄為必填欄位")
            return
        if not os.path.isdir(output):
            show_snack_bar("⚠️ 輸出目錄不存在")
            return

        only_lang = merge_only_lang_checkbox.value
        process_zh_cn = merge_process_zh_cn_switch.value
        patchouli_skip_val = merge_patchouli_skip_switch.value
        patchouli_threshold_val = safe_float(
            (merge_patchouli_threshold_field.value or "").strip()
        ) or 0.5
        zh_en_val = safe_int(
            (merge_zh_en_threshold_field.value or "").strip()
        ) or 2

        lang_codes = [code for code, cb in lang_code_checks.items() if cb.value]
        if not lang_codes:
            show_snack_bar("⚠️ 請至少選擇一個語言代碼")
            return

        cfg = load_config()
        if "lang_merger" not in cfg:
            cfg["lang_merger"] = {}
        cfg["lang_merger"]["patchouli_skip_en_us_when_zh_cn_exists"] = patchouli_skip_val
        cfg["lang_merger"]["patchouli_effective_translation_threshold"] = patchouli_threshold_val
        cfg["lang_merger"]["zh_en_letter_threshold"] = zh_en_val
        save_config(cfg)

        close_dialog(dialog)
        on_run_merge(merge_input, output, only_lang, process_zh_cn, patchouli_skip_val,
                     patchouli_threshold_val, zh_en_val, lang_codes)

    folder_input_row = ft.Container(
        visible=True,
        content=ft.Row([
            merge_folder_field,
            ft.Button("選擇資料夾", icon=ft.Icons.FOLDER, on_click=pick_folder_input),
            ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_folder_input),
        ]),
    )
    zip_input_row = ft.Container(
        visible=False,
        content=ft.Column([
            ft.Container(
                content=merge_zip_list_view,
                border=ft.Border.all(1, "#4b5563"),
                border_radius=8,
                padding=5,
                expand=True,
            ),
            ft.Button("選擇 ZIP", icon=ft.Icons.FILE_OPEN, on_click=pick_zip_input),
        ], spacing=4),
    )

    content = ft.Column([
        ft.Text("Mod 來源", weight="bold", size=13),
        merge_input_mode_group,
        folder_input_row,
        zip_input_row,
        ft.Text("輸出目錄", weight="bold", size=13),
        ft.Row([
            merge_output_dir_field,
            ft.Button("選擇資料夾", icon=ft.Icons.FOLDER_SPECIAL, on_click=pick_output_dir),
            ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_output_dir),
        ]),
        ft.Divider(),
        ft.Text("語系過濾設定", weight="bold", size=13),
        merge_only_lang_checkbox,
        ft.Row([
            merge_process_zh_cn_switch,
            ft.Text("（需開啟才能調整下方 Patchouli 設定）", size=11, color=GREY_600),
        ]),
        ft.Divider(),
        ft.Text("zh 英文含量閾值", weight=ft.FontWeight.W_500, size=12),
        ft.Row([
            merge_zh_en_threshold_field,
            ft.Text("超過此數值判定為英文，用於 lang 過濾，空白用預設值 2", size=10, color=GREY_600),
        ]),
        ft.Divider(),
        ft.Text("Patchouli 進階設定", weight="bold", size=13),
        ft.Row(
            [
                ft.Column(
                    [
                        ft.Text("允許 zh_cn 觸發跳過 en_us", weight=ft.FontWeight.W_500, size=12),
                        merge_patchouli_skip_switch,
                        ft.Text("當 zh_cn 翻譯足夠好時，跳過對應 en_us", size=10, color=GREY_600),
                    ],
                    expand=1,
                ),
                ft.Column(
                    [
                        ft.Text("en_us 跳過門檻", weight=ft.FontWeight.W_500, size=12),
                        merge_patchouli_threshold_field,
                        ft.Text("有效翻譯比例 0.0~1.0，空白用預設值 0.5", size=10, color=GREY_600),
                    ],
                    expand=1,
                ),
            ],
            spacing=8,
        ),
    ], spacing=10, tight=False)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("🔍 語系比對設定"),
        content=ft.Container(content=content, width=dialog_width),
        actions=[
            ft.TextButton("取消", on_click=lambda e: close_dialog(dialog)),
            ft.OutlinedButton("預覽結果", icon=ft.Icons.PREVIEW, on_click=lambda e: show_preview_result(dialog)),
            ft.Button("確定執行", icon=ft.Icons.CHECK, bgcolor=TEAL_700, color=WHITE,
                      on_click=lambda e: start_merge(dialog)),
        ],
    )

    page.overlay.append(dialog)
    dialog.open = True
    page.update()