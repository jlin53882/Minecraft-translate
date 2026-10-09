"""app/views/moddb/scan_panel.py：掃描匯入（選版本 → 選 mods 資料夾 → 掃描 → 結果）。

掃描在背景執行緒進行（``run_moddb_scan_service``），畫面以輪詢同步進度與日誌，
流程與機器翻譯頁相同。已存在的條目一律跳過（只新增、不覆蓋）。
"""

from __future__ import annotations

import asyncio
import threading

import flet as ft

from app.services_impl.moddb_service import (
    SRC_CUSTOM,
    SRC_I18N,
    SRC_JAR_TW,
    SRC_MANUAL,
    SRC_SUBTITLE,
    ScanOptions,
    current_settings,
    pack_format_hint,
    run_moddb_scan_service,
    version_choices,
)
from app.tasks.operation_registry import launch_page_operation
from app.tasks.task_session import TaskSession, tag_session
from app.ui import design, kit
from app.ui.design import C
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style, set_chip_status
from app.views._log import LogView, load_ui_logging_config
from app.views.moddb.formatting import format_count
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_debug, log_info, log_warning

_POLL_INTERVAL_SEC = 0.2
# 翻譯 ZIP 匯入時可選的「譯文來源」標記（預設：自訂補充）
ZIP_SOURCES = (SRC_CUSTOM, SRC_SUBTITLE, SRC_I18N, SRC_JAR_TW, SRC_MANUAL)


class ScanPanel(ft.Column):
    """掃描匯入頁籤。"""

    def __init__(
        self,
        page: ft.Page,
        file_picker: ft.FilePicker,
        get_db,
        on_finished=None,
        *,
        defer_initial_refresh: bool = False,
    ):
        super().__init__(expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self.file_picker = file_picker
        self._get_db = get_db
        self._on_finished = on_finished
        self.session: TaskSession | None = None
        self._running = False
        self._poller = PollerHandle()
        self._versions: list[str] = []
        self._db_versions: set[str] = set()
        self.mode = "jar"
        self.refresh_indicator = ft.Text(
            "背景更新版本清單中…", size=12, color=C.MUTED, visible=False
        )

        self._build_version_card()
        self._build_options_card()
        self._build_run_card()
        self.controls = [
            self.refresh_indicator,
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
        # ModDbView defers this until the scan tab is selected. The overview
        # loader owns the first database connection and must not be preceded by
        # a synchronous open from this hidden panel during page construction.
        if not defer_initial_refresh:
            self.refresh_versions()

    # ------------------------------------------------------------------ 建構
    def _build_version_card(self) -> None:
        self.version_field = kit.text_field(
            "遊戲版本",
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
            "1　選擇遊戲版本（可輸入或從下方清單選擇）",
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
        self.mode_seg = kit.Segmented(
            [("jar", "mods 資料夾（jar）"), ("zip", "翻譯 ZIP（已翻譯）")],
            "jar",
            self._on_mode,
        )
        self.path_label = kit.section_label("mods 資料夾")
        self.path_field = kit.text_field(
            hint="請選擇 mods 資料夾",
            icon=ft.Icons.FOLDER_OUTLINED,
            mono=True,
            expand=True,
            path=True,
        )
        self.zip_pick_btn = kit.pick_button(
            ft.Icons.FOLDER_ZIP_OUTLINED, "選擇 ZIP 檔", self._pick_zip
        )
        self.zip_pick_btn.visible = False
        self._source_touched = False
        self.source_dd = kit.dropdown(
            label="譯文來源標記",
            dense=True,
            value=str(SRC_CUSTOM),
            options=[],
            on_select=self._on_source_selected,
        )
        self._refresh_source_options()
        self.source_dd.visible = False
        self.mode_note = kit.hint_text(
            "ZIP 內的 lang／patchouli 的 zh_tw 一律不判讀、不清理、不套規則，直接匯入（包含沒有中文的值）。"
            "ZIP 沒有 en_us 時原文先留空，之後掃描同版本的 jar 會自動補上。"
        )
        self.mode_note.visible = False
        self.clean_row = kit.SwitchRow(
            "判斷並清理英文內容",
            "開：與機器翻譯相同，只匯入需要翻譯的英文（略過技術 ID、過短字串等）；關：所有英文字串逐字匯入",
            True,
        )
        self.nested_row = kit.SwitchRow(
            "掃描內嵌 jar",
            "META-INF/jarjar、META-INF/jars 內的子模組；翻譯 ZIP 則掃描 ZIP 內任何位置的 jar",
            True,
        )
        self.jar_tr_row = kit.SwitchRow(
            "讀取模組自帶 zh_tw／zh_cn", "以來源「模組自帶繁中」「簡中轉繁」匯入", True
        )
        self.cn_row = kit.SwitchRow(
            "簡中自動轉繁（OpenCC s2twp）",
            "只在沒有繁中時補上，標記為「簡中轉繁」",
            True,
        )
        self.rules_row = kit.SwitchRow(
            "套用替換規則",
            "與語系合併相同：自帶繁中與簡轉繁後的文字都會套用「替換規則」",
            True,
        )
        self.book_row = kit.SwitchRow(
            "包含 Patchouli 手冊", "書籍內文也匯入，供跨版本沿用", True, divider=False
        )
        self.options_card = kit.section_card(
            "2　選擇匯入來源與選項",
            ft.Column(
                [
                    self.mode_seg,
                    self.path_label,
                    ft.Row(
                        [
                            self.path_field,
                            self.zip_pick_btn,
                            kit.pick_button(
                                ft.Icons.FOLDER_OPEN_OUTLINED,
                                "選擇資料夾",
                                self._pick_folder,
                            ),
                        ],
                        spacing=8,
                    ),
                    self.source_dd,
                    self.mode_note,
                    ft.Column(
                        [
                            self.clean_row,
                            self.nested_row,
                            self.jar_tr_row,
                            self.cn_row,
                            self.rules_row,
                            self.book_row,
                        ],
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
            "沒有語言檔的檔案",
            "—",
            icon=ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
            tone="neutral",
            expand=1,
        )
        self.stat_failed = kit.stat_card(
            "失敗（整包未寫入）", "—", icon=ft.Icons.ERROR_OUTLINE, tone="red", expand=1
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

    # ------------------------------------------------------------ 譯文來源
    def _on_source_selected(self, _e=None) -> None:
        self._source_touched = True

    def _refresh_source_options(self) -> None:
        """預設值取自 config 的 translation_db.zip_source（沒設定則「自訂補充」），
        每次切到本頁籤重讀；使用者在畫面上手動選過就保留他的選擇。
        """
        # 選項順序跟隨設定的 translation_db.priority（優先序高的在前）
        settings = current_settings()
        allowed = [
            *ZIP_SOURCES,
            *settings.source_catalog.custom_codes,
        ]  # 自訂來源也能當 ZIP 的來源標記
        order = [c for c in settings.priority if c in allowed]
        order += [c for c in allowed if c not in order]
        kit.set_dropdown_options(
            self.source_dd, [(str(c), settings.source_catalog.label(c)) for c in order]
        )
        if not self._source_touched:
            default = settings.zip_source
            if default not in allowed:
                default = SRC_CUSTOM
            self.source_dd.value = str(default)

    # --------------------------------------------------------------- 版本清單
    def refresh_versions(self) -> None:
        self._refresh_source_options()
        db = self._get_db()
        self._db_versions = set(db.versions()) if db else set()
        self._versions = version_choices(db)
        self._render_versions()
        self._on_version_typed()

    def apply_refresh_snapshot(self, snapshot: dict) -> None:
        """Apply the background-loaded version choices and database markers."""
        self._refresh_source_options()
        self._versions = snapshot.get("versions", [])
        self._db_versions = snapshot.get("database_versions", set())
        self._render_versions()
        self._on_version_typed()
        self.refresh_indicator.value = str(snapshot.get("error") or "")
        self.refresh_indicator.visible = bool(snapshot.get("error"))

    def _render_versions(self) -> None:
        q = (self.version_search.value or "").strip().lower()
        chosen = (self.version_field.value or "").strip()
        in_db = self._db_versions
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
        self.start_btn.content = self._start_label()
        self.version_hint.value = pack_format_hint(version)
        self._render_versions()

    def version(self) -> str:
        return (self.version_field.value or "").strip()

    # ------------------------------------------------------------------ 事件
    def _pick_folder(self, _e) -> None:
        self._page.run_task(self._async_pick_folder)

    def _pick_zip(self, _e) -> None:
        self._page.run_task(self._async_pick_zip)

    async def _async_pick_zip(self) -> None:
        result = await self.file_picker.pick_files(
            dialog_title="選擇翻譯 ZIP",
            allow_multiple=False,
            allowed_extensions=["zip"],
        )
        if result:
            self.path_field.value = result[0].path
            self._safe_update()

    async def _async_pick_folder(self) -> None:
        result = await self.file_picker.get_directory_path()
        if result:
            self.path_field.value = result
            self._safe_update()

    def _on_mode(self, key: str) -> None:
        """切換匯入來源：mods 資料夾（jar）或翻譯 ZIP（已翻譯，zh_tw 直接匯入）。"""
        self.mode = key
        zip_mode = key == "zip"
        self.path_label.value = (
            "翻譯 ZIP（檔案或資料夾）" if zip_mode else "mods 資料夾"
        )
        self.path_field.hint_text = (
            "請選擇 ZIP 檔，或放有 ZIP 的資料夾" if zip_mode else "請選擇 mods 資料夾"
        )
        self.zip_pick_btn.visible = zip_mode
        self.source_dd.visible = zip_mode
        self.mode_note.visible = zip_mode
        self.jar_tr_row.visible = not zip_mode
        self.rules_row.visible = not zip_mode
        self.start_btn.content = self._start_label()
        self._safe_update()

    def _start_label(self) -> str:
        verb = "匯入" if self.mode == "zip" else "掃描"
        version = self.version()
        return f"開始{verb} {version}" if version else f"開始{verb}"

    def build_options(self, *, dry_run: bool = False) -> ScanOptions:
        translated = self.mode == "zip"
        return ScanOptions(
            version=self.version(),
            read_jar_translations=True if translated else self.jar_tr_row.value,
            convert_cn=self.cn_row.value,
            scan_nested=self.nested_row.value,
            include_patchouli=self.book_row.value,
            apply_rules=False if translated else self.rules_row.value,
            dry_run=dry_run,
            clean_english=self.clean_row.value,
            translated=translated,
            translation_source=int(self.source_dd.value or SRC_CUSTOM),
        )

    def start_clicked(self, _e=None, *, dry_run: bool = False) -> None:
        if self._running:
            show_snack(self._page, "掃描正在執行中", C.GOLD)
            return
        folder = (self.path_field.value or "").strip()
        if not self.version():
            log_warning("Mod 資料庫掃描未開始：尚未選擇或輸入遊戲版本")
            self._set_status("請先選擇或輸入遊戲版本", "red")
            self._safe_update()
            return
        if not folder:
            log_warning("Mod 資料庫掃描未開始：尚未選擇來源路徑")
            self._set_status(
                "請先選擇 ZIP 檔或資料夾"
                if self.mode == "zip"
                else "請先選擇 mods 資料夾",
                "red",
            )
            self._safe_update()
            return
        self.session = tag_session(
            TaskSession(), "Mod 資料庫掃描", "moddb", page=self._page
        )
        self._set_status("預覽中" if dry_run else "掃描中", "dia")
        self._set_running(True)
        self.progress_bar.value = 0
        self.log_view.clear()
        self._reset_stats()
        self._safe_update()
        launched = launch_page_operation(
            self._page,
            lambda: run_moddb_scan_service(
                folder, self.build_options(dry_run=dry_run), self.session
            ),
            name="Mod 資料庫掃描",
            owner="moddb-scan",
            task_session=self.session,
            fallback_launcher=lambda target: threading.Thread(
                target=target, daemon=True
            ).start(),
        )
        if not launched:
            self._set_running(False)
            self._set_status("應用程式正在關閉，未啟動掃描", "gold")
            show_snack(self._page, "應用程式正在關閉，無法啟動新任務", C.GOLD)
            self._safe_update()
            return
        self._running = True
        if not self._poller.running:
            self._poller.start(self._page, self._poll)

    def cancel_clicked(self, _e=None) -> None:
        if self.session is None or not self._running:
            return
        self.session.request_cancel()
        log_info("Mod 資料庫掃描：使用者要求取消，等待目前處理中的檔案停止")
        self.cancel_btn.disabled = True
        self._set_status("取消中…", "gold")
        self._safe_update()

    def will_unmount(self) -> None:
        self._poller.stop()

    def resume(self) -> None:
        """重新掛載（換頁後切回來）：任務還在追蹤就接續輪詢，已結束的補上最終狀態與摘要。"""
        if self.session is not None and self._running and not self._poller.running:
            self._poller.start(self._page, self._poll)

    def _abort_polling(self) -> None:
        """輪詢失敗時收尾：恢復按鈕、狀態標明原因，避免畫面卡在「掃描中」。"""
        self._running = False
        self._set_running(False)
        self._set_status("畫面更新失敗，請查看日誌（任務可能已結束）", "red")
        self._safe_update()

    # ------------------------------------------------------------------ 輪詢
    async def _poll(self, alive=lambda: True) -> None:
        while alive() and self._running:
            try:
                self.sync_from_session()
            except Exception as exc:  # noqa: BLE001 - 輪詢失敗不能讓畫面永遠卡在「執行中」
                log_warning(f"掃描輪詢中止：{exc!r}")
                self._abort_polling()
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
            adopted = s.get("adopted") or 0
            self.stat_added.set_value(
                format_count(s.get("added_translations")),
                delta=f"另補上原文 {adopted:,} 筆" if adopted else "",
            )
            self.stat_skip.set_value(format_count(s.get("existing")))
            self.stat_changed.set_value(format_count(s.get("en_changed")))
        self.stat_nolang.set_value(format_count(s.get("jars_without_lang")))
        skipped = s.get("skipped_nested") or 0
        self.stat_failed.set_value(
            format_count(s.get("jars_failed")),
            delta=f"另略過內嵌 jar {skipped:,} 個" if skipped else "",
        )

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
