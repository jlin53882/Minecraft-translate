"""app/views/untranslated_checker.py 模組。

用途：提供「翻譯 Key 缺失檢查」功能的 UI 元件。
維護注意：本模組依賴 qc_base.QCBase 與 run_untranslated_check_service。
"""

import flet as ft
from typing import List
from app.views.qc_base import QCBase
from app.services import run_untranslated_check_service
from app.ui import theme
from translation_tool.utils.log_unit import log_info


class UntranslatedChecker(ft.Container):
    """UntranslatedChecker 元件。

    用途：封裝「翻譯 Key 缺失檢查」的 UI 與邏輯。
    維護注意：此元件依賴外部傳入的 task_runner (QCBase 實例)。
    """

    def __init__(
        self,
        page: ft.Page,
        file_picker: ft.FilePicker,
        task_runner: QCBase,
    ):
        """初始化 UntranslatedChecker。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
            task_runner: QCBase 實例，用於執行緒任務
        """
        # --- 先建立 UI 元件 ---
        self.en_dir = ft.TextField(
            label="英文 (en_us) 來源資料夾",
            expand=True,
        )
        self.tw_dir = ft.TextField(
            label="繁中 (zh_tw) 來源資料夾",
            expand=True,
        )
        self.out_dir = ft.TextField(
            label="未翻譯報告 輸出資料夾",
            expand=True,
        )
        self.start_button = ft.ElevatedButton(
            "開始檢查",
            icon=ft.Icons.SEARCH_OFF,
            on_click=self._on_start,
        )

        # --- 先呼叫父類初始化 ---
        super().__init__(content=self._build_content())

        # --- 再設定實例屬性 ---
        self.page = page
        self.file_picker = file_picker
        self.task_runner = task_runner

        # 確保 file_picker 已加入 page.overlay
        if file_picker not in page.overlay:
            page.overlay.append(file_picker)

    def _build_content(self) -> ft.Column:
        """建立 UI 內容"""
        return ft.Column(
            [
                ft.Text(
                    "翻譯 Key 缺失檢查 (en_us vs zh_tw)",
                    theme_style=ft.TextThemeStyle.TITLE_LARGE,
                ),
                ft.Row(
                    [
                        self.en_dir,
                        self._create_pick_button(
                            self.en_dir,
                            "選擇英文 (en_us) 來源資料夾",
                            folder_mode=True,
                        ),
                    ]
                ),
                ft.Row(
                    [
                        self.tw_dir,
                        self._create_pick_button(
                            self.tw_dir,
                            "選擇繁中 (zh_tw) 來源資料夾",
                            folder_mode=True,
                        ),
                    ]
                ),
                ft.Row(
                    [
                        self.out_dir,
                        self._create_pick_button(
                            self.out_dir,
                            "選擇報告輸出資料夾",
                            folder_mode=True,
                        ),
                    ]
                ),
                self.start_button,
            ],
            spacing=15,
        )

    def _create_pick_button(
        self,
        target_textfield: ft.TextField,
        title: str,
        folder_mode: bool,
    ) -> ft.IconButton:
        """建立檔案/資料夾選擇按鈕"""
        return ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN if folder_mode else ft.Icons.FILE_PRESENT,
            tooltip=title,
            on_click=lambda e: self._pick_file_or_directory(
                e, target_textfield, title, folder_mode
            ),
        )

    def _pick_file_or_directory(
        self,
        e,
        target_textfield: ft.TextField,
        title: str,
        folder_mode: bool,
    ):
        """使用 FilePicker 選擇檔案或目錄"""
        if folder_mode:
            self.file_picker.get_directory_path(title=title)
        else:
            self.file_picker.pick_files(title=title)

        # 設定回調以更新 TextField
        def on_result(result):
            if result.path:
                target_textfield.value = result.path
                self.page.update()
            else:
                self._show_snack_bar("您已取消選擇", theme.BLUE_GREY_500)

        self.file_picker.on_result = on_result

    def _show_snack_bar(self, message: str, color: str = theme.RED_600):
        """顯示 SnackBar 訊息提示"""
        log_info(f"[UI] SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def _on_start(self, e):
        """處理開始檢查任務"""
        en_dir = self.en_dir.value
        tw_dir = self.tw_dir.value
        out_dir = self.out_dir.value

        if not en_dir or not tw_dir or not out_dir:
            self._show_snack_bar("錯誤：請填寫所有路徑！")
            return

        controls: List[ft.Control] = [
            self.start_button,
            self.en_dir,
            self.tw_dir,
            self.out_dir,
        ]

        self.task_runner.task_worker(
            run_untranslated_check_service,
            (en_dir, tw_dir, out_dir),
            controls_to_disable=controls,
        )
