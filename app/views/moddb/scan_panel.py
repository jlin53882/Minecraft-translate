"""app/views/moddb/scan_panel.py：掃描匯入（選版本 → 選 mods 資料夾 → 掃描 → 結果）。

掃描在背景執行緒進行（``run_moddb_scan_service``），畫面以輪詢同步進度與日誌，
流程與機器翻譯頁相同。已存在的條目一律跳過（只新增、不覆蓋）。
"""

from __future__ import annotations

import asyncio
import threading

import flet as ft

from app.services_impl.moddb_service import (
    ScanOptions,
    current_settings,
    pack_format_hint,
    run_moddb_scan_service,
    version_choices,
)
from app.tasks.task_session import TaskSession, tag_session
from app.ui import design, kit
from app.ui.design import C
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style, set_chip_status
from app.views._log import LogView, load_ui_logging_config
from app.views.moddb.formatting import format_count
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_debug

_POLL_INTERVAL_SEC = 0.2


class ScanPanel(ft.Column):
    """掃描匯入頁籤。"""

    def __init__(
        self, page: ft.Page, file_picker: ft.FilePicker, get_db, on_finished=None
    ):
        super().__init__(expand=True, spacing=12)
        self._page = page
        self.file_picker = file_picker
        self._get_db = get_db
        self._on_finished = on_finished
        self.session: TaskSession | None = None
        self._running = False
        self._poller = PollerHandle()
        self._versions: list[str] = []

        self._build_version_card()
        self._build_options_card()
        self._build_run_card()
        self.controls = [
            ft.Row(
                [
                    ft.Container(self.version_card, expand=1),
                    ft.Container(self.options_card, expand=1),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            self.run_card,
        ]
        self.refresh_versions()

    # ------------------------------------------------------------------ 建構
    def _build_version_card(self) -> None:
        self.version_field = kit.text_field(
            "遊戲版本",
            hint="可手動輸入，例如 1.21.1，或從下方清單選擇",
            value=current_settings().version,
            on_change=self._on_version_typed,
            expand=True,
        )
        self.version_hint = kit.hint_text("")
        self.version_search = kit.text_field(
            hint="搜尋版本",
            icon=ft.Icons.SEARCH,
            on_change=lambda _e: self._render_versions(),
        )
        self.version_list = ft.ListView(spacing=0, height=170)
        self.version_card = kit.section_card(
            "1　選擇遊戲版本",
            ft.Column(
                [
                    self.version_field,
                    self.version_hint,
                    self.version_search,
                    ft.Container(
                        self.version_list,
                        bgcolor=C.PANEL2,
                        border=ft.Border.all(1, C.LINE),
                        border_radius=design.RADIUS_CONTROL,
                    ),
                    kit.hint_text(
                        "與資源包打包頁共用同一份版本清單。這批 jar 全部歸入所選版本；1.12 以前的 .lang 格式不處理。"
                    ),
                ],
                spacing=10,
            ),
            icon=ft.Icons.SELL_OUTLINED,
            tone="dia",
        )

    def _build_options_card(self) -> None:
        self.path_field = kit.text_field(
            hint="請選擇 mods 資料夾",
            icon=ft.Icons.FOLDER_OUTLINED,
            mono=True,
            expand=True,
        )
        self.nested_row = kit.SwitchRow(
            "掃描內嵌 jar", "META-INF/jarjar、META-INF/jars 內的子模組也一併讀取", True
        )
        self.jar_tr_row = kit.SwitchRow(
            "讀取模組自帶 zh_tw／zh_cn", "以來源「模組自帶繁中」「簡中轉繁」匯入", True
        )
        self.cn_row = kit.SwitchRow(
            "簡中自動轉繁（OpenCC s2twp）",
            "只在沒有繁中時補上，標記為「簡中轉繁」",
            True,
        )
        self.book_row = kit.SwitchRow(
            "包含 Patchouli 手冊", "書籍內文也匯入，供跨版本沿用", True, divider=False
        )
        self.options_card = kit.section_card(
            "2　選擇 mods 資料夾與選項",
            ft.Column(
                [
                    kit.section_label("mods 資料夾"),
                    ft.Row(
                        [
                            self.path_field,
                            kit.pick_button(
                                ft.Icons.FOLDER_OPEN_OUTLINED,
                                "選擇資料夾",
                                self._pick_folder,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Column(
                        [self.nested_row, self.jar_tr_row, self.cn_row, self.book_row],
                        spacing=0,
                    ),
                    kit.hint_text(
                        "已存在的條目一律跳過（只新增、不覆蓋）；原文已變動者會另外回報。"
                    ),
                ],
                spacing=10,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="gold",
        )

    def _build_run_card(self) -> None:
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"))
        apply_status_style(self.status_chip, "neutral")
        self.progress_bar = kit.progress_bar(0, "em", height=8)
        ui_cfg = load_ui_logging_config(load_config)
        self.log_view = LogView(
            page=self._page, mode="tail", tail_lines=ui_cfg.get("tail_lines", 250)
        )
        self.log_view.height = 180
        self.start_btn = kit.button(
            "開始掃描", "primary", icon=ft.Icons.PLAY_ARROW, on_click=self.start_clicked
        )
        self.preview_btn = kit.button(
            "先預覽（不寫入）",
            "secondary",
            icon=ft.Icons.VISIBILITY_OUTLINED,
            on_click=lambda e: self.start_clicked(e, dry_run=True),
        )
        self.cancel_btn = kit.button(
            "取消",
            "secondary",
            icon=ft.Icons.STOP,
            on_click=self.cancel_clicked,
            disabled=True,
        )
        self.stat_new = kit.stat_card(
            "新增條目", "—", icon=ft.Icons.ADD_CIRCLE_OUTLINE, tone="em", expand=1
        )
        self.stat_added = kit.stat_card(
            "補入新來源譯文", "—", icon=ft.Icons.PLAYLIST_ADD, tone="dia", expand=1
        )
        self.stat_skip = kit.stat_card(
            "已存在，略過", "—", icon=ft.Icons.SKIP_NEXT, tone="neutral", expand=1
        )
        self.stat_changed = kit.stat_card(
            "原文已變動", "—", icon=ft.Icons.COMPARE_ARROWS, tone="gold", expand=1
        )
        self.stat_nolang = kit.stat_card(
            "沒有語言檔的 jar",
            "—",
            icon=ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
            tone="neutral",
            expand=1,
        )
        self.stat_failed = kit.stat_card(
            "無法讀取", "—", icon=ft.Icons.ERROR_OUTLINE, tone="red", expand=1
        )
        self.stats_row = ft.Row(
            [
                self.stat_new,
                self.stat_added,
                self.stat_skip,
                self.stat_changed,
                self.stat_nolang,
                self.stat_failed,
            ],
            spacing=10,
        )
        self.run_card = kit.section_card(
            "3　掃描",
            ft.Column(
                [
                    ft.Row(
                        [
                            self.start_btn,
                            self.preview_btn,
                            self.cancel_btn,
                            self.status_chip,
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.progress_bar,
                    self.log_view,
                    self.stats_row,
                ],
                spacing=12,
            ),
            icon=ft.Icons.RADAR,
            tone="em",
        )

    # --------------------------------------------------------------- 版本清單
    def refresh_versions(self) -> None:
        db = self._get_db()
        self._versions = version_choices(db)
        self._render_versions()
        self._on_version_typed()

    def _render_versions(self) -> None:
        q = (self.version_search.value or "").strip().lower()
        chosen = (self.version_field.value or "").strip()
        db = self._get_db()
        in_db = set(db.versions()) if db else set()
        rows: list[ft.Control] = []
        for label in self._versions:
            if q and q not in label.lower():
                continue
            rows.append(
                ft.Container(
                    ink=True,
                    on_click=lambda _e, v=label: self._choose_version(v),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                    bgcolor=C.EM_BG if label == chosen else None,
                    border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
                    content=ft.Row(
                        [
                            ft.Text(
                                label,
                                size=13,
                                color=C.TEXT,
                                weight=ft.FontWeight.W_500
                                if label == chosen
                                else ft.FontWeight.W_400,
                                expand=True,
                            ),
                            *([kit.chip("資料庫已有", "em")] if label in in_db else []),
                            ft.Text(
                                pack_format_hint(label),
                                size=11,
                                color=C.DIM,
                                font_family=design.FONT_MONO,
                            ),
                        ]
                    ),
                )
            )
        self.version_list.controls = rows or [
            kit.hint_text("沒有符合的版本；可直接在上方輸入")
        ]

    def _choose_version(self, label: str) -> None:
        self.version_field.value = label
        self._on_version_typed()
        self._safe_update()

    def _on_version_typed(self, _e=None) -> None:
        version = self.version()
        self.start_btn.text = f"開始掃描 {version}" if version else "開始掃描"
        self.version_hint.value = pack_format_hint(version)
        self._render_versions()

    def version(self) -> str:
        return (self.version_field.value or "").strip()

    # ------------------------------------------------------------------ 事件
    def _pick_folder(self, _e) -> None:
        self._page.run_task(self._async_pick_folder)

    async def _async_pick_folder(self) -> None:
        result = await self.file_picker.get_directory_path()
        if result:
            self.path_field.value = result
            self._safe_update()

    def build_options(self, *, dry_run: bool = False) -> ScanOptions:
        return ScanOptions(
            version=self.version(),
            read_jar_translations=self.jar_tr_row.value,
            convert_cn=self.cn_row.value,
            scan_nested=self.nested_row.value,
            include_patchouli=self.book_row.value,
            dry_run=dry_run,
        )

    def start_clicked(self, _e=None, *, dry_run: bool = False) -> None:
        if self._running:
            show_snack(self._page, "掃描正在執行中", C.GOLD)
            return
        folder = (self.path_field.value or "").strip()
        if not self.version():
            self._set_status("請先選擇或輸入遊戲版本", "red")
            self._safe_update()
            return
        if not folder:
            self._set_status("請先選擇 mods 資料夾", "red")
            self._safe_update()
            return
        self.session = tag_session(TaskSession(), "Mod 資料庫掃描", "moddb")
        self._set_status("預覽中" if dry_run else "掃描中", "dia")
        self._set_running(True)
        self.progress_bar.value = 0
        self.log_view.clear()
        self._reset_stats()
        self._safe_update()
        threading.Thread(
            target=run_moddb_scan_service,
            args=(folder, self.build_options(dry_run=dry_run), self.session),
            daemon=True,
        ).start()
        self._running = True
        if not self._poller.running:
            self._poller.start(self._page, self._poll)

    def cancel_clicked(self, _e=None) -> None:
        if self.session is None or not self._running:
            return
        self.session.request_cancel()
        self.cancel_btn.disabled = True
        self._set_status("取消中…", "gold")
        self._safe_update()

    def will_unmount(self) -> None:
        self._poller.stop()

    # ------------------------------------------------------------------ 輪詢
    async def _poll(self, alive=lambda: True) -> None:
        while alive() and self._running:
            try:
                self.sync_from_session()
            except RuntimeError as exc:
                log_debug(f"掃描輪詢停止：{exc}")
                self._running = False
                break
            if alive() and self._running:
                await asyncio.sleep(_POLL_INTERVAL_SEC)

    def sync_from_session(self) -> None:
        session = self.session
        if session is None:
            return
        snap = session.snapshot()
        self.progress_bar.value = float(snap.get("progress", 0) or 0)
        self.log_view.sync_entries(snap.get("logs", []) or [], update=False)
        status = (snap.get("status") or "").upper()
        if status in ("DONE", "ERROR"):
            summary = snap.get("summary") or {}
            if status == "ERROR":
                self._set_status("掃描發生錯誤", "red")
            elif getattr(session, "cancel_requested", False):
                self._set_status("已取消", "gold")
            elif summary.get("dry_run"):
                self._set_status("預覽完成", "em")
            else:
                self._set_status("掃描完成", "em")
            self._apply_summary(summary)
            self._running = False
            self._set_running(False)
            if self._on_finished and not summary.get("dry_run"):
                self._on_finished()
            self.refresh_versions()
        self._safe_update()

    def _apply_summary(self, s: dict) -> None:
        if not s:
            return
        if s.get("dry_run"):
            self.stat_new.set_value(
                format_count(s.get("items_found")), delta="預覽：可匯入的項目數"
            )
            self.stat_added.set_value("—")
            self.stat_skip.set_value("—")
            self.stat_changed.set_value("—")
        else:
            self.stat_new.set_value(format_count(s.get("new_entries")), delta="")
            self.stat_added.set_value(format_count(s.get("added_translations")))
            self.stat_skip.set_value(format_count(s.get("existing")))
            self.stat_changed.set_value(format_count(s.get("en_changed")))
        self.stat_nolang.set_value(format_count(s.get("jars_without_lang")))
        self.stat_failed.set_value(format_count(s.get("jars_failed")))

    def _reset_stats(self) -> None:
        for card in (
            self.stat_new,
            self.stat_added,
            self.stat_skip,
            self.stat_changed,
            self.stat_nolang,
            self.stat_failed,
        ):
            card.set_value("—", delta="")

    def _set_running(self, running: bool) -> None:
        self.start_btn.disabled = running
        self.preview_btn.disabled = running
        self.cancel_btn.disabled = not running

    def _set_status(self, text: str, tone: str = "neutral") -> None:
        set_chip_status(self.status_chip, text, tone)

    def _safe_update(self) -> None:
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響掃描本身
            log_debug(f"ScanPanel update 略過：{exc}")
