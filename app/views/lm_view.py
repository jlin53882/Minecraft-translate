"""app/views/lm_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import asyncio
import threading
import time
from functools import partial

import flet as ft

from app.services_impl.moddb_service import load_db_settings, summarize_database
from app.services_impl.pipelines.lm_service import run_lm_translation_service
from app.tasks.task_session import TaskSession, tag_session
from app.ui import kit
from app.ui.design import C
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style, set_chip_status
from app.views._log import LogView, load_ui_logging_config
from translation_tool.utils.config_manager import (
    get_batch_write_interval,
    load_config,
)
from translation_tool.utils.log_unit import log_debug, log_info, log_warning

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
        batch_interval, cache_row, dry_run_row, lang_row = (
            self._init_lm_state_and_options(file_picker, page)
        )
        settings_card = self._build_lm_settings_card(
            batch_interval, cache_row, dry_run_row, lang_row
        )
        self._build_lm_status_and_log_cards(settings_card)

    def _init_lm_state_and_options(self, file_picker, page):
        """機器翻譯頁的狀態與輸入選項。"""
        self._page = page
        self.file_picker = file_picker

        self.session: TaskSession | None = None
        self._ui_timer_running = False
        self._poller = PollerHandle()  # 輪詢的 owner：卸載時 stop、重新掛載時 resume
        self._started_at: float | None = None

        # 基本輸入
        self.input_path = kit.text_field(
            hint="請選擇要進行 LM 翻譯的資料夾",
            icon=ft.Icons.FOLDER_OUTLINED,
            mono=True,
            expand=True,
            path=True,
        )
        self.output_path = kit.text_field(
            hint=f"留空會使用：{get_lm_translate_folder_name()}",
            icon=ft.Icons.FOLDER_COPY_OUTLINED,
            mono=True,
            expand=True,
            path=True,
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
        self._init_db_options()
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
        return batch_interval, cache_row, dry_run_row, lang_row

    def _init_db_options(self) -> None:
        """Mod 資料庫選項：預設值取自設定，可在這一次任務個別覆寫。"""
        db_settings = load_db_settings()
        db_row = kit.SwitchRow(
            "使用 Mod 資料庫",
            "資料庫 → 快取 → AI；翻譯結果也會寫回資料庫",
            db_settings.enabled,
            divider=False,
        )
        self.use_db_switch = db_row.switch
        self.use_db_switch.on_change = self._on_db_option_changed
        self.db_version_field = kit.text_field(
            "目標版本",
            hint="例如 1.21.1（留空則使用設定中的預設版本）",
            value=db_settings.version,
            on_change=self._on_db_option_changed,
        )
        self.db_info = ft.Text("", size=11.5, color=C.DIM)
        self.refresh_db_info()
        self.db_card = kit.section_card(
            "預翻譯資料庫",
            ft.Column(
                [db_row, self.db_version_field, self.db_info],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            icon=ft.Icons.DATASET_OUTLINED,
            tone="dia",
        )

    def _on_db_option_changed(self, _e=None) -> None:
        """開關或版本變更：更新提示（沒有指定版本時，這次不會使用資料庫）。"""
        self.refresh_db_info()
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面尚未掛載時只是不即時更新提示
            log_debug(f"LM 資料庫提示更新略過：{exc}")

    def refresh_db_info(self) -> None:
        """顯示資料庫目前的狀態（不存在時提示如何建立；開著卻沒有版本時提醒填寫）。"""
        try:
            info = summarize_database()
        except Exception as exc:  # noqa: BLE001 - 資料庫問題只影響提示文字，不應讓頁面載入失敗
            log_warning(f"讀取 Mod 資料庫摘要失敗：{exc!r}")
            info = {"problem": f"讀取摘要失敗（{exc}），詳情請看後台 log"}
        if info is not None and info.get("problem"):
            self.db_info.value = f"⚠ 資料庫無法使用：{info['problem']}"
        elif info is None:
            self.db_info.value = (
                "尚未建立資料庫：到「Mod 資料庫」頁掃描 jar 後，這裡會自動使用。"
            )
        else:
            self.db_info.value = (
                f"資料庫 {info['entries']:,} 條目（已翻譯 {info['progress']}%）・"
                f"版本：{'、'.join(info['versions'][:4]) or '—'}"
            )
        no_version = not (self.db_version_field.value or "").strip() and not (
            load_db_settings().version
        )
        if self.use_db_switch.value and no_version:
            self.db_info.value += "\n⚠ 尚未指定目標版本：請填寫上方「目標版本」，否則這次不會使用也不會寫入資料庫。"

    def _build_lm_settings_card(self, batch_interval, cache_row, dry_run_row, lang_row):
        """機器翻譯設定卡片與執行按鈕。"""
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
        return settings_card

    def _build_lm_status_and_log_cards(self, settings_card) -> None:
        """狀態卡片、日誌卡片與整體版面。"""
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
                        [settings_card, self.db_card],
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
            from app.services_impl.key_health_service import get_key_health_snapshot
            from app.shell.topbar import summarize_keys

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
        # 診斷：後端按下按鈕當下實際收到的輸入／輸出路徑（Web 欄位沒同步時可直接比對畫面）
        log_info(
            f"[LM翻譯] 開始按鈕：input={self.input_path.value!r}, "
            f"output={self.output_path.value!r}"
        )
        if self._ui_timer_running:
            # 任務執行中：避免重複啟動（會重複送出 API 並同時寫入同一輸出/快取）
            show_snack(self.page, "翻譯正在執行中，請等待完成或先取消", C.GOLD)
            return
        if not (self.input_path.value or "").strip():
            self._set_status("請先選擇輸入資料夾", "red")
            self.page.update()
            return

        # session 的 start()／finish() 由 run_lm_translation_service 擁有（單一 owner）：
        # 這裡不能再 start()，否則會重複登記並清掉剛寫入的日誌
        self.session = tag_session(TaskSession(), "機器翻譯", "lm")
        if not (self.output_path.value or "").strip():
            # 屬於 session 日誌的開頭訊息（service 的 start() 清空日誌後會放回，
            # 輪詢的 tail 重整也不會讓它消失）；沒有此方法的替身退回 add_log
            add = getattr(self.session, "add_start_log", self.session.add_log)
            add(f"[資訊] 未指定輸出，將使用預設：{get_lm_translate_folder_name()}")
        # 日誌顯示行數：每次開始任務時讀最新設定，存檔後不必重開頁面
        self.log_view.set_tail_lines(
            load_ui_logging_config(load_config).get("tail_lines", 250)
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
        db_version = (self.db_version_field.value or "").strip() or None

        log_debug(
            "LM UI options: dry_run=%s export_lang=%s write_new_cache=%s",
            dry_run,
            export_lang,
            write_new_cache,
        )

        threading.Thread(
            target=partial(
                run_lm_translation_service,
                use_translation_db=self.use_db_switch.value,
                translation_db_version=db_version,
            ),
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

    def resume_interrupted(self, task) -> None:
        """重開後續跑（#151）：帶入上次的輸入與選項後開始。

        只在使用者於啟動時的確認對話框按下「續跑」後才會被呼叫；已完成的譯文由翻譯快取
        還原，只會翻譯尚未完成的部分。
        """
        if self._ui_timer_running:
            show_snack(self.page, "翻譯正在執行中，無法續跑上次的任務", C.GOLD)
            return
        self.input_path.value = task.input_dir
        self.output_path.value = task.output_dir
        self.dry_run_switch.value = False
        self.export_lang_checkbox.value = task.export_lang
        self.write_new_cache_switch.value = task.write_new_cache
        # 沿用上次任務實際使用的資料庫選項，不採用目前的頁面／設定值（否則剩餘項目會寫回不同版本）
        # 沒有版本＝上次其實沒有使用資料庫；不能讓空欄位退回目前設定的版本
        self.use_db_switch.value = task.use_translation_db and bool(
            task.translation_db_version
        )
        self.db_version_field.value = task.translation_db_version
        self.refresh_db_info()
        self.start_clicked(None)

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
        self._ui_timer_running = True
        if self._poller.running:
            return
        self._poller.start(self._page, self._poll_session)

    def will_unmount(self):
        """換頁／關閉：停止輪詢（idempotent）。任務本身照常執行，不再碰已卸載的控制項。"""
        self._poller.stop()

    def did_mount(self):
        """重新掛載：任務仍在追蹤就接續輪詢（任務已結束時補上最終狀態與按鈕）。"""
        if self._ui_timer_running and self.session is not None:
            self._poller.start(self._page, self._poll_session)

    async def _poll_session(self, alive=lambda: True):
        """定期把 session 的進度與日誌同步到畫面，直到任務結束、頁面關閉或輪詢被停止。"""
        while alive() and self._ui_timer_running:
            try:
                self._sync_from_session()
            except RuntimeError as e:
                # 例如頁面已關閉（session 中斷）：停止輪詢，背景任務照常完成
                log_debug(f"LM UI poll stopped: {e}")
                self._ui_timer_running = False
                break
            if alive() and self._ui_timer_running:
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
