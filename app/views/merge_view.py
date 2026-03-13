"""app/views/merge_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import threading
import time
from pathlib import Path

import flet as ft
from app.ui import theme

import flet as ft

# UI 共用元件：統一卡片/按鈕樣式
from app.ui.components import primary_button, styled_card

from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service
from app.task_session import TaskSession


class MergeView(ft.Column):
    """ZIP 合併頁面（視覺風格對齊 Translation/Extractor）。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """

        - 主要包裝：`__init__`, `TaskSession`, `Event`

        回傳：None
        """
        super().__init__(expand=True, spacing=16)
        self.page = page
        self.file_picker = file_picker

        self.session = TaskSession(max_logs=2000)
        self._ui_stop = threading.Event()
        self._last_log_count = 0

        self.selected_zips: list[str] = []

        # 參數區
        self.only_lang_checkbox = ft.Checkbox(
            label="只處理 lang 檔案（其他檔案不處理）",
            value=True,
        )
        self.output_dir_field = ft.TextField(
            label="輸出資料夾",
            hint_text="請選擇合併結果輸出位置",
            expand=True,
            dense=True,
            border_color=theme.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER_COPY,
        )

        # ZIP 清單
        self.zip_list_view = ft.ListView(height=160, spacing=4, auto_scroll=False)

        # 狀態區
        self.status_chip = ft.Chip(
            label=ft.Text("尚未開始"), bgcolor=theme.GREY_200
        )
        self.progress_bar = ft.ProgressBar(
            value=0, height=8, bgcolor=theme.GREY_200, color=theme.BLUE
        )

        # 日誌區
        self.log_view = ft.ListView(expand=True, spacing=4, auto_scroll=True)

        # 動作按鈕（共用 primary style；語意色彩用 bgcolor 控制）
        self.pick_zip_button = primary_button(
            "新增 ZIP",
            icon=ft.Icons.ADD,
            tooltip="選擇要合併的 ZIP 檔案",
            on_click=self.pick_zips,
            bgcolor=theme.BLUE_700,
        )
        self.start_button = primary_button(
            "開始合併 ZIP",
            icon=ft.Icons.PLAY_ARROW,
            tooltip="開始執行 ZIP 合併流程",
            on_click=self.start_merge,
            bgcolor=theme.GREEN_700,
        )

        self.controls = [
            styled_card(
                title="ZIP 清單",
                icon=ft.Icons.ARCHIVE,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                self.pick_zip_button,
                                ft.Text(
                                    "可加入多個 ZIP，會依序合併",
                                    size=12,
                                    color=theme.GREY_600,
                                ),
                            ],
                            spacing=10,
                        ),
                        self.zip_list_view,
                    ],
                    spacing=10,
                ),
            ),
            styled_card(
                title="輸出與選項",
                icon=ft.Icons.FOLDER,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                self.output_dir_field,
                                ft.IconButton(
                                    icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                                    icon_color=theme.BLUE_GREY_700,
                                    tooltip="選擇輸出資料夾",
                                    on_click=lambda e: self.pick_output_dir(),
                                ),
                            ],
                            spacing=6,
                        ),
                        self.only_lang_checkbox,
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
                        self.start_button,
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
    # 目的：統一各頁卡片樣式，並降低重複程式碼。

    # --------------------------------------------------
    # ZIP handling
    # --------------------------------------------------
    def pick_zips(self, e):
        """

        - 主要包裝：`pick_files`

        回傳：None
        """
        self.file_picker.on_result = self._on_zip_picked
        self.file_picker.pick_files(
            dialog_title="選擇 ZIP 檔案",
            allow_multiple=True,
            allowed_extensions=["zip"],
        )

    def _on_zip_picked(self, e: ft.FilePickerResultEvent):
        """

        - 主要包裝：`_refresh_zip_list`

        回傳：None
        """
        if not e.files:
            return
        for f in e.files:
            if f.path and f.path not in self.selected_zips:
                self.selected_zips.append(f.path)
        self._refresh_zip_list()
        self.page.update()

    def _refresh_zip_list(self):
        """

        - 主要包裝：`clear`

        回傳：None
        """
        self.zip_list_view.controls.clear()
        for path in self.selected_zips:
            name = Path(path).name
            self.zip_list_view.controls.append(
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(name, expand=True),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            tooltip="移除",
                            on_click=lambda e, p=path: self._remove_zip(p),
                        ),
                    ],
                )
            )

    def _remove_zip(self, path: str):
        """

        回傳：None
        """
        if path in self.selected_zips:
            self.selected_zips.remove(path)
            self._refresh_zip_list()
            self.page.update()

    # --------------------------------------------------
    # Output dir
    # --------------------------------------------------
    def pick_output_dir(self):
        """

        - 主要包裝：`get_directory_path`

        回傳：None
        """
        self.file_picker.on_result = self._on_output_picked
        self.file_picker.get_directory_path(dialog_title="選擇輸出資料夾")

    def _on_output_picked(self, e: ft.FilePickerResultEvent):
        """

        回傳：None
        """
        if e.path:
            self.output_dir_field.value = e.path
            self.page.update()

    # --------------------------------------------------
    # Task runner
    # --------------------------------------------------
    def start_merge(self, e):
        """

        - 主要包裝：`clear`, `_set_status`, `start`

        回傳：None
        """
        if not self.selected_zips or not (self.output_dir_field.value or "").strip():
            self._show_snack_bar("請先選擇 ZIP 與輸出資料夾")
            return

        self.start_button.disabled = True
        self.zip_list_view.disabled = True
        self.log_view.controls.clear()
        self._set_status("執行中", theme.BLUE_200)

        self.session.start()
        self.session.add_log("[系統] 開始 ZIP 合併任務")
        self._start_ui_poller()

        threading.Thread(
            target=run_merge_zip_batch_service,
            args=(
                self.selected_zips,
                self.output_dir_field.value,
                self.session,
                self.only_lang_checkbox.value,
            ),
            daemon=True,
        ).start()

    # --------------------------------------------------
    # UI poller
    # --------------------------------------------------
    def _start_ui_poller(self):
        """

        - 主要包裝：`clear`, `start`

        回傳：None
        """
        self._ui_stop.clear()
        self._last_log_count = 0

        def poll():
            """

            回傳：None
            """
            while not self._ui_stop.is_set():
                snap = self.session.snapshot()
                status = snap["status"]
                progress = snap["progress"]
                logs = snap["logs"]

                if status == "RUNNING":
                    self._set_status("執行中", theme.BLUE_200)
                elif status == "DONE":
                    self._set_status("任務完成", theme.GREEN_200)
                elif status == "ERROR":
                    self._set_status("任務發生錯誤", theme.RED_200)

                self.progress_bar.value = progress

                if len(logs) > self._last_log_count:
                    for line in logs[self._last_log_count :]:
                        self.log_view.controls.append(
                            ft.Text(line, size=13, color=theme.GREY_100)
                        )
                    self._last_log_count = len(logs)
                    self.log_view.scroll_to(offset=-1, duration=100)

                if status in ("DONE", "ERROR"):
                    self.start_button.disabled = False
                    self.zip_list_view.disabled = False
                    self.page.update()
                    break

                self.page.update()
                time.sleep(0.1)

        threading.Thread(target=poll, daemon=True).start()

    # --------------------------------------------------
    # UI helpers
    # --------------------------------------------------
    def _set_status(self, text: str, color: str):
        """

        - 主要包裝：`Text`

        回傳：None
        """
        self.status_chip.label = ft.Text(text)
        self.status_chip.bgcolor = color

    def _show_snack_bar(self, message: str, color: str = theme.RED_600):
        """

        - 主要包裝：`SnackBar`

        回傳：None
        """
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
