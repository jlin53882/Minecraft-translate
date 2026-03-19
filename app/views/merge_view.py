"""app/views/merge_view.py 模組。
用途：提供 ZIP 合併頁面 UI 與執行流程。
維護注意：本檔案的 docstring 與中文註解用於維護說明，不代表行為變更。
"""

import threading
import time
from pathlib import Path

import flet as ft

from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service
from app.task_session import TaskSession
from app.ui import theme
from app.ui.components import primary_button, styled_card
from translation_tool.utils.log_unit import log_info


class MergeView(ft.Column):
    """ZIP 合併頁面（視覺風格對齊 Translation / Extractor）。"""

    _zh_cn_disabled_note: ft.Text = None  # type: ignore[assignment]

    def _skip_disabled_note(self) -> ft.Text:
        """回傳 zh_cn 關聯設定停用時的提示文字元件。"""
        return self._zh_cn_disabled_note

    def _on_zh_cn_switch_changed(self, e):
        """主開關互鎖：關閉 zh_cn 處理時，同步停用兩個相依設定。"""
        enabled = bool(e.control.value)
        self.skip_zh_cn_switch.disabled = not enabled
        self.patchouli_skip_zh_cn_switch.disabled = not enabled
        if self._zh_cn_disabled_note:
            self._zh_cn_disabled_note.visible = not enabled
        if not enabled:
            self.skip_zh_cn_switch.value = False
            self.patchouli_skip_zh_cn_switch.value = False
        self.update()

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 MergeView。"""
        super().__init__(expand=True, spacing=16, scroll=ft.ScrollMode.AUTO)
        self.page = page
        self.file_picker = file_picker

        self.session = TaskSession(max_logs=2000)
        self._ui_stop = threading.Event()
        self._last_log_count = 0
        self.selected_zips: list[str] = []

        self.only_lang_checkbox = ft.Checkbox(
            label="只處理 lang 檔案",
            value=True,
        )
        self.process_zh_cn_switch = ft.Switch(
            label="處理 zh_cn 檔案",
            value=True,
            on_change=self._on_zh_cn_switch_changed,
        )
        self.skip_zh_cn_switch = ft.Switch(
            label="只處理 lang 時跳過 zh_cn",
            value=False,
        )
        self.patchouli_skip_zh_cn_switch = ft.Switch(
            label="允許 zh_cn 觸發跳過 en_us",
            value=False,
        )
        self.patchouli_threshold_field = ft.TextField(
            value="0.5",
            width=96,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
        )
        self._zh_cn_disabled_note = ft.Text(
            "需先開啟「處理 zh_cn 檔案」",
            size=11,
            color=theme.RED_400,
            visible=False,
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

        self.zip_list_view = ft.ListView(height=160, spacing=4, auto_scroll=False)
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"), bgcolor=theme.GREY_200)
        self.progress_bar = ft.ProgressBar(value=0, height=8, bgcolor=theme.GREY_200, color=theme.BLUE)
        self.log_view = ft.ListView(expand=True, spacing=4, auto_scroll=True)

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

        general_options_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("一般選項", weight=ft.FontWeight.W_600, size=15),
                    self.only_lang_checkbox,
                    ft.Text(
                        "開啟後，只處理語言檔；其他內容檔案會略過。",
                        size=12,
                        color=theme.GREY_600,
                    ),
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=theme.GREY_50,
            border_radius=10,
        )

        zh_cn_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("zh_cn 處理", weight=ft.FontWeight.W_600, size=15),
                    self.process_zh_cn_switch,
                    ft.Text(
                        "關閉後，所有 zh_cn 檔案都會略過。",
                        size=12,
                        color=theme.GREY_600,
                    ),
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=theme.GREY_50,
            border_radius=10,
        )

        patchouli_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Patchouli 進階設定", weight=ft.FontWeight.W_600, size=15),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text("只處理 lang 時跳過 zh_cn", weight=ft.FontWeight.W_500, size=14, expand=True),
                                        self.skip_zh_cn_switch,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text("僅在「只處理 lang」模式生效。", size=12, color=theme.GREY_600),
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=theme.WHITE,
                        border_radius=8,
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text("允許 zh_cn 觸發跳過 en_us", weight=ft.FontWeight.W_500, size=14, expand=True),
                                        self.patchouli_skip_zh_cn_switch,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text("zh_cn 達門檻時，跳過對應 en_us。", size=12, color=theme.GREY_600),
                                self._skip_disabled_note(),
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=theme.WHITE,
                        border_radius=8,
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text("en_us 跳過門檻", weight=ft.FontWeight.W_500, size=14, expand=True),
                                        self.patchouli_threshold_field,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text("預設 0.5，範圍 0.0 ~ 1.0。", size=12, color=theme.GREY_600),
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=theme.WHITE,
                        border_radius=8,
                    ),
                ],
                spacing=10,
            ),
            padding=12,
            bgcolor=theme.GREY_50,
            border_radius=10,
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
                                ft.Text("可加入多個 ZIP，會依序合併。", size=12, color=theme.GREY_600),
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
                        general_options_section,
                        zh_cn_section,
                        patchouli_section,
                    ],
                    spacing=12,
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
                content=ft.Container(
                    height=280,
                    bgcolor="#2b2f36",
                    border=ft.border.all(1, "#4b5563"),
                    border_radius=8,
                    padding=10,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    content=self.log_view,
                ),
            ),
        ]

    def pick_zips(self, e):
        """開啟 ZIP 檔案選擇對話框。"""
        self.file_picker.on_result = self._on_zip_picked
        self.file_picker.pick_files(dialog_title="選擇 ZIP 檔案", allow_multiple=True, allowed_extensions=["zip"])

    def _on_zip_picked(self, e: ft.FilePickerResultEvent):
        """處理 ZIP 檔案選擇結果。"""
        if not e.files:
            return
        for f in e.files:
            if f.path and f.path not in self.selected_zips:
                self.selected_zips.append(f.path)
        self._refresh_zip_list()
        self.page.update()

    def _refresh_zip_list(self):
        """重新整理 ZIP 檔案清單顯示。"""
        self.zip_list_view.controls.clear()
        for path in self.selected_zips:
            name = Path(path).name
            self.zip_list_view.controls.append(
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(name, expand=True),
                        ft.IconButton(icon=ft.Icons.CLOSE, tooltip="移除", on_click=lambda e, p=path: self._remove_zip(p)),
                    ],
                )
            )

    def _remove_zip(self, path: str):
        """移除指定的 ZIP 檔案。"""
        if path in self.selected_zips:
            self.selected_zips.remove(path)
            self._refresh_zip_list()
            self.page.update()

    def pick_output_dir(self):
        """開啟輸出目錄選擇對話框。"""
        self.file_picker.on_result = self._on_output_picked
        self.file_picker.get_directory_path(dialog_title="選擇輸出資料夾")

    def _on_output_picked(self, e: ft.FilePickerResultEvent):
        """處理輸出目錄選擇結果。"""
        if e.path:
            self.output_dir_field.value = e.path
            self.page.update()

    def start_merge(self, e):
        """處理開始合併按鈕事件。"""
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
            args=(self.selected_zips, self.output_dir_field.value, self.session, self.only_lang_checkbox.value),
            daemon=True,
        ).start()

    def _start_ui_poller(self):
        """啟動 UI 輪詢器，定期同步進度與日誌。"""
        self._ui_stop.clear()
        self._last_log_count = 0

        def poll():
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
                    for line in logs[self._last_log_count:]:
                        self.log_view.controls.append(ft.Text(line, size=13, color=theme.WHITE))
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

    def _set_status(self, text: str, color: str):
        """更新狀態晶片顯示。"""
        self.status_chip.label = ft.Text(text)
        self.status_chip.bgcolor = color

    def _show_snack_bar(self, message: str, color: str = theme.RED_600):
        """顯示 SnackBar 訊息。"""
        log_info(f"[UI] SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
