"""Step 1 - 抽取資源對話框

用途：
- 選擇 Mod 來源資料夾與輸出目錄
- 選擇執行模式（Lang / Book / 全部）
- 勾選要處理的語言代碼
- 預覽即將提取的 JAR 檔案統計
- 執行實際抽取（背景執行緒 + 進度輪詢）
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
    GREEN_50, RED_400, RED_50,
)
from app.logging.task_session import TaskSession
from translation_tool.utils.config_manager import load_config
from app.services_impl.pipelines.extract_service import (
    run_lang_extraction_service,
    run_book_extraction_service,
)
from app.views.extractor.extractor_state import PreviewState
from translation_tool.core.jar_processor import preview_extraction_generator, find_jar_files


def open_extract_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str,
    output_path: str,
    on_run_extraction,
    lang_code_checks: dict,
    show_snack_bar,
):
    """打開抽取資源設定對話框。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: 預填的 Mod 來源路徑
        output_path: 預填的輸出目錄路徑
        on_run_extraction: 回調函式，簽名：
            on_run_extraction(mods_dir, output_dir, mode, lang_codes)
        lang_code_checks: dict[str, ft.Checkbox]，由外層管理
        show_snack_bar: 回調：(message: str, color: str) -> void
    """
    dialog_width = int(page.width * 0.6)

    cfg = load_config()
    lang_codes = cfg.get("jar_extractor", {}).get("lang_codes", ["en_us", "zh_cn", "zh_tw"])
    folder_names = cfg.get("extractor", {}).get("output_folder_names", {})
    lang_extract = folder_names.get("lang_extract", "_提取lang_輸出")
    book_extract = folder_names.get("book_extract", "_提取book_輸出")
    dual_extract = folder_names.get("dual_extract", "_提取both_輸出")

    mods_field = ft.TextField(
        label="Mod 來源",
        hint_text=f"自動帶入：{input_path}" if input_path else "留空使用上方設定的 Mod 來源",
        value=input_path,
        expand=True,
        border_color=BLUE_700,
    )
    output_field = ft.TextField(
        label="輸出目錄",
        hint_text=f"自動帶入：{output_path}" if output_path else "留空使用上方設定的輸出目錄",
        value=output_path,
        expand=True,
        border_color=BLUE_700,
    )

    radio_group = ft.RadioGroup(
        content=ft.Column([
            ft.Radio(label="提取 Lang", value="lang"),
            ft.Radio(label="提取 Book", value="book"),
            ft.Radio(label="全部執行（Lang + Book）", value="both"),
        ], spacing=4),
        value="lang",
    )

    lang_code_checks_local = {}
    for code in lang_codes:
        lang_code_checks_local[code] = ft.Checkbox(label=code, value=True)

    def close_dialog(dialog):
        dialog.open = False
        page.update()

    def start_extraction(dialog):
        mods = (mods_field.value or "").strip()
        output = (output_field.value or "").strip()
        mode = radio_group.value
        if mode == "both":
            mode = "dual"

        if not mods:
            show_snack_bar("⚠️ Mod 來源為必填欄位")
            return
        if not os.path.isdir(mods):
            show_snack_bar("⚠️ Mod 來源資料夾不存在")
            return
        if not output:
            show_snack_bar("⚠️ 輸出目錄為必填欄位")
            return
        if not os.path.isdir(output):
            show_snack_bar("⚠️ 輸出目錄不存在")
            return

        selected_codes = [code for code, cb in lang_code_checks_local.items() if cb.value]
        close_dialog(dialog)

        for code in lang_codes:
            lang_code_checks[code] = lang_code_checks_local[code]
        on_run_extraction(mods, output, mode, lang_codes=selected_codes)

    def pick_mods_dir(e=None):
        async def do_pick():
            result = await file_picker.get_directory_path()
            if result:
                mods_field.value = result
                page.update()
        page.run_task(do_pick)

    def browse_mods_dir(e=None):
        path = (mods_field.value or "").strip()
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
                output_field.value = result
                page.update()
        page.run_task(do_pick)

    def browse_output_dir(e=None):
        path = (output_field.value or "").strip()
        if path and os.path.isdir(path):
            os.startfile(path)
        elif not path:
            show_snack_bar("⚠️ 請先選擇資料夾")
        else:
            show_snack_bar("⚠️ 路徑不存在")

    def show_preview_result(dialog):
        preview_dialog_width = int(page.width * 0.6)
        mods = (mods_field.value or "").strip()
        if not mods or not os.path.isdir(mods):
            show_snack_bar("⚠️ 請選擇有效的 Mod 來源")
            return

        mode = radio_group.value
        if mode == "both":
            mode = "dual"

        selected_codes = [code for code, cb in lang_code_checks_local.items() if cb.value]
        jar_files = find_jar_files(mods)
        total_jars = len(jar_files)

        preview_state = PreviewState()
        preview_state.total = total_jars
        preview_state.current = 0

        def do_preview():
            try:
                for update in preview_extraction_generator(mods, mode, lang_codes=selected_codes):
                    if 'error' in update:
                        preview_state.error = update['error']
                        preview_state.done = True
                        break
                    preview_state.progress = update.get('progress', 0)
                    preview_state.current = update.get('current', 0)
                    preview_state.total = update.get('total', 0)
                    if 'result' in update:
                        preview_state.result = update['result']
                        preview_state.done = True
            except Exception as ex:
                preview_state.error = str(ex)
                preview_state.done = True

        threading.Thread(target=do_preview, daemon=True).start()

        preview_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("預覽結果"),
            content=ft.Container(
                content=ft.Text(f"預覽掃描中...（0/{total_jars}）"),
                width=preview_dialog_width,
            ),
            actions=[ft.TextButton("取消", on_click=lambda e: close_preview_dialog(preview_dialog))],
        )

        def close_preview_dialog(d):
            d.open = False
            page.update()

        page.overlay.append(preview_dialog)
        preview_dialog.open = True
        page.update()

        def poll_preview():
            while not preview_state.done:
                time.sleep(0.2)
                async def do_update(_):
                    pct = int(preview_state.progress * 100)
                    preview_dialog.content = ft.Container(
                        content=ft.Text(f"預覽掃描中...（{preview_state.current}/{preview_state.total}）{pct}%"),
                        width=preview_dialog_width,
                    )
                    page.update()
                page.run_task(do_update, None)

            async def do_final(_):
                if preview_state.error:
                    preview_dialog.content = ft.Container(
                        content=ft.Text(f"❌ 錯誤：{preview_state.error}", color=RED_400),
                        width=preview_dialog_width,
                    )
                else:
                    result = preview_state.result
                    jar_count = total_jars
                    total_files = result.get('total_files', 0)
                    preview_results = result.get('preview_results', [])
                    total_size_mb = result.get('total_size_mb', 0)

                    list_items = []
                    for pr in preview_results:
                        jar_name = pr.get('jar', 'unknown')
                        if mode == "dual":
                            lang_count = pr.get('lang_count', 0)
                            book_count = pr.get('book_count', 0)
                            list_items.append(ft.Text(f"  {jar_name}（Lang: {lang_count}, Book: {book_count}）", size=12))
                        else:
                            count = pr.get('count', 0)
                            list_items.append(ft.Text(f"  {jar_name}（{count} 個檔案）", size=12))

                    preview_dialog.content = ft.Container(
                        width=preview_dialog_width,
                        content=ft.Column([
                            ft.Text(f"JAR 數量：{jar_count} 個"),
                            ft.Text(f"預計提取：{total_files} 個檔案（約 {total_size_mb:.1f} MB）"),
                            ft.Divider(),
                            ft.Text("詳細清單：", weight="bold"),
                            ft.ListView(
                                controls=list_items,
                                expand=True,
                            ),
                        ], tight=False),
                    )
                preview_dialog.actions = [ft.TextButton("確定", on_click=lambda e: close_preview_dialog(preview_dialog))]
                page.update()
            page.run_task(do_final, None)

        threading.Thread(target=poll_preview, daemon=True).start()

    lang_codes_section = ft.Column([lang_code_checks_local[code] for code in lang_codes], spacing=2)

    content = ft.Column([
        ft.Text("Mod 來源", weight="bold", size=13),
        ft.Row([
            mods_field,
            ft.Button("選擇資料夾", icon=ft.Icons.FOLDER, on_click=pick_mods_dir),
            ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_mods_dir),
        ]),
        ft.Text("輸出目錄", weight="bold", size=13),
        ft.Row([
            output_field,
            ft.Button("選擇資料夾", icon=ft.Icons.FOLDER_SPECIAL, on_click=pick_output_dir),
            ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_output_dir),
        ]),
        ft.Text("執行模式", weight="bold", size=13),
        radio_group,
        ft.Text("處理的語言代碼", weight="bold", size=13),
        lang_codes_section,
    ], spacing=10, tight=False)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("📦 抽取資源設定"),
        content=ft.Container(content=content, width=dialog_width),
        actions=[
            ft.TextButton("取消", on_click=lambda e: close_dialog(dialog)),
            ft.OutlinedButton("預覽結果", icon=ft.Icons.PREVIEW, on_click=lambda e: show_preview_result(dialog)),
            ft.Button("確定執行", icon=ft.Icons.CHECK, bgcolor=GREEN_700, color=WHITE,
                      on_click=lambda e: start_extraction(dialog)),
        ],
    )

    page.overlay.append(dialog)
    dialog.open = True
    page.update()