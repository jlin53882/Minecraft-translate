"""Step 3 - 啟動翻譯對話框

用途：
- 選擇翻譯輸入資料夾（自動帶入整理後的待翻譯資料夾）
- 設定輸出目錄
- 設定執行選項（Dry Run、寫入新快取）
- API Key 不在此對話框輸入（請到設定頁的 API 與模型設定）
- 執行翻譯（背景執行緒 + 進度輪詢）
"""

import functools
import os
import types

import flet as ft

from app.services_impl.pipelines.extract_service import open_output_folder
from app.ui.design import C
from app.ui.dialogs import close_overlay_dialog
from app.ui.sync_text_field import SyncTextField
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
        input_path: 管線頁的 Mod 來源（本對話框不使用；翻譯目標由輸出根目錄推算）
        output_path: 管線頁的輸出根目錄；翻譯目標預設為
            ``{output}/locale_sort/_整理輸出/lang_output/<待翻譯整理資料夾>``，
            輸出預設為 ``{output}/lm_translate/<翻譯輸出子資料夾>``
        on_start_translate: 回調函式，簽名：
            on_start_translate(input_dir, output_dir, dry_run, write_new_cache)
        show_snack_bar: 回調：(message: str, color: str = C.RED) -> void
    """
    ctx = types.SimpleNamespace(
        page=page,
        file_picker=file_picker,
        on_start_translate=on_start_translate,
        show_snack_bar=show_snack_bar,
    )
    dialog_width = _translate_init_state_and_fields(ctx, input_path, output_path)
    _translate_build_option_widgets(ctx)
    content = _translate_build_content(ctx)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("🔄 啟動翻譯設定"),
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
                bgcolor=C.DIA,
                color=C.ON_EM,
                on_click=lambda e: ctx.start_translate(dialog),
            ),
        ],
    )

    ctx.page.overlay.append(dialog)
    dialog.open = True
    ctx.page.update()


def _translate_init_state_and_fields(ctx, input_path, output_path):
    """翻譯對話框的路徑、設定預設值與輸入欄位。"""
    dialog_width = int(ctx.page.width * 0.6)

    cfg = load_config()
    lang_merger_cfg = cfg.get("lang_merger", {})
    organized_folder = lang_merger_cfg.get(
        "pending_organized_folder_name", "待翻譯整理需翻譯"
    )

    translate_output_subfolder = lang_merger_cfg.get(
        "lm_translate_folder_name", "_翻譯輸出"
    )

    # 與 PipelineConfig 一致：語系合併把待翻譯清單輸出在 lang_output/ 底下；
    # 翻譯輸出 {output}/lm_translate/<子資料夾> 也是打包對話框的預設輸入
    ctx.default_input = (
        os.path.join(
            output_path, "locale_sort", "_整理輸出", "lang_output", organized_folder
        )
        if output_path
        else ""
    )
    ctx.default_output = (
        os.path.join(output_path, "lm_translate", translate_output_subfolder)
        if output_path
        else ""
    )

    ctx.translate_input_field = SyncTextField(
        label="翻譯目標",
        hint_text=f"自動帶入：{ctx.default_input}"
        if ctx.default_input
        else "留空自動帶入整理後的待翻譯資料夾",
        value=ctx.default_input,
        expand=True,
        border_color=C.DIA,
        path_input=True,
    )
    ctx.translate_output_field = SyncTextField(
        label="輸出目錄",
        hint_text=f"自動帶入：{ctx.default_output}"
        if ctx.default_output
        else "留空自動帶入 lm_translate/<翻譯輸出子資料夾>",
        value=ctx.default_output,
        expand=True,
        border_color=C.DIA,
        path_input=True,
    )

    ctx.dry_run_switch = ft.Switch(label="Dry Run（只分析不翻譯）", value=False)
    return dialog_width


def _translate_build_option_widgets(ctx) -> None:
    """開關、API 金鑰區與 handler 綁定。"""
    ctx.write_new_cache_switch = ft.Switch(
        label="寫入新快取（每次回傳單獨快取）", value=True
    )

    ctx.close_dialog = functools.partial(_translate_close_dialog, ctx)

    ctx.start_translate = functools.partial(_translate_start_translate, ctx)

    ctx.pick_input_dir = functools.partial(_translate_pick_input_dir, ctx)

    ctx.browse_input_dir = functools.partial(_translate_browse_input_dir, ctx)

    ctx.pick_output_dir = functools.partial(_translate_pick_output_dir, ctx)

    ctx.browse_output_dir = functools.partial(_translate_browse_output_dir, ctx)

    ctx.show_preview_result = functools.partial(_translate_show_preview_result, ctx)


def _translate_build_content(ctx):
    """對話框內容。"""

    content = ft.Column(
        [
            ft.Text("輸入來源", weight="bold", size=13),
            ft.Text("留空自動帶入前一步驟輸出", size=10, color=C.MUTED),
            ft.Row(
                [
                    ctx.translate_input_field,
                    ft.Button(
                        "選擇資料夾", icon=ft.Icons.FOLDER, on_click=ctx.pick_input_dir
                    ),
                    ft.Button(
                        "瀏覽", icon=ft.Icons.SEARCH, on_click=ctx.browse_input_dir
                    ),
                ]
            ),
            ft.Text("輸出目錄", weight="bold", size=13),
            ft.Text(
                "輸出說明：→ {output}/lm_translate/<翻譯輸出子資料夾>",
                size=10,
                color=C.MUTED,
            ),
            ft.Row(
                [
                    ctx.translate_output_field,
                    ft.Button(
                        "選擇資料夾",
                        icon=ft.Icons.FOLDER_SPECIAL,
                        on_click=ctx.pick_output_dir,
                    ),
                    ft.Button(
                        "瀏覽", icon=ft.Icons.SEARCH, on_click=ctx.browse_output_dir
                    ),
                ]
            ),
            ft.Divider(),
            ft.Text("執行選項", weight="bold", size=13),
            ctx.dry_run_switch,
            ctx.write_new_cache_switch,
        ],
        spacing=10,
        tight=False,
    )
    return content


def _translate_close_dialog(ctx, dialog):
    close_overlay_dialog(ctx.page, dialog)


def _translate_start_translate(ctx, dialog):
    input_dir = (ctx.translate_input_field.value or "").strip()
    output_dir = (ctx.translate_output_field.value or "").strip()

    if input_dir and not os.path.isdir(input_dir):
        ctx.show_snack_bar("⚠️ 翻譯目標資料夾不存在")
        return

    ctx.close_dialog(dialog)
    ctx.on_start_translate(
        input_dir=input_dir or ctx.default_input,
        output_dir=output_dir or ctx.default_output,
        dry_run=ctx.dry_run_switch.value,
        write_new_cache=ctx.write_new_cache_switch.value,
    )


def _translate_pick_input_dir(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.get_directory_path()
        if result:
            ctx.translate_input_field.value = result
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _translate_browse_input_dir(ctx, e=None):
    path = (ctx.translate_input_field.value or "").strip()
    if path and os.path.isdir(path):
        if not open_output_folder(path):
            ctx.show_snack_bar("⚠️ 無法開啟資料夾")
    elif not path:
        ctx.show_snack_bar("⚠️ 請先選擇資料夾")
    else:
        ctx.show_snack_bar("⚠️ 路徑不存在")


def _translate_pick_output_dir(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.get_directory_path()
        if result:
            ctx.translate_output_field.value = result
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _translate_browse_output_dir(ctx, e=None):
    path = (ctx.translate_output_field.value or "").strip()
    if path and os.path.isdir(path):
        if not open_output_folder(path):
            ctx.show_snack_bar("⚠️ 無法開啟資料夾")
    elif not path:
        ctx.show_snack_bar("⚠️ 請先選擇資料夾")
    else:
        ctx.show_snack_bar("⚠️ 路徑不存在")


def _translate_show_preview_result(ctx, dialog):
    input_dir = (ctx.translate_input_field.value or "").strip() or ctx.default_input
    if not input_dir or not os.path.isdir(input_dir):
        ctx.show_snack_bar("⚠️ 翻譯目標資料夾不存在")
        return
    ctx.show_snack_bar("🔍 預覽功能待實作")
    ctx.close_dialog(dialog)
