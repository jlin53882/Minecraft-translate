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
from pathlib import Path
from app.ui import theme
from translation_tool.utils.log_unit import log_info
import threading

from app.task_session import TaskSession
from app.views.extractor.extractor_actions import (
    build_preview_error_dialog,
    build_preview_result_dialog,
    show_preview as run_preview_flow,
    start_extraction as run_extraction_flow,
    start_ui_poller as run_ui_poller,
    update_stats_from_log,
)
from app.views.extractor.extractor_state import ExtractionState
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

    def _load_target_language():
        """從 config 動態讀取預設目標語系。"""
        from translation_tool.utils.config_manager import load_config
        config = load_config()
        return config.get("extractor", {}).get("target_language", "zh_tw")

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 ExtractorView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(expand=True, spacing=15)
        self._page = page
        self.file_picker = file_picker

        # ExtractorView 的長任務狀態全部收斂到 TaskSession。
        # 背景執行緒只寫 session，UI 端靠 poller 讀快照更新畫面，
        # 這樣提取流程與畫面狀態不會互相纏在一起。
        self.session = TaskSession(max_logs=2000)
        self._ui_poller_stop = threading.Event()
        self._extraction_state = ExtractionState()

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
            hint_text="./mods 或 %USERPROFILE%/Mods",
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
            helper="（請選擇或直接輸入輸出資料夾）",
        )

        self.output_dir_helper_text = ft.Text("", size=12, color=ft.Colors.GREY_600)

        self.skip_zh_cn_switch = ft.Switch(
            label="跳過 zh_cn 抽取",
            value=False,
        )

        # 提取 Lang 按鈕：disabled/enabled 由 worker 透過 page.run_task() 控制
        self.lang_button = ft.Button(
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
        # 提取 Book 按鈕：disabled/enabled 由 worker 透過 page.run_task() 控制
        self.book_button = ft.Button(
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

        # 預覽按鈕（Lang）
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
        # 同時提取 Lang + Book，按下後啟動背景執行緒，UI 由 worker 透過 page.run_task() 更新
        self.dual_extract_button = ft.Button(
            "提取 Lang + Book",
            icon=ft.Icons.LANGUAGE,
            style=ft.ButtonStyle(
                color=theme.WHITE,
                bgcolor="#7B1FA2",
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=20,
            ),
            on_click=lambda e: self.start_extraction("dual"),
        )
        self.dual_preview_button = ft.OutlinedButton(
            "預覽 Lang + Book",
            icon=ft.Icons.PREVIEW,
            on_click=lambda e: self.show_preview("dual"),
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

        # 初始化 output_dir helper，動態讀取設定值
        self._update_output_dir_helper()

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
        """開啟目錄選擇對話框。

        Args:
            target: 選擇後要填入路徑的 TextField。
        """
        self._show_snack_bar("請選擇此欄位的資料夾", color=theme.BLUE_600)
        self._page.run_task(self._async_pick_directory, target)

    async def _async_pick_directory(self, target):
        """async 實作：等待使用者選擇目錄後更新目標欄位。

        Args:
            target: 選擇後要填入路徑的 TextField。
        """
        result = await self.file_picker.get_directory_path()
        if result:
            target.value = result
            self.page.update()
        else:
            self._show_snack_bar("未選擇資料夾", color=theme.BLUE_600)

    # 僅在按「預覽/提取」時自動填入輸出路徑，選擇資料夾時不自動填入
    def refresh_output_dir_helper(self):
        """重新讀取 config 並更新 output_dir_textfield 的 helper。

        在設定頁儲存 extractor 設定後呼叫。
        """
        self._update_output_dir_helper()

    def _update_output_dir_helper(self):
        """動態更新 output_dir_textfield 的 helper，顯示實際的資料夾命名設定。"""
        from translation_tool.utils.config_manager import load_config
        config = load_config()
        folder_names = config.get("extractor", {}).get("output_folder_names", {})
        lang_extract = folder_names.get("lang_extract", "_提取lang_輸出")
        book_extract = folder_names.get("book_extract", "_提取book_輸出")
        dual_extract = folder_names.get("dual_extract", "_提取both_輸出")
        lang_preview = folder_names.get("lang_preview", "_預覽lang_輸出")
        book_preview = folder_names.get("book_preview", "_預覽book_輸出")

        helper_text = (
            f"未指定時自動產生（路徑 + 設定名稱）：\n"
            f"  • Lang 提取：...mods + {lang_extract}\n"
            f"  • Book 提取：...mods + {book_extract}\n"
            f"  • Dual 提取：...mods + {dual_extract}\n"
            f"  • Lang 預覽：...mods + {lang_preview}\n"
            f"  • Book 預覽：...mods + {book_preview}\n\n"
            f"預設抽取語系：zh_cn / zh_tw / en_us\n"
            f"選項：可勾選「跳過 zh_cn 抽取」（預設關閉）\n"
            f"自動產生資料夾名稱可以在設定頁面調整"
        )
        self.output_dir_textfield.helper = helper_text
        self._page.update()

    def _auto_fill_output_path(self, mods_dir: str, mode: str = "lang"):
        """根據 Mods 資料夾自動產生並填入輸出路徑（使用指定模式的設定）。"""
        from translation_tool.utils.config_manager import load_config

        config = load_config()
        folder_names = config.get("extractor", {}).get("output_folder_names", {})
        lang_extract = folder_names.get("lang_extract", "_提取lang_輸出")
        book_extract = folder_names.get("book_extract", "_提取book_輸出")
        dual_extract = folder_names.get("dual_extract", "_提取both_輸出")

        if mode == "lang":
            suffix = lang_extract
        elif mode == "book":
            suffix = book_extract
        elif mode == "dual":
            suffix = dual_extract
        else:
            suffix = lang_extract

        mods_path = Path(mods_dir)
        output_path = str(mods_path.with_name(mods_path.name + suffix))
        self.output_dir_textfield.value = output_path
        self.page.update()
        self._append_log_line(f"[系統] 自動設定輸出路徑：{output_path}")

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
        """顯示提取結果摘要（UI 風格對齊預覽 modal）。"""
        stats = self._extraction_stats

        content = ft.Column(
            [
                ft.Text("提取結果摘要", size=16, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color=theme.GREEN, size=20),
                        ft.Text(f"成功處理 JAR：{stats['success']} 個", size=14),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.WARNING, color=theme.ORANGE, size=20),
                        ft.Text(f"因內容相同而跳過的檔案：{stats['warnings']} 個", size=14),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ERROR, color=theme.RED, size=20),
                        ft.Text(f"失敗項目：{stats['failures']} 個", size=14),
                    ],
                    spacing=8,
                ),
                ft.Divider(),
                ft.Text(
                    f"新提取或更新的檔案：{stats['total_files']} 個",
                    size=14,
                    color=ft.Colors.BLUE_700,
                    weight=ft.FontWeight.BOLD,
                ),
            ],
            spacing=10,
            tight=True,
        )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"提取完成 - {mode.upper()}"),
            content=ft.Container(content=content, width=520),
            actions=[
                ft.TextButton("關閉", on_click=lambda e: self._close_dialog_overlay(dialog)),
            ],
        )

        try:
            self.page.overlay.append(dialog)
            dialog.open = True
            async def _do_update(_):
                self.page.update()
            self.page.run_task(_do_update, None)
        except Exception:
            pass

    def _append_log_line(self, entry_or_str):
        """新增日誌訊息到日誌檢視區。

        支援傳入 LogEntry（PR2 後 poller 傳入）或 str（直接呼叫時）。
        """
        text = entry_or_str.text if hasattr(entry_or_str, "text") else entry_or_str
        log_info(f"[DEBUG] _append_log_line called: thread={threading.current_thread().name}, text={text[:80]}...")
        color = "#e0e0e0"  # default logs are light grey
        if "[ERROR]" in text:
            color = "#ff6b6b"  # soft red
        elif "[系統]" in text:
            color = "#69db7c"  # soft green
        elif "Translation" in text or "完成" in text:
            color = "#74c0fc"  # soft blue

        #log_info(f"[DEBUG] _append_log_line: before append, log_view.controls count={len(self.log_view.controls)}")
        self.log_view.controls.append(
            ft.Text(
                text,
                font_family="Consolas,Monospace",
                size=13,
                color=color,
                selectable=True,
            )
        )
        #log_info(f"[DEBUG] _append_log_line: after append, log_view.controls count={len(self.log_view.controls)}")

    # ==================================================
    # Worker Logic
    # ==================================================
    def start_extraction(self, mode: str):
        """启动 JAR 文件提取任务（lang 或 book 模式）"""
        return run_extraction_flow(self, mode)

    def _show_snack_bar(self, message: str, color: str = theme.ERROR):
        """
        顯示底部的快訊通知 (SnackBar)

        :param message: 要顯示的文字訊息
        :param color: SnackBar 的背景顏色，預設為淺紅色 (RED_400)
        """
        log_info(f"[UI] SnackBar: {message}")
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

    def _show_preview_dialog_result(self, result: dict, mode: str):
        """显示预览结果对话框"""
        dialog = build_preview_result_dialog(self, result, mode)
        self.page.overlay.append(dialog)
        dialog.open = True
        async def _do_update(_):
            self.page.update()
        self.page.run_task(_do_update, None)

    def _show_preview_dialog_error(self, error: str, mode: str):
        """显示预览错误对话框"""
        self._preview_error_dialog = build_preview_error_dialog(self, error, mode)
        self.page.overlay.append(self._preview_error_dialog)
        self._preview_error_dialog.open = True
        async def _do_update(_):
            self.page.update()
        self.page.run_task(_do_update, None)

    def _close_dialog_overlay(self, dialog):
        """關閉 overlay 對話框並重置 UI 狀態"""
        try:
            dialog.open = False
            self.status_text.value = '狀態：閒置'
            self.progress_bar.value = 0
            self.progress_bar.color = ft.Colors.BLUE
            self.set_controls_disabled(False)
            self.page.update()
        except Exception:
            pass

    def _start_from_preview_overlay(self, dialog, mode: str):
        """從預覽對話框開始提取（overlay 版本）"""
        self._close_dialog_overlay(dialog)
        self.start_extraction(mode)

    @property
    def page(self):
        return self._page
