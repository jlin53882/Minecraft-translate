"""Extractor 完整提取對話框

用途：
- 點擊提取/預覽按鈕時打開的完整對話框
- 包含：進度條 + 日誌 + 結果統計
- 直接使用外部傳入的設定，無需重新設定

使用方式：
    from app.views.extractor.extractor_dialog import open_extractor_dialog
    open_extractor_dialog(page, file_picker, input_path="...", output_path="...", on_complete=..., mode="lang")
"""

import flet as ft
import threading
import os
from pathlib import Path

from app.ui import theme
from translation_tool.utils.config_manager import load_config
from app.services_impl.pipelines.extract_service import (
    run_lang_extraction_service,
    run_book_extraction_service,
)
from translation_tool.core.jar_processor import (
    preview_extraction_generator,
    find_jar_files,
    extract_dual_files_generator,
    extract_lang_files_generator,
    extract_book_files_generator,
)


def open_extractor_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str = "",
    output_path: str = "",
    on_complete=None,
    mode: str = "lang",  # "lang", "book", "dual"
):
    """打開完整的提取對話框（進度+日誌+結果）。

    直接使用外部傳入的設定，無需重新設定。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: Mod 來源路徑
        output_path: 輸出目錄路徑
        on_complete: 完成後的回調函式 (可選)
        mode: 提取模式 ("lang", "book", "dual")
    """
    dialog_width = max(600, int(page.width * 0.7))

    # 從外部取得設定
    mods_dir = input_path
    output_dir = output_path  # 可為空

    # 如果未指定輸出目錄，使用 Mod 來源目錄
    if not output_dir:
        output_dir = mods_dir

    # 讀取配置
    cfg = load_config()
    lang_codes = cfg.get("jar_extractor", {}).get("lang_codes", ["en_us", "zh_cn", "zh_tw"])
    folder_names = cfg.get("extractor", {}).get("output_folder_names", {})
    lang_extract = folder_names.get("lang_extract", "_提取lang_輸出")
    book_extract = folder_names.get("book_extract", "_提取book_輸出")
    dual_extract = folder_names.get("dual_extract", "_提取both_輸出")

    # 根據 mode 產生輸出目錄
    if mode == "lang":
        output_subdir = lang_extract
    elif mode == "book":
        output_subdir = book_extract
    else:  # dual
        output_subdir = dual_extract

    final_output = os.path.join(output_dir, output_subdir) if output_dir else ""

    # 狀態變數
    state = {
        "running": False,
        "done": False,
        "cancelled": False,
        "stats": {"success": 0, "warnings": 0, "failures": 0},
        "progress": 0,
        "current_file": "",
    }

    # ========== UI 元件 - 進度條 ==========
    progress_bar = ft.ProgressBar(
        value=0, height=10, visible=False, bgcolor=theme.GREY_200, color=theme.BLUE
    )
    status_text = ft.Text("等待任務啟動...", size=13, color=theme.GREY_600)
    progress_pct = ft.Text("0%", size=12, color=theme.GREY_600, weight=ft.FontWeight.BOLD)

    # ========== UI 元件 - 日誌 ==========
    log_view = ft.ListView(
        expand=True,
        spacing=2,
        auto_scroll=True,
        padding=10,
    )

    # ========== UI 元件 - 結果統計 ==========
    stats_success = ft.Text("0", size=14, color=theme.GREEN_700, weight=ft.FontWeight.BOLD)
    stats_warnings = ft.Text("0", size=14, color=theme.ORANGE_700, weight=ft.FontWeight.BOLD)
    stats_failures = ft.Text("0", size=14, color=theme.RED_400, weight=ft.FontWeight.BOLD)

    stats_row = ft.Row(
        [
            ft.Text("結果：", size=13),
            ft.Text("成功 ", size=13),
            stats_success,
            ft.Text(" / 跳過 ", size=13),
            stats_warnings,
            ft.Text(" / 失敗 ", size=13),
            stats_failures,
        ],
        spacing=2,
        visible=False,
    )

    # ========== 按鈕 ==========
    start_button = ft.Button(
        "開始提取",
        icon=ft.Icons.PLAY_ARROW,
        bgcolor=theme.GREEN_700,
        color=theme.WHITE,
    )
    cancel_button = ft.Button(
        "取消",
        icon=ft.Icons.STOP,
        visible=False,
    )
    close_button = ft.Button(
        "關閉",
        icon=ft.Icons.CLOSE,
        visible=False,
    )
    browse_button = ft.Button(
        "開啟輸出資料夾",
        icon=ft.Icons.FOLDER_OPEN,
        visible=False,
    )

    # ========== 輔助函式 ==========
    def add_log(msg: str, color=None):
        if color is None:
            color = theme.CYAN_700
        log_view.controls.append(
            ft.Text(f">> {msg}", color=color, size=12, font_family="Consolas")
        )
        # 使用 run_task 確保在主執行緒執行
        page.run_task(_do_update)

    async def _do_update():
        page.update()

    def update_progress(val: float, text: str):
        progress_bar.value = val
        status_text.value = text
        progress_pct.value = f"{int(val * 100)}%"
        page.run_task(_do_update)

    def update_stats(success, warnings, failures):
        stats_success.value = str(success)
        stats_warnings.value = str(warnings)
        stats_failures.value = str(failures)
        page.run_task(_do_update)

    # ========== 提取工作執行緒 ==========
    def run_extraction():
        selected_mode = mode
        selected_codes = lang_codes  # 使用配置中的所有語系

        # 建立輸出目錄
        os.makedirs(final_output, exist_ok=True)

        # 開始提取
        state["running"] = True
        state["done"] = False
        state["cancelled"] = False

        # 更新 UI
        def ui_start():
            start_button.visible = False
            cancel_button.visible = True
            progress_bar.visible = True
            update_progress(0, "開始任務...")
            add_log(f"[系統] 開始提取 ({selected_mode})...")
            add_log(f"[系統] 來源：{mods_dir}")
            add_log(f"[系統] 輸出：{final_output}")

        # 直接呼叫 UI 更新（無需 run_task）
        ui_start()

        # 統計數據
        stats = {"success": 0, "warnings": 0, "failures": 0}

        try:
            if selected_mode == "lang":
                gen = extract_lang_files_generator(mods_dir, final_output, lang_codes=selected_codes)
            elif selected_mode == "book":
                gen = extract_book_files_generator(mods_dir, final_output)
            else:
                gen = extract_dual_files_generator(mods_dir, final_output, selected_codes)

            total = 0
            current = 0

            for update in gen:
                if state["cancelled"]:
                    add_log("[系統] 任務已取消", theme.ORANGE_700)
                    break

                # 解析更新
                if "progress" in update:
                    total = update.get("total", 1)
                    current = update.get("current", 0)
                    pct = update.get("progress", 0)
                    # 優先使用 log 欄位，若無則使用 current
                    log_msg = update.get("log", f"正在處理 {current}/{total}")
                    update_progress(pct, log_msg)
                    add_log(log_msg)

                    # 檢查是否完成（progress=1.0 或有 stats 欄位）
                    if pct >= 1.0 or "stats" in update:
                        result = update.get("stats", {})
                        stats["success"] = result.get("success", 0)
                        stats["warnings"] = result.get("warnings", 0)
                        stats["failures"] = result.get("failures", 0)
                        state["done"] = True

                elif "error" in update:
                    add_log(f"[ERROR] {update['error']}", theme.RED_400)
                    stats["failures"] += 1

            if state["done"]:
                add_log(f"[完成] 成功 {stats['success']} / 跳過 {stats['warnings']} / 失敗 {stats['failures']}", theme.GREEN_700)
                update_progress(1.0, "任務完成")
                update_stats(stats["success"], stats["warnings"], stats["failures"])

        except Exception as ex:
            add_log(f"[ERROR] {ex}", theme.RED_400)
            stats["failures"] += 1
            update_stats(stats["success"], stats["warnings"], stats["failures"])

        finally:
            state["running"] = False

            def ui_done():
                cancel_button.visible = False
                close_button.visible = True
                browse_button.visible = True
                stats_row.visible = True
                if on_complete:
                    on_complete(state["done"], state["stats"])
                page.update()

            # 直接呼叫 UI 更新
            ui_done()

    def on_start_click(e):
        # 驗證輸入
        if not mods_dir:
            status_text.value = "⚠️ 請先設定 Mod 來源"
            page.update()
            return
        if not os.path.isdir(mods_dir):
            status_text.value = "⚠️ Mod 來源資料夾不存在"
            page.update()
            return

        # 啟動執行緒
        threading.Thread(target=run_extraction, daemon=True).start()

    def on_cancel_click(e):
        state["cancelled"] = True
        status_text.value = "正在取消..."
        page.update()

    def on_close_click(e):
        dialog.open = False
        page.update()

    def on_browse_click(e):
        if final_output and os.path.isdir(final_output):
            os.startfile(final_output)

    # ========== 資訊顯示 ==========
    info_text = ft.Text(
        f"來源：{mods_dir}\n輸出：{final_output}",
        size=12,
        color=theme.GREY_600,
    )

    # ========== 建立對話框 ==========
    progress_section = ft.Column(
        [
            ft.Row([status_text, progress_pct], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            progress_bar,
        ],
        spacing=4,
    )

    log_section = ft.Container(
        content=log_view,
        bgcolor="#1e1e1e",
        border_radius=8,
        height=250,
        padding=10,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )

    dialog = ft.AlertDialog(
        modal=False,
        title=ft.Row(
            [
                ft.Icon(ft.Icons.DOWNLOAD, size=24, color=theme.BLUE_700),
                ft.Text(f"提取資源 - {mode.upper()}", size=18, weight=ft.FontWeight.BOLD),
            ],
            spacing=10,
        ),
        content=ft.Container(
            content=ft.Column(
                [
                    # 資訊區域
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text("設定", weight=ft.FontWeight.BOLD, size=14),
                                info_text,
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=theme.GREY_100,
                        border_radius=8,
                    ),
                    ft.Divider(),
                    # 進度區域
                    progress_section,
                    ft.Divider(),
                    # 日誌區域
                    log_section,
                    # 結果統計
                    stats_row,
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=dialog_width,
            height=min(600, int(page.height * 0.8)),
        ),
        actions=[
            start_button,
            cancel_button,
            close_button,
            browse_button,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    # 綁定按鈕事件
    start_button.on_click = on_start_click
    cancel_button.on_click = on_cancel_click
    close_button.on_click = on_close_click
    browse_button.on_click = on_browse_click

    # 顯示對話框
    page.overlay.append(dialog)
    dialog.open = True
    page.update()

    return dialog


def open_preview_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str = "",
    output_path: str = "",
    mode: str = "lang",
):
    """打開預覽對話框。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: Mod 來源路徑
        output_path: 輸出目錄路徑
        mode: 預設模式
    """
    from app.views.extractor.extractor_state import PreviewState

    dialog_width = max(500, int(page.width * 0.5))

    # 狀態
    preview_state = PreviewState()

    # 預覽結果區域
    result_text = ft.Text("點擊「預覽」開始掃描...", size=13)
    result_list = ft.ListView(height=200, spacing=2)

    # 按鈕
    def do_preview():
        mods = input_path
        if not mods or not os.path.isdir(mods):
            result_text.value = "⚠️ 請選擇有效的 Mod 來源"
            page.update()
            return

        result_text.value = "正在掃描..."
        page.update()

        try:
            jar_files = find_jar_files(mods)
            preview_state.total = len(jar_files)

            for update in preview_extraction_generator(mods, mode):
                if "error" in update:
                    result_text.value = f"錯誤: {update['error']}"
                    break
                preview_state.progress = update.get("progress", 0)
                preview_state.current = update.get("current", 0)
                result_text.value = f"進度：{preview_state.current}/{preview_state.total}"
                page.update()

            if preview_state.total > 0:
                result_text.value = f"完成！找到 {preview_state.total} 個 JAR 檔案"
        except Exception as ex:
            result_text.value = f"錯誤: {ex}"

        page.update()

    preview_button = ft.Button("開始預覽", icon=ft.Icons.SEARCH, on_click=lambda e: do_preview())

    dialog = ft.AlertDialog(
        modal=False,
        title=ft.Row(
            [
                ft.Icon(ft.Icons.SEARCH, size=24, color=theme.BLUE_700),
                ft.Text(f"預覽 - {mode.upper()}", size=18, weight=ft.FontWeight.BOLD),
            ],
            spacing=10,
        ),
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Text(f"來源：{input_path}", size=12, color=theme.GREY_600),
                    ft.Text(f"輸出：{output_path}", size=12, color=theme.GREY_600),
                    ft.Text(f"模式：{mode}", size=12, color=theme.GREY_600),
                    ft.Divider(),
                    result_text,
                    result_list,
                ],
                spacing=10,
            ),
            width=dialog_width,
            height=350,
        ),
        actions=[preview_button],
    )

    page.overlay.append(dialog)
    dialog.open = True
    page.update()

    return dialog
