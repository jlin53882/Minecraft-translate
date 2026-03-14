"""app/views/extractor_view.py（JAR 提取頁）

提供兩種提取流程：
- Lang：從 mods/*.jar 提取語言檔案
- Book：從 mods/*.jar 提取 Patchouli 等手冊內容

維護重點：
- 提取屬於長時間 I/O 任務；UI 與背景執行緒透過 TaskSession 溝通。
- UI 端靠 poller 定期讀 snapshot，避免背景執行緒直接操作 UI 控制項。

本輪僅補 docstring/註解，不調整提取流程。
"""

# /minecraft_translator_flet/app/views/extractor_view.py
import flet as ft
from app.ui import theme
import threading

from app.task_session import TaskSession
from app.ui.components import styled_card  # guard: shared card source remains explicit in extractor_view
from app.views.extractor.extractor_actions import (
    build_preview_error_dialog,
    build_preview_result_dialog,
    show_preview as run_preview_flow,
    start_extraction as run_extraction_flow,
    start_ui_poller as run_ui_poller,
    update_stats_from_log,
)
from app.views.extractor.extractor_panels import build_logs_card, build_settings_card, build_pick_button

class ExtractorView(ft.Column):
    """JAR 提取頁（UI）。

    設計概念：
    - 長任務全部寫入 TaskSession（log/progress/status）。
    - UI 只渲染 session 的快照，避免跨執行緒操作 UI 造成不穩定。

    維護注意：
    - 若新增新的提取模式，務必沿用同一套 session + poller 流程。
    - stats 欄位是 UI 顯示用途；不要在核心流程依賴它當正確性來源。
    """

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 ExtractorView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(expand=True, spacing=15)
        self.page = page
        self.file_picker = file_picker

        # ExtractorView 的長任務狀態全部收斂到 TaskSession。
        # 背景執行緒只寫 session，UI 端靠 poller 讀快照更新畫面，
        # 這樣提取流程與畫面狀態不會互相纏在一起。
        self.session = TaskSession(max_logs=2000)
        self._ui_poller_stop = threading.Event()
        self._last_rendered_log_count = 0

        # 提取統計
        self._extraction_stats = {
            "success": 0,
            "warnings": 0,
            "failures": 0,
            "total_files": 0,
        }

        # ======================
        # UI Components
        # ======================

        # 1. Configuration Section Components
        self.mods_dir_textfield = ft.TextField(
            hint_text="C:\\Example\\Mods",
            expand=True,
            dense=True,
            border_color=theme.OUTLINE,
            text_size=14,
            content_padding=15,
        )

        self.output_dir_textfield = ft.TextField(
            hint_text="（未指定將自動產生）",
            expand=True,
            dense=True,
            border_color=theme.OUTLINE,
            text_size=14,
            content_padding=15,
        )

        # 2. Action Buttons
        self.lang_button = ft.ElevatedButton(
            "提取 Lang",
            icon=ft.Icons.LANGUAGE,
            style=ft.ButtonStyle(
                color=theme.WHITE,
                bgcolor=theme.BLUE_700,
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=20,
            ),
            on_click=lambda e: self.start_extraction("lang"),
        )
        self.book_button = ft.ElevatedButton(
            "提取 Book",
            icon=ft.Icons.BOOK,
            style=ft.ButtonStyle(
                color=theme.WHITE,
                bgcolor=theme.GREEN_700,
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=20,
            ),
            on_click=lambda e: self.start_extraction("book"),
        )

        # 預覽按鈕
        self.preview_lang_button = ft.OutlinedButton(
            "預覽 Lang",
            icon=ft.Icons.PREVIEW,
            on_click=lambda e: self.show_preview("lang"),
        )
        self.preview_book_button = ft.OutlinedButton(
            "預覽 Book",
            icon=ft.Icons.PREVIEW,
            on_click=lambda e: self.show_preview("book"),
        )

        # 3. Status Display
        self.status_text = ft.Text("狀態：閒置", size=14, color=theme.GREY_700)
        self.progress_bar = ft.ProgressBar(
            value=0,
            visible=True,
            height=8,
            bgcolor=theme.GREY_200,
            color=theme.BLUE,
        )

        # 4. Logs Console
        self.log_view = ft.ListView(
            expand=True,
            spacing=2,
            auto_scroll=True,
            padding=10,
        )

        # ======================
        # Layout Composition
        # ======================
        self.controls = [
            self._build_settings_card(),
            self._build_logs_card(),
        ]

    def _build_settings_card(self):
        """构建设置卡片 UI 组件"""
        # delegate to panel builder; actual card仍使用 shared styled_card(...)
        return build_settings_card(self)

    def _build_logs_card(self):
        """构建日志卡片 UI 组件"""
        return build_logs_card(self)

    # ==================================================
    # UI helpers
    # ==================================================
    def _pick_button(self, target):
        """构建目录选择按钮"""
        return build_pick_button(self, target)

    def pick_directory(self, target):
        """開啟目錄選擇對話框"""
        self._show_snack_bar("請選擇此欄位的資料夾", color=theme.BLUE_600)
        self.file_picker.on_result = lambda e: self._on_dir_picked(e, target)
        self.file_picker.get_directory_path()

    def _on_dir_picked(self, e, target):
        """處理目錄選擇結果"""
        if e.path:
            target.value = e.path
            self.page.update()
        else:
            self._show_snack_bar("未選擇資料夾", color=theme.BLUE_600)

    def set_controls_disabled(self, disabled: bool):
        """設定控制項停用/啟用狀態"""
        for ctrl in (
            self.mods_dir_textfield,
            self.output_dir_textfield,
            self.lang_button,
            self.book_button,
        ):
            ctrl.disabled = disabled
            ctrl.opacity = 0.5 if disabled else 1.0
        self.page.update()

    def clear_output_path(self, e=None):
        """清除輸出路徑欄位"""
        if not (self.output_dir_textfield.value or "").strip():
            return
        self.output_dir_textfield.value = ""
        self.page.update()
        self._append_log_line("[系統] 已清除輸出路徑")

    # ==================================================
    # TaskSession UI Poller
    # ==================================================
    def _start_ui_poller(self, mode: str = ""):
        """启动 UI 轮询器以定期更新界面状态"""
        return run_ui_poller(self, mode=mode)

    def _update_stats_from_log(self, line: str):
        """根据日志内容更新提取统计信息"""
        return update_stats_from_log(self, line)

    def _show_extraction_summary(self, mode: str):
        """顯示提取結果摘要"""
        stats = self._extraction_stats

        # 建立摘要內容
        summary_content = ft.Column(
            [
                ft.Text("提取結果摘要", size=18, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color=theme.GREEN, size=24),
                        ft.Text(f"成功: {stats['success']} 個 JAR", size=14),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.WARNING, color=theme.ORANGE, size=24),
                        ft.Text(f"跳過: {stats['warnings']} 個 JAR", size=14),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ERROR, color=theme.RED, size=24),
                        ft.Text(f"失敗: {stats['failures']} 個 JAR", size=14),
                    ],
                    spacing=8,
                ),
                ft.Divider(),
                ft.Text(
                    f"總共提取: {stats['total_files']} 個檔案",
                    size=15,
                    weight=ft.FontWeight.BOLD,
                    color=theme.BLUE_700,
                ),
            ],
            spacing=12,
        )

        # 建立對話框
        dialog = ft.AlertDialog(
            title=ft.Text(f"{mode.upper()} 提取完成"),
            content=summary_content,
            actions=[
                ft.ElevatedButton(
                    "確定", on_click=lambda e: self._close_dialog(dialog)
                ),
            ],
        )

        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _append_log_line(self, line: str):
        """新增日誌訊息到日誌檢視區"""
        color = "#e0e0e0"  # default logs are light grey
        if "[ERROR]" in line:
            color = "#ff6b6b"  # soft red
        elif "[系統]" in line:
            color = "#69db7c"  # soft green
        elif "Translation" in line or "完成" in line:
            color = "#74c0fc"  # soft blue

        self.log_view.controls.append(
            ft.Text(
                line,
                font_family="Consolas,Monospace",
                size=13,
                color=color,
                selectable=True,
            )
        )

    # ==================================================
    # Worker Logic
    # ==================================================
    def start_extraction(self, mode: str):
        """启动 JAR 文件提取任务（lang 或 book 模式）"""
        return run_extraction_flow(self, mode)

    def _show_snack_bar(self, message: str, color: str = theme.RED_400):
        """
        顯示底部的快訊通知 (SnackBar)

        :param message: 要顯示的文字訊息
        :param color: SnackBar 的背景顏色，預設為淺紅色 (RED_400)
        """
        # 建立 SnackBar 元件，包含文字內容與背景顏色
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)

        # 將 SnackBar 加入頁面的 overlay 層。
        # 在現代 Flet 版本中，這是顯示彈出式元件（如 SnackBar, Dialog）的標準做法。
        self.page.overlay.append(snack)

        # 將 open 屬性設為 True 以觸發顯示動畫
        snack.open = True

        # 更新頁面，讓變更立即反映在 UI 上
        self.page.update()

    # ==================================================
    # 預覽功能
    # ==================================================
    def show_preview(self, mode: str):
        """显示提取预览对话框（lang 或 book 模式）"""
        return run_preview_flow(self, mode)

    def _show_preview_dialog_result_v2(self, result: dict, mode: str):
        """显示预览结果对话框"""
        dialog = build_preview_result_dialog(self, result, mode)
        try:
            self.page.open(dialog)
        except Exception as ex:
            self._append_log_line(f"[ERROR] 顯示對話框失敗: {ex}")

    def _show_preview_dialog_error_v2(self, error: str, mode: str):
        """显示预览错误对话框"""
        self._preview_error_dialog = build_preview_error_dialog(self, error, mode)
        try:
            self.page.open(self._preview_error_dialog)
        except Exception as ex:
            self._append_log_line(f"[ERROR] 顯示錯誤對話框失敗: {ex}")

    def _close_dialog_overlay(self, dialog):
        """關閉 overlay 對話框"""
        try:
            # 使用 Flet 官方推薦的關閉方式
            self.page.close(dialog)
        except Exception:
            # 如果 page.close() 失敗，改用手動方式
            dialog.open = False
            if dialog in self.page.overlay:
                self.page.overlay.remove(dialog)
            try:
                self.page.update()
            except Exception:
                pass

    def _start_from_preview_overlay(self, dialog, mode: str):
        """從預覽對話框開始提取（overlay 版本）"""
        self._close_dialog_overlay(dialog)
        self.start_extraction(mode)

