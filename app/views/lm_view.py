"""app/views/lm_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import threading
import time

import flet as ft
from app.ui import theme
from app.ui.snack import show_snack
from translation_tool.utils.log_unit import log_info, log_debug

# UI 共用元件：統一卡片/按鈕樣式
from app.ui.components import primary_button, styled_card

from app.services_impl.pipelines.lm_service import run_lm_translation_service
from app.task_session import TaskSession
from app.logging import load_ui_logging_config
from app.views._log import LogView
from translation_tool.utils.config_manager import load_config

LM_translate_folder_name = (
    load_config().get("lm_translator", {}).get("lm_translate_folder_name", "LM翻譯後")
)


class LMView(ft.Column):
    """LM 翻譯頁（風格對齊 Translation/Extractor）。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 LMView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(expand=True, spacing=16)
        self._page = page
        self.file_picker = file_picker

        self.session: TaskSession | None = None
        self._ui_timer_running = False

        # 基本輸入
        self.input_path = ft.TextField(
            label="輸入資料夾（通常是 assets）",
            hint_text="請選擇要進行 LM 翻譯的資料夾",
            expand=True,
            dense=True,
            border_color=theme.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER,
        )
        self.output_path = ft.TextField(
            label="輸出資料夾（可選）",
            hint_text=f"留空會使用：{LM_translate_folder_name}",
            expand=True,
            dense=True,
            border_color=theme.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER_COPY,
        )

        # 參數
        self.dry_run_switch = ft.Switch(
            label="Dry-run（只分析，不發送 API）", value=False
        )
        self.export_lang_checkbox = ft.Switch(
            label="輸出 .lang 檔案（不是 .json）", value=False
        )
        self.write_new_cache_switch = ft.Switch(
            label="寫入新快取(每次回傳單獨快取)（write_new_cache）", value=False
        )
        batch_interval = load_config().get("lm_translator", {}).get("batch_write_interval", 2)
        self.batch_interval_info = ft.Text(
            f"快取寫入頻率: 每 {batch_interval} 批次寫入一次（由 Config 設定）",
            size=11,
            color=theme.GREY_600,
        )

        # 狀態與日誌
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"), bgcolor=theme.GREY_200)
        self.progress_bar = ft.ProgressBar(
            value=0, height=8, bgcolor=theme.GREY_200, color=theme.BLUE
        )
        # 統一的 LogView widget（取代裸 ListView + 寫死 hex 容器）
        # tail 模式與既有的 [-250:] 行為一致
        ui_cfg = load_ui_logging_config(load_config)
        self.log_view = LogView(
            page=self._page,
            mode="tail",
            tail_lines=ui_cfg.get("tail_lines", 250),
        )

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
                        self.batch_interval_info,
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
                # self.log_view 已是 LogView widget（自帶深色容器 + 等寬字）
                content=self.log_view,
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
        """建立路徑輸入列"""
        return ft.Row(
            [
                field,
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                    icon_color=theme.BLUE_GREY_700,
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
        """開啟輸入目錄選擇對話框"""
        self._page.run_task(self._async_pick_input_directory)

    async def _async_pick_input_directory(self):
        """async 實作：選擇輸入目錄並觸發回調。"""
        result = await self.file_picker.get_directory_path()
        if result:
            class FakeEvent:
                path = result
            self.on_input_dir_picked(FakeEvent())

    def pick_output_directory(self, e):
        """開啟輸出目錄選擇對話框。"""
        self._page.run_task(self._async_pick_output_directory)

    async def _async_pick_output_directory(self):
        """async 實作：選擇輸出目錄並觸發回調。"""
        result = await self.file_picker.get_directory_path()
        if result:
            class FakeEvent:
                path = result
            self.on_output_dir_picked(FakeEvent())

    def on_input_dir_picked(self, e):
        """處理輸入目錄選擇結果。

        Args:
            e: 具有 .path 屬性的事件物件。
        """
        if e.path:
            self.input_path.value = e.path
            self.page.update()

    def on_output_dir_picked(self, e):
        """處理輸出目錄選擇結果。

        Args:
            e: 具有 .path 屬性的事件物件。
        """
        if e.path:
            self.output_path.value = e.path
            self.page.update()

    def start_clicked(self, e):
        """處理開始翻譯按鈕點擊事件"""
        if not (self.input_path.value or "").strip():
            self._set_status("請先選擇輸入資料夾", theme.RED_200)
            self.page.update()
            return

        self.session = TaskSession()
        self.session.start()

        if not (self.output_path.value or "").strip():
            self.session.add_log(
                f"[資訊] 未指定輸出，將使用預設：{LM_translate_folder_name}"
            )

        self._set_status("執行中", theme.BLUE_200)
        self.progress_bar.value = 0
        self.log_view.clear()
        self.page.update()

        output_dir = self.output_path.value or LM_translate_folder_name
        dry_run = self.dry_run_switch.value
        export_lang = self.export_lang_checkbox.value
        write_new_cache = self.write_new_cache_switch.value

        log_debug(
            "LM UI options: dry_run=%s export_lang=%s write_new_cache=%s",
            dry_run,
            export_lang,
            write_new_cache,
        )

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
        """啟動 UI 更新定時器，定期刷新進度條與日誌。"""
        if self._ui_timer_running:
            return
        self._ui_timer_running = True

        def loop():
            while self._ui_timer_running:
                time.sleep(0.1)
                if not self.session:
                    continue

                try:
                    snap = self.session.snapshot()
                except Exception:
                    continue

                try:
                    self.progress_bar.value = float(snap.get("progress", 0) or 0)
                except Exception:
                    self.progress_bar.value = 0

                logs = snap.get("logs", []) or []
                try:
                    self.log_view.sync_entries(logs)
                except Exception as e:
                    log_debug(f"LM log presenter sync failed: {e}")

                # 強制刷新頁面（sync_entries 內部已呼叫 page.update()）
                try:
                    self.page.update()
                except Exception:
                    pass

                status = (snap.get("status") or "").upper()
                if status == "DONE":
                    self._set_status("任務完成", theme.GREEN_200)
                    self._ui_timer_running = False
                elif status == "ERROR":
                    self._set_status("任務發生錯誤", theme.RED_200)
                    self._ui_timer_running = False

                try:
                    self.page.update()
                except Exception as e:
                    log_debug(f"LM page update failed: {e}")
                    self._ui_timer_running = False
                    break

        threading.Thread(target=loop, daemon=True).start()

    # --------------------------------------------------
    # UI helpers
    # --------------------------------------------------
    def _set_status(self, text: str, color: str):
        """更新狀態晶片顯示"""
        self.status_chip.label = ft.Text(text)
        self.status_chip.bgcolor = color


    @property
    def page(self):
        """回傳 Flet Page 實例 (2026-08-01 PR #85 重構補 @property)。

        之前 def page(self) 沒 @property decorator,變成 bound method reference,
        PR #85 改用 show_snack(self.page, ...) 直接呼叫時,
        self.page 是 method object 而非 Page 實例,SnackBar 永遠跳不出來。
        加 @property 後 self.page 才是 Page 實例。
        """
        return self._page
