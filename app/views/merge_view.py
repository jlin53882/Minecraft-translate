"""app/views/merge_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import threading
import time
from pathlib import Path

import flet as ft
from app.ui import theme
from translation_tool.utils.log_unit import log_info, log_warning, log_error

import flet as ft

# UI 共用元件：統一卡片/按鈕樣式
from app.ui.components import primary_button, styled_card

from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service
from app.task_session import TaskSession

class MergeView(ft.Column):
    """ZIP 合併頁面（視覺風格對齊 Translation/Extractor）。"""

    # --------------------------------------------------
    # Interlocking: when process_zh_cn_switch goes False, disable 2 and 3
    # --------------------------------------------------
    # Disabled 原因說明（兩個開關被禁用時動態顯示）
    _zh_cn_disabled_note: ft.Text = None  # type: ignore[assignment]

    def _skip_disabled_note(self) -> ft.Text:
        """對外暴露的 disabled 原因文字（讓 UI 可以引用同一個物件）。"""
        return self._zh_cn_disabled_note

    def _on_zh_cn_switch_changed(self, e):
        """互鎖：當 zh_cn 處理關閉時，禁用兩個依賴開關並顯示原因。"""
        enabled = e.control.value
        self.skip_zh_cn_switch.disabled = not enabled
        self.patchouli_skip_zh_cn_switch.disabled = not enabled
        if self._zh_cn_disabled_note:
            self._zh_cn_disabled_note.visible = not enabled
        if not enabled:
            self.skip_zh_cn_switch.value = False
            self.patchouli_skip_zh_cn_switch.value = False
        self.update()

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 MergeView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
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

        # 1. process_zh_cn_files switch
        self.process_zh_cn_switch = ft.Switch(
            label="處理 zh_cn 檔案",
            value=True,
            on_change=self._on_zh_cn_switch_changed,
        )
        # 2. skip_zh_cn_when_only_lang switch
        self.skip_zh_cn_switch = ft.Switch(
            label="只處理 lang 時跳過 zh_cn",
            value=False,
        )
        # 3. patchouli_skip_zh_cn switch
        self.patchouli_skip_zh_cn_switch = ft.Switch(
            label="Patchouli：允許 zh_cn 觸發跳過 en_us",
            value=False,
        )
        # 4. threshold field
        self.patchouli_threshold_field = ft.TextField(
            label="Patchouli 有效翻譯比例門檻",
            value="0.5",
            width=120,
            keyboard_type=ft.KeyboardType.NUMBER,
            suffix_text="(0.0~1.0)",
        )
        # Disabled 原因說明
        self._zh_cn_disabled_note = ft.Text(
            "⚠️ 需先開啟「處理 zh_cn 檔案」才能使用",
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

        # ZIP 清單
        self.zip_list_view = ft.Column(scroll="auto", spacing=4, tight=True)

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

        general_options_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("一般選項", weight=ft.FontWeight.W_600, size=15),
                    self.only_lang_checkbox,
                    ft.Text(
                        "只處理語言檔時，其他內容檔案會略過。",
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
                    ft.Text("簡體中文（zh_cn）處理", weight=ft.FontWeight.W_600, size=15),
                    self.process_zh_cn_switch,
                    ft.Text(
                        "啟用後會處理 zh_cn 檔案；關閉後，所有 zh_cn 檔案都會被略過。",
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
                                ft.Text("只處理 lang 時跳過 zh_cn", weight=ft.FontWeight.W_500, size=14),
                                self.skip_zh_cn_switch,
                                ft.Text(
                                    "僅在「只處理 lang」模式生效。開啟後，zh_cn 的 lang 檔會直接跳過。",
                                    size=12,
                                    color=theme.GREY_600,
                                ),
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
                                ft.Text("允許 zh_cn 觸發跳過 en_us", weight=ft.FontWeight.W_500, size=14),
                                self.patchouli_skip_zh_cn_switch,
                                ft.Text(
                                    "開啟後，若 zh_cn 的有效翻譯比例達門檻，會視為可用翻譯並跳過對應的 en_us。",
                                    size=12,
                                    color=theme.GREY_600,
                                ),
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
                                ft.Text("Patchouli 有效翻譯比例門檻", weight=ft.FontWeight.W_500, size=14),
                                self.patchouli_threshold_field,
                                ft.Text(
                                    "預設 0.5。代表至少一半的文字檔被判定為有效 CJK 翻譯。範圍 0.0 ~ 1.0。",
                                    size=12,
                                    color=theme.GREY_600,
                                ),
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

        # ------------------------------------------------------------------
        # Helper functions for settings UI (vertical block layout)
        # ------------------------------------------------------------------
        def _section_header(title: str) -> ft.Text:
            return ft.Text(title, size=12, weight=ft.FontWeight.W_600, color=theme.GREY_600)

        def _setting_block(
            label: str,
            control,
            note: str = "",
            disabled_note=None,
        ) -> ft.Column:
            """Vertical block: label + control + note + optional disabled_note."""
            children = [
                ft.Text(label, size=14),
                control,
            ]
            if note:
                children.append(ft.Text(note, size=11, color=theme.GREY_600))
            if disabled_note is not None:
                children.append(disabled_note)
            return ft.Column(children, spacing=4)

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
                        # 1. 輸出資料夾（keep existing row）
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
                        # 2. 一般選項 section header
                        _section_header("一般選項"),
                        # 只處理 lang 檔案 — VERTICAL block
                        _setting_block(
                            label="只處理 lang 檔案（其他檔案不處理）",
                            control=self.only_lang_checkbox,
                            note="僅處理 lang 目錄下的語言檔，忽略其餘副檔名",
                        ),
                        ft.Divider(height=16, color=theme.GREY_100),
                        # 3. 簡體中文（zh_cn）處理 section header
                        _section_header("簡體中文（zh_cn）處理"),
                        # 啟用 zh_cn 處理 — VERTICAL block
                        _setting_block(
                            label="啟用 zh_cn 檔案處理",
                            control=self.process_zh_cn_switch,
                            note="關閉後，所有 zh_cn 檔案將被跳過",
                            disabled_note=self._zh_cn_disabled_note,
                        ),
                        ft.Divider(height=16, color=theme.GREY_100),
                        # 4. Patchouli 進階設定 section header
                        _section_header("Patchouli 進階設定"),
                        # skip_zh_cn_when_only_lang — VERTICAL block
                        _setting_block(
                            label="只處理 lang 時跳過 zh_cn",
                            control=self.skip_zh_cn_switch,
                            note="僅處理 lang 時自動跳過 zh_cn lang 檔",
                        ),
                        ft.Container(height=8),
                        # patchouli_skip_zh_cn — VERTICAL block
                        _setting_block(
                            label="允許 zh_cn 觸發跳過 en_us",
                            control=self.patchouli_skip_zh_cn_switch,
                            note="開啟後，若 zh_cn 有效翻譯比例達門檻，會視為可用翻譯並跳過 en_us",
                        ),
                        ft.Container(height=8),
                        # Threshold — VERTICAL block, NO horizontal Row
                        ft.Column(
                            [
                                ft.Text("Patchouli 有效翻譯比例門檻", size=13),
                                ft.Row([self.patchouli_threshold_field], spacing=8),
                                ft.Text(
                                    "預設 0.5，代表至少一半的文字檔被判定為有效 CJK 翻譯。範圍 0.0 ~ 1.0。",
                                    size=11,
                                    color=theme.GREY_600,
                                ),
                            ],
                            spacing=4,
                        ),
                    ],
                    spacing=8,
                    scroll="auto",
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
        """開啟 ZIP 檔案選擇對話框"""
        self.file_picker.on_result = self._on_zip_picked
        self.file_picker.pick_files(
            dialog_title="選擇 ZIP 檔案",
            allow_multiple=True,
            allowed_extensions=["zip"],
        )

    def _on_zip_picked(self, e: ft.FilePickerResultEvent):
        """處理 ZIP 檔案選擇結果"""
        if not e.files:
            return
        for f in e.files:
            if f.path and f.path not in self.selected_zips:
                self.selected_zips.append(f.path)
        self._refresh_zip_list()
        self.page.update()

    def _refresh_zip_list(self):
        """重新整理 ZIP 檔案清單顯示"""
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
        """移除指定的 ZIP 檔案"""
        if path in self.selected_zips:
            self.selected_zips.remove(path)
            self._refresh_zip_list()
            self.page.update()

    # --------------------------------------------------
    # Output dir
    # --------------------------------------------------
    def pick_output_dir(self):
        """開啟輸出目錄選擇對話框"""
        self.file_picker.on_result = self._on_output_picked
        self.file_picker.get_directory_path(dialog_title="選擇輸出資料夾")

    def _on_output_picked(self, e: ft.FilePickerResultEvent):
        """處理輸出目錄選擇結果"""
        if e.path:
            self.output_dir_field.value = e.path
            self.page.update()

    # --------------------------------------------------
    # Task runner
    # --------------------------------------------------
    def start_merge(self, e):
        """處理開始合併按鈕點擊事件"""
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
        """啟動 UI 輪詢器，定期更新進度條與日誌顯示。"""
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
        """更新狀態晶片顯示"""
        self.status_chip.label = ft.Text(text)
        self.status_chip.bgcolor = color

    def _show_snack_bar(self, message: str, color: str = theme.RED_600):
        """顯示 SnackBar 訊息提示"""
        log_info(f"[UI] SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
