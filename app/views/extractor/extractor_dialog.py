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

from app.ui import theme
from app.views._log import LogView
from app.services_impl.pipelines.extract_service import (
    prepare_extraction_paths,
    prepare_preview_paths,
    get_lang_codes,
    run_lang_extraction_service,
    run_book_extraction_service,
    run_extraction_loop,
    open_output_folder,
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
    auto_start: bool = False,  # 若 True，自動啟動提取（不需點擊「開始提取」）
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

    # ✅ 第一階段重構：路徑拼接邏輯已抽離至 Service 層
    # 由 prepare_extraction_paths 統一處理 config 讀取與子資料夾命名
    final_output = prepare_extraction_paths(mods_dir, mode, output_dir)

    # ✅ 階段 B-2 重構：lang_codes 讀取已抽離至 extract_service.get_lang_codes()
    lang_codes = get_lang_codes()

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
    # PR refactor/unified-log-view: 改用 LogView widget
    # 統一深色容器 + 等寬字 + 等級顏色（從 theme）
    log_view = LogView(
        page=page,
        mode="append",
        max_lines=2000,
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
    def add_log(msg: str, level: str = "info"):
        """PR refactor/unified-log-view: 改用 LogView.add() 統一處理等級顏色。

        level: debug/info/warning/error/system，預設 info
        從 msg 字串前綴（[系統 / [ERROR / [完成）也能推斷 level
        """
        # 從 msg 前綴推斷 level（向後兼容既有呼叫）
        if level == "info":
            if msg.startswith("[系統"):
                level = "system"
            elif msg.startswith("[ERROR"):
                level = "error"
            elif msg.startswith("[完成"):
                level = "system"
        log_view.add(f">> {msg}", level=level)

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
            add_log(f"[系統] 開始提取 ({selected_mode})...", level="system")
            add_log(f"[系統] 來源：{mods_dir}", level="system")
            add_log(f"[系統] 輸出：{final_output}", level="system")

        # 直接呼叫 UI 更新（無需 run_task）
        ui_start()

        # 統計數據
        stats = {"success": 0, "warnings": 0, "failures": 0}

        # ✅ 第三階段重構：使用 Service 層的 run_extraction_loop 處理 Generator
        # 將 Generator 選擇、cancelled 檢查、stats 解析等樣板程式碼抽離到 Service
        try:
            if selected_mode == "lang":
                gen = extract_lang_files_generator(mods_dir, final_output, lang_codes=selected_codes)
            elif selected_mode == "book":
                gen = extract_book_files_generator(mods_dir, final_output)
            else:
                gen = extract_dual_files_generator(mods_dir, final_output, selected_codes)

            # 使用 cancelled_flag list 作為執行緒間通訊（與 Service 介面一致）
            cancelled_flag = [False]

            # 定義 UI 更新回調（仍由 dialog 處理 UI，但 Generator 邏輯已抽離）
            def on_update(update):
                if "progress" in update:
                    total = update.get("total", 1)
                    current = update.get("current", 0)
                    pct = update.get("progress", 0)
                    log_msg = update.get("log", f"正在處理 {current}/{total}")
                    add_log(log_msg)
                    update_progress(pct, log_msg)

                    # 檢查是否完成
                    if pct >= 1.0 or "stats" in update:
                        state["done"] = True
                        add_log(
                            f"[完成] 成功 {stats['success']} / 跳過 {stats['warnings']} / 失敗 {stats['failures']}",
                            level="system",
                        )
                        update_progress(1.0, "任務完成")
                        update_stats(stats["success"], stats["warnings"], stats["failures"])

                elif "error" in update:
                    add_log(f"[ERROR] {update['error']}", level="error")

            # 透過 Service 統一處理 Generator 迭代
            result_stats = run_extraction_loop(gen, cancelled_flag=cancelled_flag, on_update=on_update)
            stats.update(result_stats)

            # 同步 cancelled_flag 到 state（讓 on_cancel_click 仍能正常運作）
            if cancelled_flag[0]:
                state["cancelled"] = True
                add_log("[系統] 任務已取消", theme.ORANGE_700)

            if state["done"]:
                update_stats(stats["success"], stats["warnings"], stats["failures"])

        except Exception as ex:
            add_log(f"[ERROR] {ex}", level="error")
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
            # ✅ 第三階段：透過 cancelled_flag list 與 Service 通訊
            # 這裡仍設置 state["cancelled"] 以保持向後相容
            state["cancelled"] = True
            status_text.value = "正在取消..."
            page.update()

    def on_close_click(e):
        dialog.open = False
        page.update()

    def on_browse_click(e):
        # ✅ 階段 C 重構：os.startfile 已抽離至 Service 層
        open_output_folder(final_output)

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

    # 如果指定了 auto_start，則自動啟動提取
    if auto_start:
        on_start_click(None)

    return dialog


def open_preview_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str = "",
    output_path: str = "",
    mode: str = "lang",
):
    """打開預覽對話框。

    直接呼叫原本的 extractor_actions.show_preview（沿用原本的 polling + 結果對話框實作）。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: Mod 來源路徑
        output_path: 輸出目錄路徑
        mode: 預設模式
    """
    from translation_tool.core.jar_processor import preview_extraction_generator, find_jar_files
    from app.views.extractor.extractor_state import PreviewState

    dialog_width = max(500, int(page.width * 0.5))

    # ========== UI 元件 ==========
    info_text = ft.Text(
        f"來源：{input_path}\n輸出：{output_path}\n模式：{mode}",
        size=12,
        color=ft.Colors.GREY_600,
    )

    # 進度區
    # 🐛 Bug 修復：初始狀態文字應為「等待開始」而非「正在掃描」
    status_text = ft.Text("等待開始預覽...", size=13, color=ft.Colors.GREY_600)
    progress_pct = ft.Text("--", size=12, color=ft.Colors.GREY_600, weight=ft.FontWeight.BOLD)
    # 🐛 Bug 修復：明確設定 progress_bar 的顏色與背景色，避免渲染不明顯
    progress_bar = ft.ProgressBar(
        value=0,
        height=8,
        bgcolor=theme.GREY_200,
        color=theme.BLUE,
    )

    # 日誌區
    # PR refactor/unified-log-view: 改用 LogView widget
    # 統一深色容器 + 等寬字 + 等級顏色（從 theme）
    log_view = LogView(
        page=page,
        mode="append",
        max_lines=2000,
        height=200,
    )

    def add_log(msg, level: str = "info"):
        """PR refactor/unified-log-view: 改用 LogView.add() 統一處理等級顏色。

        level: debug/info/warning/error/system，預設 info
        從 msg 字串前綴（[系統 / [ERROR / [完成）也能推斷 level
        """
        # 從 msg 前綴推斷 level（向後兼容既有呼叫）
        if level == "info":
            if msg.startswith("[系統"):
                level = "system"
            elif msg.startswith("[ERROR"):
                level = "error"
            elif msg.startswith("[完成"):
                level = "system"
        log_view.add(f">> {msg}", level=level)

    async def _do_update():
        page.update()

    def update_progress(pct, text):
        progress_bar.value = pct
        progress_pct.value = f"{int(pct * 100)}%"
        if text:
            status_text.value = text
        page.run_task(_do_update)

    def show_result_dialog(result):
        """顯示預覽結果對話框（包含『確認執行』按鈕）"""
        preview_results = result.get("preview_results", [])
        total_files = result.get("total_files", 0)
        total_size_mb = result.get("total_size_mb", 0)

        controls = [
            ft.Text(f"預覽結果（{mode.upper()}）", size=16, weight=ft.FontWeight.BOLD),
            ft.Divider(),
        ]

        if mode == "dual":
            total_lang = sum(r.get("lang_count", 0) for r in preview_results)
            total_book = sum(r.get("book_count", 0) for r in preview_results)
            controls.append(ft.Text(f"Lang：{total_lang} 個", size=14, color=ft.Colors.BLUE_700))
            controls.append(ft.Text(f"Book：{total_book} 個", size=14, color=ft.Colors.BLUE_700))
        else:
            controls.append(ft.Text(f"共找到 {total_files} 個檔案", size=14, color=ft.Colors.BLUE_700))

        controls.append(ft.Text(f"總大小：{total_size_mb:.2f} MB", size=14, color=ft.Colors.BLUE_700))
        controls.extend([ft.Divider(), ft.Text(f"詳細清單（{len(preview_results)} 個 JAR）：", size=13, weight=ft.FontWeight.BOLD)])

        jar_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
        for r in preview_results:
            if mode == "dual":
                jar_list.controls.append(
                    ft.Text(f"📦 {r['jar']}: Lang {r.get('lang_count', 0)} 個 / Book {r.get('book_count', 0)} 個", size=12)
                )
            else:
                jar_list.controls.append(
                    ft.Text(f"📦 {r['jar']}: {r['count']} 個檔案 ({r['size_mb']:.1f} MB)", size=12)
                )

        list_container = ft.Container(
            content=jar_list, height=300, padding=5, bgcolor=ft.Colors.GREY_100, border_radius=8
        )
        controls.append(list_container)

        def start_extraction(e):
            """確認執行：關閉結果對話框，直接啟動提取"""
            result_dialog.open = False
            preview_dialog.open = False
            page.update()
            # 直接開啟提取對話框並自動啟動（不需點擊「開始提取」）
            open_extractor_dialog(
                page, file_picker,
                input_path=input_path,
                output_path=output_path,
                mode=mode,
                auto_start=True,
            )

        result_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"提取預覽 - {mode.upper()}"),
            content=ft.Container(
                content=ft.Column(controls, spacing=8, scroll=ft.ScrollMode.AUTO),
                width=600,
                height=500,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: setattr(result_dialog, "open", False) or page.update()),
                ft.Button("確認執行", icon=ft.Icons.CHECK, on_click=start_extraction),
            ],
        )

        page.overlay.append(result_dialog)
        result_dialog.open = True
        page.update()

    # ========== 執行掃描 ==========
    state = {"running": False, "cancelled": False, "done": False}
    preview_state = PreviewState()

    def start_scan():
        """按鈕：開始預覽掃描"""
        if state["running"]:
            return

        nonlocal output_path
        # 若輸出路徑是空的，自動設定
        actual_output = output_path
        if not actual_output:
            # ✅ 階段 B-2 重構：preview 路徑拼接已抽離至 extract_service.prepare_preview_paths()
            actual_output = prepare_preview_paths(input_path, mode)
            # 更新 info_text
            info_text.value = f"來源：{input_path}\n輸出：{actual_output}\n模式：{mode}"
            output_path = actual_output
            page.update()

        state["running"] = True
        state["cancelled"] = False
        state["done"] = False
        preview_state.progress = 0
        preview_state.current = 0
        preview_state.total = 0
        preview_state.result = None
        preview_state.error = None

        # 重置 UI
        log_view.clear()
        progress_bar.value = 0
        progress_pct.value = "0%"
        status_text.value = "正在掃描..."
        start_button.disabled = True
        page.update()

        add_log(f"[系統] 開始預覽 {mode.upper()} 掃描...", level="system")

        def do_scan():
            """背景執行緒：跑 generator，更新 preview_state"""
            try:
                for update in preview_extraction_generator(input_path, mode):
                    if state["cancelled"]:
                        break
                    if "progress" in update:
                        preview_state.progress = update.get("progress", 0)
                        preview_state.current = update.get("current", 0)
                        preview_state.total = update.get("total", 0)
                        # 動態設定 log 屬性
                        try:
                            preview_state.log = update.get("log", "")
                        except Exception:
                            pass
                    if "error" in update:
                        preview_state.error = update["error"]
                        preview_state.done = True
                        break
                    if "result" in update:
                        preview_state.result = update["result"]
                        preview_state.done = True
            except Exception as ex:
                preview_state.error = str(ex)
                preview_state.done = True

        def ui_poller():
            """主執行緒輪詢：更新 UI 進度條 + log"""
            import time

            last_log = [None]  # 用 list 讓 closure 可以修改

            async def _do_update():
                progress_bar.value = preview_state.progress
                progress_pct.value = f"{int(preview_state.progress * 100)}%"
                cur_log = getattr(preview_state, 'log', None)
                if cur_log:
                    status_text.value = cur_log
                    if cur_log != last_log[0]:
                        add_log(cur_log)
                        last_log[0] = cur_log
                page.update()

            while not preview_state.done and not state["cancelled"]:
                try:
                    page.run_task(_do_update)
                except Exception:
                    pass
                time.sleep(0.1)

            # 完成後的 UI 更新
            final_result = preview_state.result
            final_error = preview_state.error

            async def _do_finalize():
                progress_bar.value = 1.0
                progress_pct.value = "100%"
                status_text.value = "預覽完成"
                start_button.disabled = False
                state["running"] = False

                if final_result:
                    results = final_result.get("preview_results", [])
                    add_log(f"[完成] 找到 {len(results)} 個 JAR", level="system")
                    page.update()
                    show_result_dialog(final_result)
                elif final_error:
                    add_log(f"[ERROR] {final_error}", level="error")
                    status_text.value = f"預覽失敗：{final_error}"
                    page.update()

            try:
                page.run_task(_do_finalize)
            except Exception:
                pass

        threading.Thread(target=do_scan, daemon=True).start()
        threading.Thread(target=ui_poller, daemon=True).start()

    # ========== 建立對話框 ==========
    start_button = ft.Button(
        "開始預覽",
        icon=ft.Icons.SEARCH,
        on_click=lambda e: start_scan(),
    )

    preview_dialog = ft.AlertDialog(
        modal=False,
        title=ft.Row([
            ft.Icon(ft.Icons.SEARCH, size=24, color=ft.Colors.BLUE_700),
            ft.Text(f"預覽 - {mode.upper()}", size=18, weight=ft.FontWeight.BOLD),
        ]),
        content=ft.Container(
            content=ft.Column([
                info_text,
                ft.Divider(),
                ft.Row([status_text, progress_pct], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                progress_bar,
                # log_view 已是 LogView widget（自帶深色容器 + 圓角）
                log_view,
            ], spacing=10),
            width=dialog_width,
            height=500,
        ),
        actions=[start_button],
    )

    page.overlay.append(preview_dialog)
    preview_dialog.open = True
    page.update()

    return preview_dialog
