"""app/views/bundler_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/app/views/bundler_view.py (tkinter 修正版)

import flet as ft
import threading
from app.ui import theme
from app.services_impl.config_service import load_config_json
from app.services_impl.pipelines.bundle_service import run_bundling_service

# --- 導入 tkinter ---
import tkinter as tk
from tkinter import filedialog

class BundlerView(ft.Column):
    """BundlerView 類別。

    用途：封裝與 BundlerView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 BundlerView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=15)
        self.page = page
        # 我們仍然保留 file_picker，以防萬一 (雖然現在主要用 tkinter)
        self.file_picker = file_picker

        # --- UI 元件 ---
        self.root_dir_textfield = ft.TextField(
            label="翻譯專案根目錄",
            expand=True,
            tooltip="包含所有翻譯產出資料夾 (如 zh_tw_generated) 的最上層資料夾",
        )
        self.output_zip_textfield = ft.TextField(
            label="最終 ZIP 檔案儲存路徑",
            expand=True,
            tooltip="選擇您要將 .zip 檔案儲存的位置和檔名",
        )
        self.start_button = ft.ElevatedButton(
            "開始打包", on_click=self.start_bundling_clicked, icon=ft.Icons.ARCHIVE
        )
        self.progress_bar = ft.ProgressBar(value=0, visible=False)
        self.log_view = ft.ListView(expand=True, spacing=5, auto_scroll=True)

        # --- UI 佈局 ---
        self.controls = [
            ft.Card(
                content=ft.Container(
                    padding=15,
                    content=ft.Column(
                        [
                            ft.Text(
                                "打包成品資源包", theme_style=ft.TextThemeStyle.TITLE_LARGE
                            ),
                            ft.Row(
                                [
                                    self.root_dir_textfield,
                                    self._create_pick_button(
                                        self.root_dir_textfield, "dir"
                                    ),
                                ]
                            ),
                            ft.Row(
                                [
                                    self.output_zip_textfield,
                                    self._create_pick_button(
                                        self.output_zip_textfield, "save"
                                    ),
                                ]
                            ),
                            self.start_button,
                            self.progress_bar,
                        ],
                        spacing=15,
                    ),
                )
            ),
            ft.Text("打包日誌", theme_style=ft.TextThemeStyle.TITLE_MEDIUM),
            ft.Container(
                content=self.log_view,
                border=ft.border.all(1, theme.OUTLINE),
                border_radius=ft.border_radius.all(5),
                padding=10,
                expand=True,
            ),
        ]

    # --- 輔助函式 ---
    def _create_pick_button(self, target_textfield: ft.TextField, pick_type: str):
        
        """

    
        """
        if pick_type == "dir":
            icon = ft.Icons.FOLDER_OPEN
            tooltip = "選擇資料夾"
        else:  # 'save'
            icon = ft.Icons.SAVE_AS
            tooltip = "選擇儲存位置"
        return ft.IconButton(
            icon=icon,
            tooltip=tooltip,
            on_click=lambda e: self.pick_path_with_tkinter(
                e, target_textfield, pick_type
            ),  # <-- 修改點
        )

    def _show_snack_bar(self, message: str, color: str = theme.RED_600):
        
        """
        """
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def pick_path_with_tkinter(self, e, target_textfield: ft.TextField, pick_type: str):
        """
        *** 全新的函式：使用 tkinter 來選擇資料夾或儲存檔案 ***
        """
        path = ""
        try:
            root = tk.Tk()
            root.withdraw()  # 隱藏主視窗
            root.attributes("-topmost", True)  # 強制置頂

            if pick_type == "dir":
                path = filedialog.askdirectory(title="請選擇翻譯專案根目錄")
            else:  # 'save'
                config = load_config_json()
                default_name = config.get("output_bundler", {}).get(
                    "output_zip_name", "可使用翻譯.zip"
                )
                path = filedialog.asksaveasfilename(
                    title="請選擇要儲存的 ZIP 檔案路徑",
                    initialfile=default_name,
                    defaultextension=".zip",
                    filetypes=[("ZIP 壓縮檔", "*.zip"), ("所有檔案", "*.*")],
                )

            root.destroy()

            if path:
                target_textfield.value = path
                self.page.update()
            else:
                self._show_snack_bar("您已取消選擇", theme.BLUE_GREY_500)

        except Exception as ex:
            self._show_snack_bar(f"開啟對話框失敗: {ex}")

    # (原有的 Flet FilePicker 相關函式 on_path_picked 已被 pick_path_with_tkinter 取代)

    def set_controls_disabled(self, disabled: bool):
        
        """
        """
        for ctrl in [
            self.root_dir_textfield,
            self.output_zip_textfield,
            self.start_button,
        ]:
            ctrl.disabled = disabled
        self.page.update()

    def start_bundling_clicked(self, e):
        
        """
        """
        root_dir = self.root_dir_textfield.value
        output_zip = self.output_zip_textfield.value

        if not root_dir or not output_zip:
            self._show_snack_bar("錯誤：請同時提供「專案根目錄」和「ZIP 儲存路徑」！")
            return

        self.set_controls_disabled(True)
        self.progress_bar.value = 0
        self.progress_bar.color = theme.PRIMARY
        self.progress_bar.visible = True
        self.log_view.controls.clear()
        self.log_view.controls.append(ft.Text("[系統] 開始執行打包..."))
        self.page.update()

        thread = threading.Thread(
            target=self.bundling_worker, args=(root_dir, output_zip)
        )
        thread.start()

    def bundling_worker(self, root_dir, output_zip):
        
        """
        """
        try:
            for update in run_bundling_service(root_dir, output_zip):
                log_msg = update.get("log", "")
                for line in log_msg.split("\n"):
                    if line.strip():
                        self.log_view.controls.append(ft.Text(line))
                if "progress" in update:
                    self.progress_bar.value = update["progress"]
                if update.get("error"):
                    self.progress_bar.color = theme.RED
                self.log_view.scroll_to(offset=-1, duration=100)
                self.page.update()
        finally:
            self.set_controls_disabled(False)
