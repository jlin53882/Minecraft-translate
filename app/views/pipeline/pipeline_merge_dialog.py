"""Step 2 - 語系比對對話框

用途：
- 選擇輸入來源（資料夾 或 ZIP 多選）
- 設定輸出目錄
- 設定語系過濾規則（only_lang、process_zh_cn、Patchouli 進階設定）
- 執行語系比對（背景執行緒 + 進度輪詢）
"""

import functools
import os
import sys  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import types
from pathlib import Path

import flet as ft

from app.services_impl.pipelines.extract_service import open_output_folder
from app.services_impl.pipelines.merge_service import (
    run_merge_zip_batch_service,  # noqa: F401
)
from app.tasks.task_session import TaskSession  # noqa: F401
from app.ui.design import C
from app.ui.dialogs import close_overlay_dialog
from app.ui.safe_file_picker import ensure_output_dir
from app.ui.sync_text_field import SyncTextField
from translation_tool.utils.config_manager import load_config


def _default_safe_int(s):
    """整數轉換；失敗回傳 None。"""
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _default_safe_float(s):
    """浮點數轉換；失敗回傳 None。"""
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def open_merge_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str,
    output_path: str,
    lang_code_checks: dict,
    on_run_merge,
    show_snack_bar,
    safe_int=None,
    safe_float=None,
):
    """建立可捲動內容區、並將操作列保留在視窗內的設定對話框。

    Args:
        page: 用來讀取視窗尺寸並顯示 Dialog 的 Flet Page。
        file_picker: 用於選擇來源 ZIP 與輸出目錄的檔案選擇器。
        input_path: 預填的 Mod 來源路徑。
        output_path: 預填的輸出目錄路徑。
        lang_code_checks: 由外層管理的語言代碼 Checkbox 對照表。
        on_run_merge: 驗證輸入後呼叫；參數依序為來源、輸出、模式、
            lang/zh_cn/Patchouli 選項與語言代碼。
        show_snack_bar: 顯示輸入驗證或預覽提示的回呼。
        safe_int: 可選的整數轉換器，失敗時回傳 None。
        safe_float: 可選的浮點數轉換器，失敗時回傳 None。

    Side Effects:
        將 AlertDialog 附加至 page.overlay、開啟對話框並更新頁面。
    """
    ctx = types.SimpleNamespace(
        page=page,
        file_picker=file_picker,
        lang_code_checks=lang_code_checks,
        on_run_merge=on_run_merge,
        show_snack_bar=show_snack_bar,
        safe_int=safe_int,
        safe_float=safe_float,
    )
    dialog_width, patchouli_skip, patchouli_threshold, zh_en_threshold = (
        _merge_init_state_and_config(ctx, input_path)
    )
    _merge_build_option_widgets(
        ctx, output_path, patchouli_skip, patchouli_threshold, zh_en_threshold
    )
    _merge_bind_handlers(ctx)
    _merge_build_zip_row(ctx)
    content = _merge_build_content(ctx)

    # 保留標題、內容間距與固定操作列所需的垂直空間。
    dialog_content_height = int(ctx.page.height * 0.62)
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("🔍 語系比對設定"),
        content=ft.Container(
            content=content,
            width=dialog_width,
            height=dialog_content_height,
        ),
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
                bgcolor=C.EM,
                color=C.ON_EM,
                on_click=lambda e: ctx.start_merge(dialog),
            ),
        ],
    )

    ctx.page.overlay.append(dialog)
    dialog.open = True
    ctx.page.update()


def _merge_init_state_and_config(ctx, input_path):
    """合併對話框的設定預設值、輸入欄位與狀態。"""
    if ctx.safe_int is None:
        ctx.safe_int = _default_safe_int
    if ctx.safe_float is None:
        ctx.safe_float = _default_safe_float

    dialog_width = int(ctx.page.width * 0.6)

    cfg = load_config()
    lang_merger_cfg = cfg.get("lang_merger", {})
    patchouli_skip = lang_merger_cfg.get(
        "patchouli_skip_en_us_when_zh_cn_exists", False
    )
    patchouli_threshold = str(
        lang_merger_cfg.get("patchouli_effective_translation_threshold", 0.5)
    )
    zh_en_threshold = str(lang_merger_cfg.get("zh_en_letter_threshold", 2))

    ctx.input_mode = "folder"
    ctx.merge_folder_field = SyncTextField(
        label="Mod 來源",
        hint_text=f"自動帶入：{input_path}" if input_path else "留空使用上方設定的路徑",
        value=input_path,
        expand=True,
        border_color=C.EM,
    )
    merge_zip_field = SyncTextField(  # noqa: F841
        label="Mod 來源（ZIP）",
        hint_text="選擇 ZIP 檔案（支援多選）",
        expand=True,
        border_color=C.EM,
        read_only=True,
        disabled=True,
        visible=False,
    )
    ctx.merge_zip_list_view = ft.ListView(
        expand=False,
        height=100,
        spacing=2,
    )
    ctx.merge_selected_zips = []
    return dialog_width, patchouli_skip, patchouli_threshold, zh_en_threshold


def _merge_build_option_widgets(
    ctx, output_path, patchouli_skip, patchouli_threshold, zh_en_threshold
) -> None:
    """輸出、僅 lang、zh_cn 與 Patchouli 選項控制項。"""
    ctx.merge_output_dir_field = SyncTextField(
        label="輸出目錄",
        hint_text=f"自動帶入：{output_path}"
        if output_path
        else "留空使用上方設定的輸出目錄",
        value=output_path,
        expand=True,
        border_color=C.EM,
    )
    ctx.merge_only_lang_checkbox = ft.Checkbox(label="只處理 lang 檔案", value=True)
    ctx.merge_process_zh_cn_switch = ft.Switch(label="處理 zh_cn 檔案", value=True)
    ctx.merge_patchouli_skip_switch = ft.Switch(
        label="允許 zh_cn 觸發跳過 en_us",
        value=patchouli_skip,
    )
    ctx.merge_patchouli_threshold_field = SyncTextField(
        value=patchouli_threshold,
        width=100,
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER,
        hint_text="空白用預設值",
    )
    ctx.merge_zh_en_threshold_field = SyncTextField(
        value=zh_en_threshold,
        width=80,
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER,
        hint_text="空白用預設值",
    )

    ctx.on_input_mode_changed = functools.partial(_merge_on_input_mode_changed, ctx)

    ctx.merge_input_mode_group = ft.RadioGroup(
        content=ft.Column(
            [
                ft.Radio(label="資料夾", value="folder"),
                ft.Radio(label="ZIP", value="zip"),
            ],
            spacing=4,
        ),
        value="folder",
        on_change=ctx.on_input_mode_changed,
    )

    ctx.close_dialog = functools.partial(_merge_close_dialog, ctx)


def _merge_bind_handlers(ctx) -> None:
    """Patchouli／zh_cn 連動與資料夾、ZIP 選取 handler。"""

    ctx.update_patchouli_controls = functools.partial(
        _merge_update_patchouli_controls, ctx
    )

    ctx.on_zh_cn_switch_changed = functools.partial(_merge_on_zh_cn_switch_changed, ctx)

    ctx.merge_process_zh_cn_switch.on_change = ctx.on_zh_cn_switch_changed
    ctx.update_patchouli_controls()

    ctx.pick_folder_input = functools.partial(_merge_pick_folder_input, ctx)

    ctx.pick_zip_input = functools.partial(_merge_pick_zip_input, ctx)

    ctx.refresh_zip_list = functools.partial(_merge_refresh_zip_list, ctx)

    ctx.remove_merge_zip = functools.partial(_merge_remove_merge_zip, ctx)

    ctx.browse_folder_input = functools.partial(_merge_browse_folder_input, ctx)

    ctx.pick_output_dir = functools.partial(_merge_pick_output_dir, ctx)

    ctx.browse_output_dir = functools.partial(_merge_browse_output_dir, ctx)

    ctx.show_preview_result = functools.partial(_merge_show_preview_result, ctx)

    ctx.start_merge = functools.partial(_merge_start_merge, ctx)

    ctx.folder_input_row = ft.Container(
        visible=True,
        content=ft.Row(
            [
                ctx.merge_folder_field,
                ft.Button(
                    "選擇資料夾", icon=ft.Icons.FOLDER, on_click=ctx.pick_folder_input
                ),
                ft.Button(
                    "瀏覽", icon=ft.Icons.SEARCH, on_click=ctx.browse_folder_input
                ),
            ]
        ),
    )


def _merge_build_zip_row(ctx) -> None:
    """ZIP 輸入列。"""
    ctx.zip_input_row = ft.Container(
        visible=False,
        content=ft.Column(
            [
                ft.Container(
                    content=ctx.merge_zip_list_view,
                    border=ft.Border.all(1, C.LINE),
                    border_radius=8,
                    padding=5,
                    expand=True,
                ),
                ft.Button(
                    "選擇 ZIP", icon=ft.Icons.FILE_OPEN, on_click=ctx.pick_zip_input
                ),
            ],
            spacing=4,
        ),
    )


def _merge_build_content(ctx):
    """對話框內容。"""

    content = ft.ListView(
        controls=[
            ft.Text("Mod 來源", weight="bold", size=13),
            ctx.merge_input_mode_group,
            ctx.folder_input_row,
            ctx.zip_input_row,
            ft.Text("輸出目錄", weight="bold", size=13),
            ft.Row(
                [
                    ctx.merge_output_dir_field,
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
            ft.Text("語系過濾設定", weight="bold", size=13),
            ctx.merge_only_lang_checkbox,
            ft.Row(
                [
                    ctx.merge_process_zh_cn_switch,
                    ft.Text(
                        "（需開啟才能調整下方 Patchouli 設定）", size=11, color=C.MUTED
                    ),
                ]
            ),
            ft.Divider(),
            ft.Text("zh 英文含量閾值", weight=ft.FontWeight.W_500, size=12),
            ft.Row(
                [
                    ctx.merge_zh_en_threshold_field,
                    ft.Text(
                        "超過此數值判定為英文，用於 lang 過濾，空白用預設值 2",
                        size=10,
                        color=C.MUTED,
                    ),
                ]
            ),
            ft.Divider(),
            ft.Text("Patchouli 進階設定", weight="bold", size=13),
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                "允許 zh_cn 觸發跳過 en_us",
                                weight=ft.FontWeight.W_500,
                                size=12,
                            ),
                            ctx.merge_patchouli_skip_switch,
                            ft.Text(
                                "當 zh_cn 翻譯足夠好時，跳過對應 en_us",
                                size=10,
                                color=C.MUTED,
                            ),
                        ],
                        expand=1,
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                "en_us 跳過門檻", weight=ft.FontWeight.W_500, size=12
                            ),
                            ctx.merge_patchouli_threshold_field,
                            ft.Text(
                                "有效翻譯比例 0.0~1.0，空白用預設值 0.5",
                                size=10,
                                color=C.MUTED,
                            ),
                        ],
                        expand=1,
                    ),
                ],
                spacing=8,
            ),
        ],
        spacing=10,
        padding=0,
        scroll=ft.ScrollMode.AUTO,
    )
    return content


def _merge_on_input_mode_changed(ctx, e=None):
    ctx.input_mode = ctx.merge_input_mode_group.value or "folder"
    folder_visible = ctx.input_mode == "folder"
    zip_visible = ctx.input_mode == "zip"
    ctx.folder_input_row.visible = folder_visible
    ctx.zip_input_row.visible = zip_visible
    ctx.page.update()


def _merge_close_dialog(ctx, dialog):
    close_overlay_dialog(ctx.page, dialog)


def _merge_update_patchouli_controls(ctx):
    enabled = bool(ctx.merge_process_zh_cn_switch.value)
    ctx.merge_patchouli_skip_switch.disabled = not enabled
    ctx.merge_patchouli_threshold_field.disabled = not enabled
    if not enabled:
        ctx.merge_patchouli_skip_switch.value = False


def _merge_on_zh_cn_switch_changed(ctx, e):
    ctx.update_patchouli_controls()
    ctx.page.update()


def _merge_pick_folder_input(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.get_directory_path()
        if result:
            ctx.merge_folder_field.value = result
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _merge_pick_zip_input(ctx, e=None):
    async def _async_pick_zip():
        # Flet 1.0：pick_files() 直接回傳選到的檔案（不會觸發 on_upload）
        result = await ctx.file_picker.pick_files(
            dialog_title="選擇 ZIP 檔案",
            allow_multiple=True,
            allowed_extensions=["zip"],
        )
        if not result:
            return
        for f in result if isinstance(result, list) else [result]:
            path = getattr(f, "path", None) or (f if isinstance(f, str) else None)
            if path and path not in ctx.merge_selected_zips:
                ctx.merge_selected_zips.append(path)
        ctx.refresh_zip_list()
        ctx.page.update()
        ctx.show_snack_bar(f"選了 {len(ctx.merge_selected_zips)} 個 ZIP")

    ctx.page.run_task(_async_pick_zip)


def _merge_refresh_zip_list(ctx):
    ctx.merge_zip_list_view.controls.clear()
    for path in ctx.merge_selected_zips:
        name = Path(path).name
        ctx.merge_zip_list_view.controls.append(
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text(name, expand=True, size=12),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        tooltip="移除",
                        icon_size=16,
                        on_click=lambda e, p=path: ctx.remove_merge_zip(p),
                    ),
                ],
            )
        )


def _merge_remove_merge_zip(ctx, path: str):
    if path in ctx.merge_selected_zips:
        ctx.merge_selected_zips.remove(path)
        ctx.refresh_zip_list()
        ctx.page.update()


def _merge_browse_folder_input(ctx, e=None):
    path = (ctx.merge_folder_field.value or "").strip()
    if path and os.path.isdir(path):
        if not open_output_folder(path):
            ctx.show_snack_bar("⚠️ 無法開啟資料夾")
    elif not path:
        ctx.show_snack_bar("⚠️ 請先選擇資料夾")
    else:
        ctx.show_snack_bar("⚠️ 路徑不存在")


def _merge_pick_output_dir(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.get_directory_path()
        if result:
            ctx.merge_output_dir_field.value = result
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _merge_browse_output_dir(ctx, e=None):
    path = (ctx.merge_output_dir_field.value or "").strip()
    if path and os.path.isdir(path):
        if not open_output_folder(path):
            ctx.show_snack_bar("⚠️ 無法開啟資料夾")
    elif not path:
        ctx.show_snack_bar("⚠️ 請先選擇資料夾")
    else:
        ctx.show_snack_bar("⚠️ 路徑不存在")


def _merge_show_preview_result(ctx, dialog):
    if ctx.input_mode == "folder":
        input_src = (ctx.merge_folder_field.value or "").strip()
    else:
        input_src = ",".join(ctx.merge_selected_zips)
    output = (ctx.merge_output_dir_field.value or "").strip()
    if not input_src:
        ctx.show_snack_bar("⚠️ 請填寫輸入來源")
        return
    if not output:
        ctx.show_snack_bar("⚠️ 請填寫輸出目錄")
        return
    ctx.show_snack_bar("🔍 預覽功能待實作")  # 保留對話框，避免丟掉使用者已填的設定


def _merge_start_merge(ctx, dialog):
    if ctx.input_mode == "folder":
        input_src = (ctx.merge_folder_field.value or "").strip()
        if not input_src:
            ctx.show_snack_bar("⚠️ 輸入來源為必填欄位")
            return
        if not os.path.isdir(input_src):
            ctx.show_snack_bar("⚠️ 輸入來源資料夾不存在")
            return
        merge_input = input_src
    else:
        if not ctx.merge_selected_zips:
            ctx.show_snack_bar("⚠️ 請選擇 ZIP 檔案")
            return
        merge_input = ctx.merge_selected_zips

    output = (ctx.merge_output_dir_field.value or "").strip()
    if not output:
        ctx.show_snack_bar("⚠️ 輸出目錄為必填欄位")
        return
    output_error = ensure_output_dir(output)
    if output_error:
        ctx.show_snack_bar(f"⚠️ {output_error}")
        return

    only_lang = ctx.merge_only_lang_checkbox.value
    process_zh_cn = ctx.merge_process_zh_cn_switch.value
    patchouli_skip_val = ctx.merge_patchouli_skip_switch.value
    # 0 是合法值；只有空白／格式錯誤才回到預設
    patchouli_threshold_val = ctx.safe_float(
        (ctx.merge_patchouli_threshold_field.value or "").strip()
    )
    if patchouli_threshold_val is None:
        patchouli_threshold_val = 0.5
    zh_en_val = ctx.safe_int((ctx.merge_zh_en_threshold_field.value or "").strip())
    if zh_en_val is None:
        zh_en_val = 2

    lang_codes = [code for code, cb in ctx.lang_code_checks.items() if cb.value]
    if not lang_codes:
        ctx.show_snack_bar("⚠️ 請至少選擇一個語言代碼")
        return

    ctx.close_dialog(dialog)
    ctx.on_run_merge(
        merge_input,
        output,
        ctx.input_mode,
        only_lang,
        process_zh_cn,
        patchouli_skip_val,
        patchouli_threshold_val,
        zh_en_val,
        lang_codes,
    )
