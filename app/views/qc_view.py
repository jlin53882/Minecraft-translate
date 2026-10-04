"""app/views/qc_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from collections.abc import Callable
from typing import Any

import flet as ft

# 導入我們需要的服務
from app.services import (
    run_untranslated_check_service,
    run_variant_compare_service,
    run_variant_compare_tsv_service,
)

# 導入 UI 元件與主題
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack

# 導入新的拆分元件
from app.views._log import LogView
from app.views.qc_base import QCBase
from app.views.untranslated_checker import UntranslatedChecker
from translation_tool.utils.log_unit import log_debug

# 三種檢驗模式（key、標題、說明、圖示、色組）
QC_MODES = (
    (
        "untranslated",
        "Key 缺失檢查",
        "比對 en_us 與 zh_tw 的 key",
        ft.Icons.FIND_IN_PAGE_OUTLINED,
        "dia",
    ),
    (
        "compare_json",
        "簡繁差異（JSON 資料夾）",
        "OpenCC 轉換後逐檔比對",
        ft.Icons.MERGE_TYPE,
        "em",
    ),
    (
        "compare_tsv",
        "簡繁差異（TSV 單檔）",
        "輸出含轉換欄位的 CSV",
        ft.Icons.DESCRIPTION_OUTLINED,
        "ench",
    ),
)


class QCView(ft.Column):
    """QC 品質檢驗頁：先選一種檢驗模式，再填路徑並執行；日誌共用同一塊。

    維護注意：三種模式的欄位 / 按鈕屬性名稱是對外（測試、其他頁）依賴的介面，改名前先確認呼叫點。
    """

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 QCView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=18)
        self._init_qc_state_and_progress(file_picker, page)
        self._build_qc_mode_cards()
        json_panel = self._build_qc_json_panel()
        self._build_qc_tsv_and_untranslated_panels(json_panel)

        self.controls = [
            kit.page_header(
                "QC 品質檢驗",
                "檢查缺漏、簡繁不一致與英文殘留；報告輸出到你指定的資料夾",
                icon=ft.Icons.VERIFIED_USER_OUTLINED,
                tone="red",
            ),
            ft.Row(list(self.mode_cards.values()), spacing=16),
            *self.mode_panels.values(),
            kit.section_card(
                "處理日誌",
                ft.Column(
                    [self.progress_bar, self.log_view],
                    spacing=10,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                icon=ft.Icons.TERMINAL,
                tone="gold",
            ),
        ]

    def _init_qc_state_and_progress(self, file_picker, page) -> None:
        """QC 頁的狀態、進度列與模式選擇。"""
        self._page = page
        self.file_picker = file_picker
        self.mode = QC_MODES[0][0]

        # --- 共用的日誌 UI ---
        self.progress_bar = kit.progress_bar(0, "em")
        self.progress_bar.visible = False
        # 統一的 LogView widget（取代裸 ListView + 寫死 hex 容器）
        # 本頁可捲動；日誌給固定高度（原本 expand 在捲動欄內只剩一行高）
        self.log_view = LogView(
            page=self._page,
            mode="append",
            max_lines=2000,
            height=360,
            expand=False,
        )

        # --- 建立 QCBase 任務執行器 ---
        self.task_runner = QCBase(page, self.progress_bar, self.log_view)

        # --- 「未翻譯檢查」元件 (PR1 拆分) ---
        self.untranslated_checker = UntranslatedChecker(
            page, file_picker, self.task_runner
        )

        # --- 「簡繁差異比較 (JSON 資料夾模式)」的 UI 元件 ---
        self.cn_dir_textfield = kit.text_field(
            "簡中 (zh_cn) 來源資料夾 (JSON)", mono=True, expand=True
        )
        self.tw_dir_textfield_2 = kit.text_field(
            "繁中 (zh_tw) 來源資料夾 (JSON)", mono=True, expand=True
        )
        self.compare_out_dir_textfield = kit.text_field(
            "JSON 差異報告 輸出資料夾", mono=True, expand=True
        )
        self.compare_start_button = kit.button(
            "啟動：JSON 資料夾差異比對",
            "primary",
            icon=ft.Icons.COMPARE,
            on_click=lambda e: self.start_task("compare_json"),
        )

        # --- 「簡繁差異比較 (TSV 單檔案模式)」的 UI 元件 ---
        self.tsv_file_textfield = kit.text_field(
            "簡繁差異 TSV 檔案路徑", mono=True, expand=True
        )
        self.tsv_out_file_textfield = kit.text_field(
            "TSV 差異報告 輸出檔案 (.csv)", mono=True, expand=True
        )
        self.compare_tsv_start_button = kit.button(
            "啟動：TSV 單檔案差異比對",
            "primary",
            icon=ft.Icons.FILE_PRESENT,
            on_click=lambda e: self.start_task("compare_tsv"),
        )

    def _build_qc_mode_cards(self) -> None:
        """QC 模式卡片。"""

        # --- UI 佈局：模式卡 + 對應的設定面板 + 共用日誌 ---
        self.mode_cards = {
            key: kit.ChoiceCard(
                key,
                title,
                sub,
                icon=icon,
                tone=tone,
                selected=key == self.mode,
                on_select=self.select_mode,
                expand=1,
            )
            for key, title, sub, icon, tone in QC_MODES
        }

    def _build_qc_json_panel(self):
        """JSON 檢查面板。"""

        json_panel = kit.section_card(
            "JSON 資料夾模式",
            ft.Column(
                [
                    ft.Text(
                        "適用於大規模翻譯資料夾的比對，輸出 JSON 報告。",
                        size=12.5,
                        color=C.MUTED,
                    ),
                    ft.Row(
                        [
                            self.cn_dir_textfield,
                            self._create_pick_button(
                                self.cn_dir_textfield,
                                "選擇簡中 (zh_cn) 來源資料夾",
                                folder_mode=True,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Row(
                        [
                            self.tw_dir_textfield_2,
                            self._create_pick_button(
                                self.tw_dir_textfield_2,
                                "選擇繁中 (zh_tw) 來源資料夾",
                                folder_mode=True,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Row(
                        [
                            self.compare_out_dir_textfield,
                            self._create_pick_button(
                                self.compare_out_dir_textfield,
                                "選擇 JSON 報告輸出資料夾",
                                folder_mode=True,
                            ),
                        ],
                        spacing=8,
                    ),
                    self.compare_start_button,
                ],
                spacing=14,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="em",
        )
        return json_panel

    def _build_qc_tsv_and_untranslated_panels(self, json_panel) -> None:
        """TSV 與未翻譯檢查面板。"""
        tsv_panel = kit.section_card(
            "TSV 單檔案模式",
            ft.Column(
                [
                    ft.Text(
                        "比較 TSV 檔案中 'zh_cn' 和 'zh_tw' 欄位的差異：把 'zh_cn' 轉成繁體後與 'zh_tw' 比對，列出所有不匹配的條目。",
                        size=12.5,
                        color=C.MUTED,
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
                        ],
                        spacing=8,
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
                        ],
                        spacing=8,
                    ),
                    self.compare_tsv_start_button,
                ],
                spacing=14,
            ),
            icon=ft.Icons.DESCRIPTION_OUTLINED,
            tone="ench",
        )
        untranslated_panel = kit.section_card(
            "Key 缺失檢查",
            self.untranslated_checker,
            icon=ft.Icons.FIND_IN_PAGE_OUTLINED,
            tone="dia",
        )
        self.mode_panels = {
            "untranslated": untranslated_panel,
            "compare_json": json_panel,
            "compare_tsv": tsv_panel,
        }
        self._apply_mode()

    # --- 模式切換 ---
    def select_mode(self, mode: str) -> None:
        """切換檢驗模式（只顯示對應的設定面板）。"""
        if mode not in self.mode_cards:
            raise KeyError(mode)
        self.mode = mode
        self._apply_mode()
        try:
            self.update()
        except RuntimeError:
            pass  # 尚未加入頁面

    def _apply_mode(self) -> None:
        for key, card in self.mode_cards.items():
            card.set_selected(key == self.mode)
        for key, panel in self.mode_panels.items():
            panel.visible = key == self.mode

    # --- 輔助函式 (已修改以支援檔案/資料夾選擇和過濾) ---
    def _create_pick_button(
        self,
        target_textfield: ft.TextField,
        title: str,
        folder_mode: bool,
        file_filter: str | None = None,
    ):
        """建立檔案/資料夾選擇按鈕（使用 Flet FilePicker，無 tkinter）。"""
        return kit.pick_button(
            ft.Icons.FOLDER_OPEN if folder_mode else ft.Icons.FILE_PRESENT,
            title,
            lambda e: self._pick_file_or_directory(
                e, target_textfield, title, folder_mode, file_filter
            ),
        )

    def _pick_file_or_directory(
        self,
        e: ft.ControlEvent,
        target_textfield: ft.TextField,
        title: str,
        folder_mode: bool,
        file_filter: str | None = None,
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
                show_snack(self.page, "您已取消選擇", C.MUTED)
        except Exception as ex:  # noqa: BLE001
            show_snack(self.page, f"開啟對話框失敗: {ex}")

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

    async def _scroll_to_log(self):
        try:
            await self.scroll_to(offset=-1, duration=300)
        except Exception as ex:  # noqa: BLE001 - 捲動失敗不影響任務
            log_debug(f"[QC] 捲動到日誌失敗: {ex}")

    def start_task(self, task_type: str):
        """處理開始品質檢查任務"""
        self.log_view.clear()
        self.progress_bar.value = 0
        self.progress_bar.color = C.EM
        self.progress_bar.visible = True
        self.set_controls_disabled(True)
        self.page.update()
        # 日誌在頁面最下方：開始任務時自動捲到底，讓使用者看得到進度
        run_task = getattr(self.page, "run_task", None)
        if run_task is not None:
            run_task(self._scroll_to_log)

        target_func: Callable[..., Any] | None = None
        args: tuple[str, ...] = ()

        # 1. 未翻譯檢查 (已移至 UntranslatedChecker 元件，這裡保留作為備用)
        if task_type == "untranslated":
            en_dir = self.untranslated_checker.en_dir.value
            tw_dir = self.untranslated_checker.tw_dir.value
            out_dir = self.untranslated_checker.out_dir.value
            if not en_dir or not tw_dir or not out_dir:
                show_snack(self.page, "錯誤：請填寫所有「Key 缺失檢查」的路徑！")
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
                show_snack(self.page, "錯誤：請填寫所有「JSON 資料夾差異比對」的路徑！")
                self.set_controls_disabled(False)
                return
            self.log_view.add(
                "[系統] 開始執行 JSON 資料夾簡繁差異比較...", level="system"
            )
            target_func = run_variant_compare_service
            args = (cn_dir, tw_dir, out_dir)

        # 3. TSV 單檔案差異比較
        elif task_type == "compare_tsv":
            tsv_path = self.tsv_file_textfield.value
            out_csv_path = self.tsv_out_file_textfield.value
            if not tsv_path or not out_csv_path:
                show_snack(self.page, "錯誤：請填寫所有「TSV 單檔案差異比對」的路徑！")
                self.set_controls_disabled(False)
                return
            self.log_view.add(
                "[系統] 開始執行 TSV 單檔案簡繁差異比較...", level="system"
            )
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
