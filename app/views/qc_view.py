"""app/views/qc_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import flet as ft
from typing import Callable, Tuple, Any

# 導入 UI 主題
from app.ui import theme
from translation_tool.utils.log_unit import log_info

# 導入我們需要的服務
from app.services import (
    run_untranslated_check_service,
    run_variant_compare_service,
    run_variant_compare_tsv_service,
)

# 導入新的拆分元件
from app.views._log import LogView
from app.views.qc_base import QCBase
from app.views.untranslated_checker import UntranslatedChecker


class QCView(ft.Column):
    """QCView 類別。

    用途：封裝與 QCView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 QCView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=15)
        self._page = page
        self.file_picker = file_picker

        # --- 共用的日誌 UI ---
        self.progress_bar = ft.ProgressBar(value=0, visible=False)
        # 統一的 LogView widget（取代裸 ListView + 寫死 hex 容器）
        self.log_view = LogView(
            page=self._page,
            mode="append",
            max_lines=2000,
        )

        # --- 建立 QCBase 任務執行器 ---
        self.task_runner = QCBase(page, self.progress_bar, self.log_view)

        # --- 「未翻譯檢查」元件 (PR1 拆分) ---
        self.untranslated_checker = UntranslatedChecker(
            page, file_picker, self.task_runner
        )

        # --- 「簡繁差異比較 (JSON 資料夾模式)」的 UI 元件 ---
        self.cn_dir_textfield = ft.TextField(
            label="簡中 (zh_cn) 來源資料夾 (JSON)", expand=True
        )
        self.tw_dir_textfield_2 = ft.TextField(
            label="繁中 (zh_tw) 來源資料夾 (JSON)", expand=True
        )
        self.compare_out_dir_textfield = ft.TextField(
            label="JSON 差異報告 輸出資料夾", expand=True
        )
        self.compare_start_button = ft.Button(
            "啟動：JSON 資料夾差異比對",
            icon=ft.Icons.COMPARE,
            on_click=lambda e: self.start_task("compare_json"),
        )

        # --- 「簡繁差異比較 (TSV 單檔案模式)」的 UI 元件 ---
        self.tsv_file_textfield = ft.TextField(
            label="簡繁差異 TSV 檔案路徑", expand=True
        )
        self.tsv_out_file_textfield = ft.TextField(
            label="TSV 差異報告 輸出檔案 (.csv)", expand=True
        )
        self.compare_tsv_start_button = ft.Button(
            "啟動：TSV 單檔案差異比對",
            icon=ft.Icons.FILE_PRESENT,
            on_click=lambda e: self.start_task("compare_tsv"),
        )

        # --- UI 佈局 (分三個卡片避免雜亂) ---
        self.controls = [
            # 卡片 1: Key 缺失檢查 (已拆分為 UntranslatedChecker 元件)
            ft.Card(
                content=ft.Container(
                    padding=15,
                    content=self.untranslated_checker,
                )
            ),
            # 卡片 2: 簡繁差異比較 - JSON 資料夾模式
            ft.Card(
                content=ft.Container(
                    padding=15,
                    content=ft.Column(
                        [
                            ft.Text(
                                "簡繁翻譯差異比較 - JSON 資料夾模式",
                                theme_style=ft.TextThemeStyle.TITLE_LARGE,
                            ),
                            ft.Text(
                                "適用於大規模翻譯資料夾的比對，輸出 JSON 報告。",
                                theme_style=ft.TextThemeStyle.BODY_SMALL,
                                color=theme.BLUE_GREY,
                            ),
                            ft.Row(
                                [
                                    self.cn_dir_textfield,
                                    self._create_pick_button(
                                        self.cn_dir_textfield,
                                        "選擇簡中 (zh_cn) 來源資料夾",
                                        folder_mode=True,
                                    ),
                                ]
                            ),
                            ft.Row(
                                [
                                    self.tw_dir_textfield_2,
                                    self._create_pick_button(
                                        self.tw_dir_textfield_2,
                                        "選擇繁中 (zh_tw) 來源資料夾",
                                        folder_mode=True,
                                    ),
                                ]
                            ),
                            ft.Row(
                                [
                                    self.compare_out_dir_textfield,
                                    self._create_pick_button(
                                        self.compare_out_dir_textfield,
                                        "選擇 JSON 報告輸出資料夾",
                                        folder_mode=True,
                                    ),
                                ]
                            ),
                            self.compare_start_button,
                        ],
                        spacing=15,
                    ),
                )
            ),
            # 卡片 3: 簡繁差異比較 - TSV 單檔案模式
            ft.Card(
                content=ft.Container(
                    padding=15,
                    content=ft.Column(
                        [
                            ft.Text(
                                "簡繁翻譯差異比較 - TSV 單檔案模式",
                                theme_style=ft.TextThemeStyle.TITLE_LARGE,
                            ),
                            ft.Text(
                                "比較 TSV 檔案中 'zh_cn' 和 'zh_tw' 欄位的差異。將 'zh_cn' 轉換為繁體中文後，與 'zh_tw' 進行比較，並列出所有不匹配的條目。",
                                theme_style=ft.TextThemeStyle.BODY_SMALL,
                                color=theme.BLUE_GREY,
                            ),
                            ft.Row(
                                [
                                    self.tsv_file_textfield,
                                    self._create_pick_button(
                                        self.tsv_file_textfield,
                                        "選擇 TSV 檔案",
                                        folder_mode=False,
                                        file_filter="TSV files (*.tsv)",
                                    ),
                                ]
                            ),
                            ft.Row(
                                [
                                    self.tsv_out_file_textfield,
                                    self._create_pick_button(
                                        self.tsv_out_file_textfield,
                                        "選擇 CSV 輸出檔案",
                                        folder_mode=False,
                                        file_filter="CSV files (*.csv)",
                                    ),
                                ]
                            ),
                            self.compare_tsv_start_button,
                        ],
                        spacing=15,
                    ),
                )
            ),
            # 共用日誌
            ft.Text("處理日誌", theme_style=ft.TextThemeStyle.TITLE_MEDIUM),
            self.progress_bar,
            # self.log_view 已是 LogView widget（自帶深色容器 + 等寬字）
            self.log_view,
        ]

    # --- 輔助函式 (已修改以支援檔案/資料夾選擇和過濾) ---
    def _create_pick_button(
        self,
        target_textfield: ft.TextField,
        title: str,
        folder_mode: bool,
        file_filter: str = None,
    ):
        """建立檔案/資料夾選擇按鈕（使用 Flet FilePicker，無 tkinter）。"""
        return ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN if folder_mode else ft.Icons.FILE_PRESENT,
            tooltip=title,
            on_click=lambda e: self._pick_file_or_directory(
                e, target_textfield, title, folder_mode, file_filter
            ),
        )

    def _show_snack_bar(self, message: str, color: str = theme.ERROR):
        """顯示 SnackBar 訊息提示"""
        log_info(f"[UI] SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def _pick_file_or_directory(
        self,
        e: ft.ControlEvent,
        target_textfield: ft.TextField,
        title: str,
        folder_mode: bool,
        file_filter: str = None,
    ):
        """使用 Flet FilePicker 選擇檔案或目錄（無 tkinter，不會彈出 Windows 視窗）。"""
        self._pending_pick = {
            "target": target_textfield,
            "title": title,
            "folder_mode": folder_mode,
            "file_filter": file_filter,
        }
        self._page.run_task(self._async_pick_file_or_directory)

    async def _async_pick_file_or_directory(self):
        """非同步選擇Callback。"""
        pick = getattr(self, "_pending_pick", None)
        if not pick:
            return
        target: ft.TextField = pick["target"]
        title: str = pick["title"]
        folder_mode: bool = pick["folder_mode"]
        file_filter: str = pick["file_filter"]

        path = ""
        try:
            if folder_mode:
                result = await self.file_picker.get_directory_path(dialog_title=title)
                if result:
                    path = result
            else:
                result = await self.file_picker.pick_files(
                    dialog_title=title,
                    allow_multiple=False,
                )
                if result:
                    path = result[0].path

            if path:
                target.value = path
                self.page.update()
            else:
                self._show_snack_bar("您已取消選擇", theme.BLUE_GREY_500)
        except Exception as ex:
            self._show_snack_bar(f"開啟對話框失敗: {ex}")

    def set_controls_disabled(self, disabled: bool):
        """設定控制項是否禁用。"""
        controls_to_disable = [
            # JSON 比較
            self.cn_dir_textfield,
            self.tw_dir_textfield_2,
            self.compare_out_dir_textfield,
            self.compare_start_button,
            # TSV 比較
            self.tsv_file_textfield,
            self.tsv_out_file_textfield,
            self.compare_tsv_start_button,
        ]
        for ctrl in controls_to_disable:
            ctrl.disabled = disabled
        self.page.update()

    def start_task(self, task_type: str):
        """處理開始品質檢查任務"""
        self.log_view.clear()
        self.progress_bar.value = 0
        self.progress_bar.color = theme.PRIMARY
        self.progress_bar.visible = True
        self.set_controls_disabled(True)
        self.page.update()

        target_func: Callable[..., Any] | None = None
        args: Tuple[str, ...] = tuple()

        # 1. 未翻譯檢查 (已移至 UntranslatedChecker 元件，這裡保留作為備用)
        if task_type == "untranslated":
            en_dir = self.untranslated_checker.en_dir.value
            tw_dir = self.untranslated_checker.tw_dir.value
            out_dir = self.untranslated_checker.out_dir.value
            if not en_dir or not tw_dir or not out_dir:
                self._show_snack_bar("錯誤：請填寫所有「Key 缺失檢查」的路徑！")
                self.set_controls_disabled(False)
                return
            self.log_view.add("[系統] 開始執行 Key 缺失檢查...", level="system")
            target_func = run_untranslated_check_service
            args = (en_dir, tw_dir, out_dir)

        # 2. JSON 資料夾差異比較
        elif task_type == "compare_json":
            cn_dir = self.cn_dir_textfield.value
            tw_dir = self.tw_dir_textfield_2.value
            out_dir = self.compare_out_dir_textfield.value
            if not cn_dir or not tw_dir or not out_dir:
                self._show_snack_bar("錯誤：請填寫所有「JSON 資料夾差異比對」的路徑！")
                self.set_controls_disabled(False)
                return
            self.log_view.add("[系統] 開始執行 JSON 資料夾簡繁差異比較...", level="system")
            target_func = run_variant_compare_service
            args = (cn_dir, tw_dir, out_dir)

        # 3. TSV 單檔案差異比較
        elif task_type == "compare_tsv":
            tsv_path = self.tsv_file_textfield.value
            out_csv_path = self.tsv_out_file_textfield.value
            if not tsv_path or not out_csv_path:
                self._show_snack_bar("錯誤：請填寫所有「TSV 單檔案差異比對」的路徑！")
                self.set_controls_disabled(False)
                return
            self.log_view.add("[系統] 開始執行 TSV 單檔案簡繁差異比較...", level="system")
            target_func = run_variant_compare_tsv_service
            args = (tsv_path, out_csv_path)

        else:
            return

        # 使用 task_runner 執行任務
        self.task_runner.task_worker(
            target_func,
            args,
            on_complete=lambda: self.set_controls_disabled(False),
        )

    @property
    def page(self):
        return self._page
