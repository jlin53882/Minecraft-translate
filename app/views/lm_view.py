"""app/views/lm_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import asyncio
import threading
import time

import flet as ft

from app.services_impl.pipelines.lm_service import run_lm_translation_service
from app.tasks.task_session import TaskSession, tag_session
from app.ui import kit, theme
from app.ui.design import C
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style, set_chip_status
from app.views._log import LogView, load_ui_logging_config
from translation_tool.utils.config_manager import (
    get_batch_write_interval,
    load_config,
)
from translation_tool.utils.log_unit import log_debug

DEFAULT_LM_TRANSLATE_FOLDER_NAME = "LM翻譯後"


def get_lm_translate_folder_name() -> str:
    """輸出資料夾預設名稱；使用時才讀設定，存檔後不需重啟（#117）。"""
    return (
        load_config()
        .get("lm_translator", {})
        .get("lm_translate_folder_name", DEFAULT_LM_TRANSLATE_FOLDER_NAME)
    )


def format_elapsed(seconds: float) -> str:
    """秒數 → ``mm:ss``（超過一小時為 ``h:mm:ss``）。"""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


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
        self._started_at: float | None = None

        # 基本輸入
        self.input_path = kit.text_field(
            hint="請選擇要進行 LM 翻譯的資料夾",
            icon=ft.Icons.FOLDER_OUTLINED,
            mono=True,
            expand=True,
        )
        self.output_path = kit.text_field(
            hint=f"留空會使用：{get_lm_translate_folder_name()}",
            icon=ft.Icons.FOLDER_COPY_OUTLINED,
            mono=True,
            expand=True,
        )

        # 參數（SwitchRow 負責版面，這裡保留 Switch 本體讓外部 / 測試讀寫 .value）
        dry_run_row = kit.SwitchRow("Dry-run", "只分析，不發送 API")
        lang_row = kit.SwitchRow("輸出 .lang 格式", "預設輸出 .json")
        cache_row = kit.SwitchRow(
            "寫入新快取", "每次回傳單獨存入快取（write_new_cache）", divider=False
        )
        self.dry_run_switch = dry_run_row.switch
        self.export_lang_checkbox = lang_row.switch
        self.write_new_cache_switch = cache_row.switch
        batch_interval = get_batch_write_interval()
        self.batch_interval_info = ft.Text(
            f"快取寫入頻率：每 {batch_interval} 批次寫入一次（由 Config 設定 lm_translator.batch_write_interval）",
            size=11,
            color=C.DIM,
        )

        # 狀態與日誌
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"))
        self._apply_status_style("neutral")
        self.progress_bar = kit.progress_bar(0, "em", height=8)
        # 統一的 LogView widget（取代裸 ListView + 寫死 hex 容器）
        # tail 模式與既有的 [-250:] 行為一致
        ui_cfg = load_ui_logging_config(load_config)
        self.log_view = LogView(
            page=self._page,
            mode="tail",
            tail_lines=ui_cfg.get("tail_lines", 250),
        )

        # 按鈕
        self.start_button = kit.button(
            "開始翻譯",
            "primary",
            icon=ft.Icons.PLAY_ARROW,
            tooltip="開始執行 LM 翻譯流程",
            on_click=self.start_clicked,
        )
        self.cancel_button = kit.button(
            "取消",
            "secondary",
            icon=ft.Icons.STOP,
            tooltip="在目前批次完成後停止翻譯（已完成的部分會保留）",
            on_click=self.cancel_clicked,
        )
        self.cancel_button.disabled = True

        # 統計卡：進度 / 已用時間 / API Key / 快取寫入
        self.stat_progress = kit.stat_card(
            "進度", "0%", icon=ft.Icons.SPEED, tone="em", expand=1
        )
        self.stat_elapsed = kit.stat_card(
            "已用時間", "—", icon=ft.Icons.TIMER_OUTLINED, tone="gold", expand=1
        )
        self.stat_keys = kit.stat_card(
            "可用 API Key", "—", icon=ft.Icons.KEY, tone="dia", expand=1
        )
        self.stat_cache = kit.stat_card(
            "快取寫入",
            f"{batch_interval}",
            delta="批次寫入一次",
            delta_tone="neutral",
            icon=ft.Icons.STORAGE_OUTLINED,
            tone="ench",
            expand=1,
        )
        self.refresh_key_stat()

        settings_card = kit.section_card(
            "翻譯設定",
            ft.Column(
                [
                    ft.Column(
                        [
                            kit.section_label("輸入資料夾（通常是 assets） *"),
                            self._path_row(self.input_path, self.pick_input_directory),
                        ],
                        spacing=6,
                    ),
                    ft.Column(
                        [
                            kit.section_label("輸出資料夾（可選）"),
                            self._path_row(
                                self.output_path, self.pick_output_directory
                            ),
                        ],
                        spacing=6,
                    ),
                    ft.Column([dry_run_row, lang_row, cache_row], spacing=0),
                    self.batch_interval_info,
                ],
                spacing=16,
            ),
            icon=ft.Icons.TUNE,
            tone="gold",
        )
        status_card = kit.section_card(
            "執行狀態",
            ft.Column(
                [
                    ft.Row([self.status_chip], wrap=True),
                    self.progress_bar,
                ],
                spacing=10,
            ),
            icon=ft.Icons.TIMELINE,
            tone="em",
        )
        log_card = kit.section_card(
            "執行日誌",
            # self.log_view 已是 LogView widget（自帶深色容器 + 等寬字）
            self.log_view,
            icon=ft.Icons.TERMINAL,
            tone="gold",
            expand=True,
        )

        self.controls = [
            kit.page_header(
                "機器翻譯",
                "Gemini 批次翻譯：多組金鑰自動輪替、失敗自動縮小批次重試",
                icon=ft.Icons.AUTO_AWESOME_OUTLINED,
                tone="gold",
                actions=[self.cancel_button, self.start_button],
            ),
            ft.Row(
                [
                    ft.Column(
                        [settings_card],
                        spacing=16,
                        expand=5,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    self.stat_progress,
                                    self.stat_elapsed,
                                    self.stat_keys,
                                    self.stat_cache,
                                ],
                                spacing=12,
                            ),
                            status_card,
                            log_card,
                        ],
                        spacing=16,
                        expand=8,
                    ),
                ],
                spacing=16,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]

    # --------------------------------------------------
    # Style helpers
    # --------------------------------------------------
    def _path_row(self, field: ft.TextField, on_pick) -> ft.Control:
        """建立路徑輸入列"""
        return ft.Row(
            [
                field,
                kit.pick_button(ft.Icons.FOLDER_OPEN_OUTLINED, "選擇資料夾", on_pick),
            ],
            spacing=8,
        )

    def refresh_key_stat(self):
        """更新「可用 API Key」統計（#113 的 key 健康度）。"""
        try:
            from app.shell.topbar import summarize_keys
            from translation_tool.core.lm_config_rules import get_key_health_snapshot

            summary = summarize_keys(get_key_health_snapshot())
        except Exception:  # noqa: BLE001 - 讀不到設定時只是不顯示
            return
        self.stat_keys.set_value(
            f"{summary.usable}/{summary.total}" if summary.total else "—",
            delta=(f"{summary.cooling} 把冷卻中" if summary.cooling else ""),
            delta_tone="gold",
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
            self._set_status("請先選擇輸入資料夾", "red")
            self.page.update()
            return

        self.session = tag_session(TaskSession(), "機器翻譯", "lm")
        self.session.start()

        if not (self.output_path.value or "").strip():
            self.session.add_log(
                f"[資訊] 未指定輸出，將使用預設：{get_lm_translate_folder_name()}"
            )

        self._set_status("執行中", "dia")
        self._started_at = time.monotonic()
        self._set_running(True)
        self.progress_bar.value = 0
        self.log_view.clear()
        self.page.update()

        output_dir = self.output_path.value or get_lm_translate_folder_name()
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
        self._set_status("取消中…", "gold")
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
        self._update_stats(self.progress_bar.value)

        status = (snap.get("status") or "").upper()
        if status in ("DONE", "ERROR"):
            if status == "ERROR":
                self._set_status("任務發生錯誤", "red")
            elif getattr(session, "cancel_requested", False):
                self._set_status("已取消", "gold")
            else:
                self._set_status("任務完成", "em")
            self._ui_timer_running = False
            self._set_running(False)
        self.page.update()

    def _update_stats(self, progress: float):
        """進度 / 已用時間 / Key 健康度統計卡。"""
        self.stat_progress.set_value(f"{round((progress or 0) * 100)}%")
        if self._started_at is not None:
            self.stat_elapsed.set_value(
                format_elapsed(time.monotonic() - self._started_at)
            )
        self.refresh_key_stat()

    # --------------------------------------------------
    # UI helpers
    # --------------------------------------------------
    def _set_status(self, text: str, tone: str = "neutral"):
        """更新狀態晶片顯示（``tone`` 為色組名稱，也接受舊背景色）。"""
        set_chip_status(self.status_chip, text, tone)

    def _apply_status_style(self, tone: str):
        apply_status_style(self.status_chip, tone)

    @property
    def page(self):
        """回傳 Flet Page 實例 (2026-08-01 PR #85 重構補 @property)。

        之前 def page(self) 沒 @property decorator,變成 bound method reference,
        PR #85 改用 show_snack(self.page, ...) 直接呼叫時,
        self.page 是 method object 而非 Page 實例,SnackBar 永遠跳不出來。
        加 @property 後 self.page 才是 Page 實例。
        """
        return self._page
