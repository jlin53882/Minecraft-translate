"""app/views/lm_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging
import threading
import time

import flet as ft

# UI 共用元件：統一卡片/按鈕樣式
from app.ui.components import primary_button, styled_card

from app.services_impl.pipelines.lm_service import run_lm_translation_service
from app.task_session import TaskSession
from translation_tool.utils.config_manager import load_config

logger = logging.getLogger(__name__)
LM_translate_folder_name = load_config().get("lm_translator", {}).get("lm_translate_folder_name", "LM翻譯後")


class LMView(ft.Column):
    """LM 翻譯頁（風格對齊 Translation/Extractor）。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """`__init__`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`__init__`, `TextField`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        super().__init__(expand=True, spacing=16)
        self.page = page
        self.file_picker = file_picker

        self.session: TaskSession | None = None
        self._ui_timer_running = False

        # 基本輸入
        self.input_path = ft.TextField(
            label="輸入資料夾（通常是 assets）",
            hint_text="請選擇要進行 LM 翻譯的資料夾",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER,
        )
        self.output_path = ft.TextField(
            label="輸出資料夾（可選）",
            hint_text=f"留空會使用：{LM_translate_folder_name}",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER_COPY,
        )

        # 參數
        self.dry_run_switch = ft.Switch(label="Dry-run（只分析，不發送 API）", value=False)
        self.export_lang_checkbox = ft.Switch(label="輸出 .lang 檔案（不是 .json）", value=False)
        self.write_new_cache_switch = ft.Switch(label="寫入新快取(每次回傳單獨快取)（write_new_cache）", value=False)

        # 狀態與日誌
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"), bgcolor=ft.Colors.GREY_200)
        self.progress_bar = ft.ProgressBar(value=0, height=8, bgcolor=ft.Colors.GREY_200, color=ft.Colors.BLUE)
        self.log_view = ft.ListView(expand=True, spacing=4, auto_scroll=True)

        # 按鈕（共用 primary style）
        self.start_button = primary_button(
            "開始翻譯",
            icon=ft.Icons.PLAY_ARROW,
            tooltip="開始執行 LM 翻譯流程",
            on_click=self.start_clicked,
        )

        self.controls = [
            styled_card(
                title="路徑設定",
                icon=ft.Icons.FOLDER,
                content=ft.Column(
                    [
                        self._path_row(self.input_path, self.pick_input_directory),
                        self._path_row(self.output_path, self.pick_output_directory),
                    ],
                    spacing=10,
                ),
            ),
            styled_card(
                title="翻譯選項",
                icon=ft.Icons.FACT_CHECK,
                content=ft.Column(
                    [
                        self.dry_run_switch,
                        self.export_lang_checkbox,
                        self.write_new_cache_switch,
                        ft.Row([self.start_button], spacing=10),
                    ],
                    spacing=8,
                ),
            ),
            styled_card(
                title="執行狀態",
                icon=ft.Icons.TIMELINE,
                content=ft.Column(
                    [
                        ft.Row([self.status_chip], wrap=True),
                        self.progress_bar,
                    ],
                    spacing=10,
                ),
            ),
            styled_card(
                title="執行日誌",
                icon=ft.Icons.RECEIPT_LONG,
                expand=True,
                content=ft.Container(
                    expand=True,
                    bgcolor="#1e1e1e",
                    border_radius=8,
                    padding=10,
                    content=self.log_view,
                ),
            ),
        ]

    # --------------------------------------------------
    # Style helpers
    # --------------------------------------------------
    # 本頁原本有 _section_header / _styled_card，現在改用 app.ui.components.styled_card。
    # 好處：
    # - 多頁共用一致樣式
    # - 之後調整 UI（padding/radius/border/divider）只要改一處

    def _path_row(self, field: ft.TextField, on_pick) -> ft.Control:
        """`_path_row`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`Row`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        return ft.Row(
            [
                field,
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                    icon_color=ft.Colors.BLUE_GREY_700,
                    tooltip="選擇資料夾",
                    on_click=on_pick,
                ),
            ],
            spacing=6,
        )

    # --------------------------------------------------
    # Events
    # --------------------------------------------------
    def pick_input_directory(self, e):
        """`pick_input_directory`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`get_directory_path`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        self.file_picker.on_result = self.on_input_dir_picked
        self.file_picker.get_directory_path()

    def on_input_dir_picked(self, e):
        """`on_input_dir_picked`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        if e.path:
            self.input_path.value = e.path
            self.page.update()

    def pick_output_directory(self, e):
        """`pick_output_directory`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`get_directory_path`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        self.file_picker.on_result = self.on_output_dir_picked
        self.file_picker.get_directory_path()

    def on_output_dir_picked(self, e):
        """`on_output_dir_picked`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        if e.path:
            self.output_path.value = e.path
            self.page.update()

    def start_clicked(self, e):
        """`start_clicked`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`TaskSession`, `start`, `_set_status`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        if not (self.input_path.value or "").strip():
            self._set_status("請先選擇輸入資料夾", ft.Colors.RED_200)
            self.page.update()
            return

        self.session = TaskSession()
        self.session.start()

        if not (self.output_path.value or "").strip():
            self.session.add_log(f"[資訊] 未指定輸出，將使用預設：{LM_translate_folder_name}")

        self._set_status("執行中", ft.Colors.BLUE_200)
        self.progress_bar.value = 0
        self.log_view.controls.clear()
        self.page.update()

        output_dir = self.output_path.value or LM_translate_folder_name
        dry_run = self.dry_run_switch.value
        export_lang = self.export_lang_checkbox.value
        write_new_cache = self.write_new_cache_switch.value

        logger.debug("LM UI options: dry_run=%s export_lang=%s write_new_cache=%s", dry_run, export_lang, write_new_cache)

        threading.Thread(
            target=run_lm_translation_service,
            args=(
                self.input_path.value,
                output_dir,
                self.session,
                dry_run,
                export_lang,
                write_new_cache,
            ),
            daemon=True,
        ).start()

        self.start_ui_timer()

    # --------------------------------------------------
    # UI Timer
    # --------------------------------------------------
    def start_ui_timer(self):
        """`start_ui_timer`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`start`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        if self._ui_timer_running:
            return
        self._ui_timer_running = True

        def loop():
            """`loop`
            
            用途：
            - 處理此函式的主要流程（細節以程式碼為準）。
            
            參數：
            - 依函式簽名。
            
            回傳：
            - None
            """
            while self._ui_timer_running:
                time.sleep(0.1)
                if not self.session:
                    continue

                snap = self.session.snapshot()
                self.progress_bar.value = snap["progress"]

                self.log_view.controls.clear()
                for line in snap["logs"][-250:]:
                    self.log_view.controls.append(ft.Text(line, size=13, color=ft.Colors.GREY_100))

                if snap["status"] == "DONE":
                    self._set_status("任務完成", ft.Colors.GREEN_200)
                    self._ui_timer_running = False
                elif snap["status"] == "ERROR":
                    self._set_status("任務發生錯誤", ft.Colors.RED_200)
                    self._ui_timer_running = False

                self.page.update()

        threading.Thread(target=loop, daemon=True).start()

    # --------------------------------------------------
    # UI helpers
    # --------------------------------------------------
    def _set_status(self, text: str, color: str):
        """`_set_status`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`Text`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        self.status_chip.label = ft.Text(text)
        self.status_chip.bgcolor = color

    def _show_snack_bar(self, message: str, color: str = ft.Colors.RED_600):
        """`_show_snack_bar`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`SnackBar`, `append`, `update`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
