"""Step 1 - 抽取資源對話框

用途：
- 選擇 Mod 來源資料夾與輸出目錄
- 選擇執行模式（Lang / Book / 全部）
- 勾選要處理的語言代碼
- 預覽即將提取的 JAR 檔案統計
- 執行實際抽取（背景執行緒 + 進度輪詢）
"""

import asyncio
import functools
import os
import threading
import types

import flet as ft

from app.services_impl.pipelines.extract_service import (
    find_jar_files,
    open_output_folder,
    preview_extraction_generator,
)
from app.ui.design import C
from app.ui.dialogs import close_overlay_dialog
from app.views.extractor.extractor_state import PreviewState
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_error
from translation_tool.utils.ui_mirror import in_new_task


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
    ctx = types.SimpleNamespace(
        page=page,
        file_picker=file_picker,
        on_run_extraction=on_run_extraction,
        lang_code_checks=lang_code_checks,
        show_snack_bar=show_snack_bar,
    )
    dialog_width = _extract_init_state_and_fields(ctx, input_path, output_path)
    lang_codes_section = _extract_build_lang_codes_section(ctx)
    content = _extract_build_content(ctx, lang_codes_section)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("📦 抽取資源設定"),
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
                bgcolor=C.EM,
                color=C.ON_EM,
                on_click=lambda e: ctx.start_extraction(dialog),
            ),
        ],
    )

    ctx.page.overlay.append(dialog)
    dialog.open = True
    ctx.page.update()


def _extract_init_state_and_fields(ctx, input_path, output_path):
    """提取對話框的路徑、設定預設值與輸入欄位。"""
    dialog_width = int(ctx.page.width * 0.6)

    cfg = load_config()
    ctx.lang_codes = cfg.get("jar_extractor", {}).get(
        "lang_codes", ["en_us", "zh_cn", "zh_tw"]
    )

    ctx.mods_field = ft.TextField(
        label="Mod 來源",
        hint_text=f"自動帶入：{input_path}"
        if input_path
        else "留空使用上方設定的 Mod 來源",
        value=input_path,
        expand=True,
        border_color=C.DIA,
    )
    ctx.output_field = ft.TextField(
        label="輸出目錄",
        hint_text=f"自動帶入：{output_path}"
        if output_path
        else "留空使用上方設定的輸出目錄",
        value=output_path,
        expand=True,
        border_color=C.DIA,
    )

    ctx.radio_group = ft.RadioGroup(
        content=ft.Column(
            [
                ft.Radio(label="提取 Lang", value="lang"),
                ft.Radio(label="提取 Book", value="book"),
                ft.Radio(label="全部執行（Lang + Book）", value="both"),
            ],
            spacing=4,
        ),
        value="lang",
    )
    return dialog_width


def _extract_build_lang_codes_section(ctx):
    """語系代碼區與 handler 綁定。"""

    ctx.lang_code_checks_local = {}
    for code in ctx.lang_codes:
        ctx.lang_code_checks_local[code] = ft.Checkbox(label=code, value=True)

    ctx.close_dialog = functools.partial(_extract_close_dialog, ctx)

    ctx.start_extraction = functools.partial(_extract_start_extraction, ctx)

    ctx.pick_mods_dir = functools.partial(_extract_pick_mods_dir, ctx)

    ctx.browse_mods_dir = functools.partial(_extract_browse_mods_dir, ctx)

    ctx.pick_output_dir = functools.partial(_extract_pick_output_dir, ctx)

    ctx.browse_output_dir = functools.partial(_extract_browse_output_dir, ctx)

    ctx.show_preview_result = functools.partial(_extract_show_preview_result, ctx)

    lang_codes_section = ft.Column(
        [ctx.lang_code_checks_local[code] for code in ctx.lang_codes], spacing=2
    )
    return lang_codes_section


def _extract_build_content(ctx, lang_codes_section):
    """對話框內容。"""

    content = ft.Column(
        [
            ft.Text("Mod 來源", weight="bold", size=13),
            ft.Row(
                [
                    ctx.mods_field,
                    ft.Button(
                        "選擇資料夾", icon=ft.Icons.FOLDER, on_click=ctx.pick_mods_dir
                    ),
                    ft.Button(
                        "瀏覽", icon=ft.Icons.SEARCH, on_click=ctx.browse_mods_dir
                    ),
                ]
            ),
            ft.Text("輸出目錄", weight="bold", size=13),
            ft.Row(
                [
                    ctx.output_field,
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
            ft.Text("執行模式", weight="bold", size=13),
            ctx.radio_group,
            ft.Text("處理的語言代碼", weight="bold", size=13),
            lang_codes_section,
        ],
        spacing=10,
        tight=False,
    )
    return content


def _extract_close_dialog(ctx, dialog):
    close_overlay_dialog(ctx.page, dialog)


def _extract_start_extraction(ctx, dialog):
    mods = (ctx.mods_field.value or "").strip()
    output = (ctx.output_field.value or "").strip()
    mode = ctx.radio_group.value
    if mode == "both":
        mode = "dual"

    if not mods:
        ctx.show_snack_bar("⚠️ Mod 來源為必填欄位")
        return
    if not os.path.isdir(mods):
        ctx.show_snack_bar("⚠️ Mod 來源資料夾不存在")
        return
    if not output:
        ctx.show_snack_bar("⚠️ 輸出目錄為必填欄位")
        return
    if not os.path.isdir(output):
        ctx.show_snack_bar("⚠️ 輸出目錄不存在")
        return

    selected_codes = [
        code for code, cb in ctx.lang_code_checks_local.items() if cb.value
    ]
    if ctx.lang_codes and not selected_codes:
        ctx.show_snack_bar("⚠️ 請至少選擇一個語言代碼")
        return
    ctx.close_dialog(dialog)

    for code in ctx.lang_codes:
        ctx.lang_code_checks[code] = ctx.lang_code_checks_local[code]
    ctx.on_run_extraction(mods, output, mode, lang_codes=selected_codes)


def _extract_pick_mods_dir(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.get_directory_path()
        if result:
            ctx.mods_field.value = result
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _extract_browse_mods_dir(ctx, e=None):
    path = (ctx.mods_field.value or "").strip()
    if path and os.path.isdir(path):
        if not open_output_folder(path):
            ctx.show_snack_bar("⚠️ 無法開啟資料夾")
    elif not path:
        ctx.show_snack_bar("⚠️ 請先選擇資料夾")
    else:
        ctx.show_snack_bar("⚠️ 路徑不存在")


def _extract_pick_output_dir(ctx, e=None):
    async def do_pick():
        result = await ctx.file_picker.get_directory_path()
        if result:
            ctx.output_field.value = result
            ctx.page.update()

    ctx.page.run_task(do_pick)


def _extract_browse_output_dir(ctx, e=None):
    path = (ctx.output_field.value or "").strip()
    if path and os.path.isdir(path):
        if not open_output_folder(path):
            ctx.show_snack_bar("⚠️ 無法開啟資料夾")
    elif not path:
        ctx.show_snack_bar("⚠️ 請先選擇資料夾")
    else:
        ctx.show_snack_bar("⚠️ 路徑不存在")


def _extract_preview_worker(
    preview_state, mods, mode, selected_codes, cancel_event=None
) -> None:
    """背景執行緒：跑預覽 generator，只寫入 preview_state（不碰任何控制項）。

    JAR 探索（``find_jar_files``，會走訪資料夾）也在這裡執行，不佔用 event loop。
    ``cancel_event`` 被設定（使用者按「取消」）時在下一個檢查點就停止，結果直接丟棄。
    """
    try:
        jar_files = find_jar_files(mods)
        preview_state.total = len(jar_files)
        if cancel_event is not None and cancel_event.is_set():
            return
        for update in preview_extraction_generator(
            mods, mode, lang_codes=selected_codes
        ):
            if cancel_event is not None and cancel_event.is_set():
                break
            if "error" in update:
                preview_state.error = update["error"]
                break
            preview_state.progress = update.get("progress", 0)
            preview_state.current = update.get("current", 0)
            # 沒帶 total 的更新（例如最後的 result）不能把「JAR 探索」得到的總數蓋成 0
            preview_state.total = update.get("total", preview_state.total)
            if "result" in update:
                preview_state.result = update["result"]
    except Exception as ex:  # noqa: BLE001 - 錯誤要顯示在對話框
        log_error(f"[Pipeline] 提取預覽失敗：{ex!r}", exc_info=True)
        preview_state.error = str(ex)
    finally:
        # 不論結果如何都標記完成，避免輪詢永遠不結束
        preview_state.done = True


def _extract_preview_result_content(
    result: dict, mode: str, jar_count: int, width: int
) -> ft.Container:
    """預覽完成後的結果內容（只列出有可提取檔案的 JAR）。"""
    total_files = result.get("total_files", 0)
    preview_results = result.get("preview_results", [])
    total_size_mb = result.get("total_size_mb", 0)

    list_items = []

    def _count(pr):
        if mode == "dual":
            return (pr.get("lang_count", 0) or 0) + (pr.get("book_count", 0) or 0)
        return pr.get("count", 0) or 0

    empty_count = sum(1 for pr in preview_results if _count(pr) == 0)
    preview_results = [pr for pr in preview_results if _count(pr) > 0]
    if empty_count:
        list_items.append(
            ft.Text(
                f"  另有 {empty_count} 個 JAR 沒有可提取的檔案，已略過不列出",
                size=12,
                color=C.MUTED,
            )
        )
    for pr in preview_results:
        jar_name = pr.get("jar", "unknown")
        if mode == "dual":
            lang_count = pr.get("lang_count", 0)
            book_count = pr.get("book_count", 0)
            list_items.append(
                ft.Text(
                    f"  {jar_name}（Lang: {lang_count}, Book: {book_count}）",
                    size=12,
                )
            )
        else:
            count = pr.get("count", 0)
            list_items.append(ft.Text(f"  {jar_name}（{count} 個檔案）", size=12))

    return ft.Container(
        width=width,
        content=ft.Column(
            [
                ft.Text(f"JAR 數量：{jar_count} 個"),
                ft.Text(f"預計提取：{total_files} 個檔案（約 {total_size_mb:.1f} MB）"),
                ft.Divider(),
                ft.Text("詳細清單：", weight="bold"),
                ft.ListView(
                    controls=list_items,
                    expand=True,
                ),
            ],
            tight=False,
        ),
    )


def _extract_close_preview_dialog(ctx, d, cancel_event=None) -> None:
    """關閉預覽對話框；掃描中按「取消」時一併中止背景掃描。"""
    if cancel_event is not None:
        cancel_event.set()
    close_overlay_dialog(ctx.page, d)


def _extract_preview_apply_final(
    ctx, preview_dialog, preview_state, mode, width
) -> None:
    """（event loop 上）把預覽結果或錯誤套用到對話框。"""
    if preview_state.error:
        preview_dialog.content = ft.Container(
            content=ft.Text(f"❌ 錯誤：{preview_state.error}", color=C.RED),
            width=width,
        )
    else:
        preview_dialog.content = _extract_preview_result_content(
            preview_state.result or {}, mode, preview_state.total, width
        )
    preview_dialog.actions = [
        ft.TextButton(
            "確定",
            on_click=lambda e: _extract_close_preview_dialog(ctx, preview_dialog),
        )
    ]
    ctx.page.update()


async def _extract_preview_poll(
    ctx, preview_dialog, preview_state, mode, width, cancel_event=None
) -> None:
    """在 event loop 上輪詢預覽進度；背景執行緒不直接碰控制項。

    使用者取消（``cancel_event``）後立即結束輪詢，不再改動已關閉的對話框。
    """
    while not preview_state.done:
        if cancel_event is not None and cancel_event.is_set():
            return
        await asyncio.sleep(0.2)
        if cancel_event is not None and cancel_event.is_set():
            return
        pct = int(preview_state.progress * 100)
        preview_dialog.content = ft.Container(
            content=ft.Text(
                f"預覽掃描中...（{preview_state.current}/{preview_state.total}）{pct}%"
            ),
            width=width,
        )
        ctx.page.update()
    if cancel_event is not None and cancel_event.is_set():
        return
    _extract_preview_apply_final(ctx, preview_dialog, preview_state, mode, width)


def _extract_show_preview_result(ctx, dialog):
    preview_dialog_width = int(ctx.page.width * 0.6)
    mods = (ctx.mods_field.value or "").strip()
    if not mods or not os.path.isdir(mods):
        ctx.show_snack_bar("⚠️ 請選擇有效的 Mod 來源")
        return

    mode = ctx.radio_group.value
    if mode == "both":
        mode = "dual"

    selected_codes = [
        code for code, cb in ctx.lang_code_checks_local.items() if cb.value
    ]

    preview_state = PreviewState()
    preview_state.total = 0
    preview_state.current = 0
    cancel_event = threading.Event()

    threading.Thread(
        target=in_new_task(
            "pipeline-extract-preview",
            functools.partial(
                _extract_preview_worker,
                preview_state,
                mods,
                mode,
                selected_codes,
                cancel_event,
            ),
        ),
        daemon=True,
    ).start()

    preview_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("預覽結果"),
        content=ft.Container(
            content=ft.Text("正在搜尋 JAR..."),
            width=preview_dialog_width,
        ),
        actions=[
            ft.TextButton(
                "取消",
                on_click=lambda e: _extract_close_preview_dialog(
                    ctx, preview_dialog, cancel_event
                ),
            )
        ],
    )

    ctx.page.overlay.append(preview_dialog)
    preview_dialog.open = True
    ctx.page.update()

    async def poll_preview():
        await _extract_preview_poll(
            ctx,
            preview_dialog,
            preview_state,
            mode,
            preview_dialog_width,
            cancel_event,
        )

    ctx.page.run_task(poll_preview)
