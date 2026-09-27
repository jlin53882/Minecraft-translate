"""app/views/lm_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import asyncio
import threading

import flet as ft

from app.logging import load_ui_logging_config
from app.services_impl.pipelines.lm_service import run_lm_translation_service
from app.task_session import TaskSession
from app.ui import theme

# UI 共用元件：統一卡片/按鈕樣式
from app.ui.components import primary_button, secondary_button, styled_card
from app.ui.snack import show_snack
from app.views._log import LogView
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_debug

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
        self.cancel_button = secondary_button(
            "取消",
            icon=ft.Icons.STOP,
            tooltip="在目前批次完成後停止翻譯（已完成的部分會保留）",
            on_click=self.cancel_clicked,
        )
        self.cancel_button.disabled = True

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
            # 選項與狀態並排：1280×900 下原本垂直堆疊，日誌只剩約 4 行
            ft.ResponsiveRow(
                [
                    ft.Container(
                        col={"xs": 12, "md": 7},
                        content=styled_card(
                            title="翻譯選項",
                            icon=ft.Icons.FACT_CHECK,
                            content=ft.Column(
                                [
                                    self.dry_run_switch,
                                    self.export_lang_checkbox,
                                    self.write_new_cache_switch,
                                    ft.Row(
                                        [self.start_button, self.cancel_button],
                                        spacing=10,
                                    ),
                                ],
                                spacing=8,
                            ),
                        ),
                    ),
                    ft.Container(
                        col={"xs": 12, "md": 5},
                        content=styled_card(
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
                    ),
                ],
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
        if self._ui_timer_running:
            # 任務執行中：避免重複啟動（會重複送出 API 並同時寫入同一輸出/快取）
            show_snack(self.page, "翻譯正在執行中，請等待完成或先取消", theme.WARNING)
            return
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
        self._set_running(True)
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

    def cancel_clicked(self, e):
        """要求取消翻譯；會在目前批次完成後停止。"""
        if self.session is None or not self._ui_timer_running:
            return
        self.session.request_cancel()
        self.cancel_button.disabled = True
        self._set_status("取消中…", theme.AMBER_200)
        self.page.update()

    def _set_running(self, running: bool):
        """執行中停用「開始」、啟用「取消」。"""
        self.start_button.disabled = running
        self.cancel_button.disabled = not running

    # --------------------------------------------------
    # UI Timer
    # --------------------------------------------------
    _POLL_INTERVAL_SEC = 0.2

    def start_ui_timer(self):
        """啟動 UI 輪詢（在 Flet event loop 上執行，避免背景執行緒直接更新 UI）。"""
        if self._ui_timer_running:
            return
        self._ui_timer_running = True
        self._page.run_task(self._poll_session)

    async def _poll_session(self):
        """定期把 session 的進度與日誌同步到畫面，直到任務結束。"""
        while self._ui_timer_running:
            try:
                self._sync_from_session()
            except RuntimeError as e:
                # 例如頁面已關閉（session 中斷）：停止輪詢，背景任務照常完成
                log_debug(f"LM UI poll stopped: {e}")
                self._ui_timer_running = False
                break
            if self._ui_timer_running:
                await asyncio.sleep(self._POLL_INTERVAL_SEC)

    def _sync_from_session(self):
        """同步一次進度/日誌/狀態；任務結束時停止輪詢並恢復按鈕。"""
        session = self.session
        if session is None:
            return
        snap = session.snapshot()
        try:
            self.progress_bar.value = float(snap.get("progress", 0) or 0)
        except (TypeError, ValueError):
            self.progress_bar.value = 0
        self.log_view.sync_entries(snap.get("logs", []) or [], update=False)

        status = (snap.get("status") or "").upper()
        if status in ("DONE", "ERROR"):
            if status == "ERROR":
                self._set_status("任務發生錯誤", theme.RED_200)
            elif getattr(session, "cancel_requested", False):
                self._set_status("已取消", theme.AMBER_200)
            else:
                self._set_status("任務完成", theme.GREEN_200)
            self._ui_timer_running = False
            self._set_running(False)
        self.page.update()

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
