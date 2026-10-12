"""app/views/moddb/translate_panel.py：批次機翻（選版本／模組 → 預覽 → 機翻 → 結果）。

把資料庫裡「沒有任何譯文」的條目交給機翻引擎，結果以「AI 機翻」來源寫回；
只填空白、不覆蓋任何既有譯文，之後匯入或人工校對的譯文會自動優先。
流程與掃描匯入頁相同：背景執行緒＋畫面輪詢。
"""

from __future__ import annotations

import asyncio

import flet as ft

from app.services_impl.moddb_retranslate_service import (
    SameSourceAIRepairPreview,
    format_repair_live,
    run_moddb_retranslate_service,
)
from app.services_impl.moddb_translate_service import (
    DEFAULT_LIMIT,
    TranslateOptions,
    estimate_batch_count,
    format_duration,
    format_live,
    run_moddb_translate_service,
    tick_live,
)
from app.tasks.operation_registry import (
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.tasks.task_session import TaskSession, tag_session
from app.ui import kit
from app.ui.design import C
from app.ui.kit.inputs import BUTTON_HEIGHTS
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style, make_status_chip, set_chip_status
from app.views._log import LogView, load_ui_logging_config
from app.views.moddb import retranslation_controller
from app.views.moddb.formatting import format_count
from app.views.moddb.repair_card import create_repair_card, repair_copy
from app.views.moddb.repair_summary import repair_summary_detail_lines
from app.views.moddb.result_inspector import TranslationResultInspector
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_debug, log_info, log_warning

_POLL_INTERVAL_SEC = 0.2
# 四張統計卡的標題列同高（「檢視」按鈕出現時卡片不會變高）
STAT_HEAD_HEIGHT = BUTTON_HEIGHTS["sm"] + 2
ALL_MODS = "__all__"
LARGE_RUN_WARNING = 5000  # 不限筆數且超過這個數量時提醒額度


class TranslatePanel(ft.Column):
    """批次機翻頁籤。"""

    def __init__(self, page: ft.Page, get_db, on_finished=None, on_view_flagged=None):
        super().__init__(expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self._get_db = get_db
        self._on_finished = on_finished
        self._repair_summary: dict | None = None
        self._repair_summary_final = True
        self._flagged_total = 0
        self._flagged_resolved = 0
        self.result_inspector = TranslationResultInspector(
            page,
            on_view_flagged,
            on_repair_review_changed=self._on_repair_review_changed,
        )
        self.view_flagged_btn = self.result_inspector.button
        self._run_version = ""
        self._repair_preview = None
        self._repair_preview_db_identity: tuple[str, tuple[int, ...]] | None = None
        self._repair_preview_generation = 0
        self._repair_preview_running = False
        self._repair_mode = "quality_mismatch"
        self._kpi_repair_condition = self._repair_mode
        self.session: TaskSession | None = None
        self._running = False
        self._poller = PollerHandle()
        self.refresh_indicator = ft.Text(
            "背景更新機翻範圍中…", size=12, color=C.MUTED, visible=False
        )

        self._build_scope_card()
        self._build_run_card()
        self._bind_kpi_titles()
        self._build_repair_card()
        self.controls = [
            self.refresh_indicator,
            self.scope_card,
            self.run_card,
            self.repair_card,
        ]

    # ------------------------------------------------------------------ 建構
    def _build_scope_card(self) -> None:
        self.version_dd = kit.dropdown(
            label="遊戲版本", dense=True, width=220, on_select=self._on_scope_changed
        )
        self.mod_dd = kit.dropdown(
            label="模組", dense=True, width=260, on_select=self._on_scope_changed
        )
        self.limit_field = kit.text_field(
            "單次上限（筆，0 = 不限）",
            hint="例如 2000",
            value=str(DEFAULT_LIMIT),
            width=220,
            on_change=lambda _e: self._on_limit_changed(),
        )
        self.count_text = ft.Text("", size=13, color=C.TEXT)
        self.reuse_row = kit.SwitchRow(
            "先沿用其他版本的相同譯文",
            "其他版本已有「模組、鍵值、原文都相同」的譯文時直接補上，不呼叫 AI、不耗額度",
            True,
            on_change=lambda _e: self._on_limit_changed(),
        )
        self.cache_row = kit.SwitchRow(
            "每次寫入新快取分片",
            "結果一律也寫入「快取資料」資料夾的翻譯快取。關閉（預設）時寫入編號分片，滿 2,500 筆自動換下一片；"
            "開啟時每次執行都另存成獨立的分片。（設定裡 enable_cache_saving 關閉時不會寫入）",
            False,
        )
        self.scope_card = kit.section_card(
            "1　選擇要機翻的範圍",
            ft.Column(
                [
                    ft.Row(
                        [self.version_dd, self.mod_dd, self.limit_field],
                        spacing=12,
                        wrap=True,
                    ),
                    self.count_text,
                    self.reuse_row,
                    self.cache_row,
                    kit.hint_text(
                        "只翻譯「沒有任何譯文」的條目，不會動既有的人工、模組自帶或匯入譯文。"
                        "結果標記為「AI 機翻」（優先序最低，之後補上人工或匯入的譯文會自動蓋過）。"
                        "翻完會檢查換行、§ 格式碼、佔位符與 Patchouli 標記是否與原文一致；不一致者不寫入。"
                        "既有來源譯文也可在下方預覽並修復。"
                    ),
                ],
                spacing=10,
            ),
            icon=ft.Icons.TRANSLATE,
            tone="dia",
        )

    def _build_run_card(self) -> None:
        self.status_chip = make_status_chip("尚未開始")
        apply_status_style(self.status_chip, "neutral")
        self.progress_bar = kit.progress_bar(0, "em", height=8)
        # 批次、已處理筆數、已用時間、預估剩餘時間、預計完成時刻（每批結束更新）
        self.live_text = ft.Text("", size=12.5, color=C.MUTED, selectable=True)
        self._build_repair_progress_panel()
        ui_cfg = load_ui_logging_config(load_config)
        self.log_view = LogView(
            page=self._page,
            mode="tail",
            tail_lines=ui_cfg.get("tail_lines", 250),
            height=0,
            expand=False,
        )
        self.log_view.visible = False
        self.log_explainer = kit.hint_text(
            "執行紀錄說明：來源項目＝資料庫中需要處理的譯文；"
            "AI 修復項目＝實際送給 AI 的工作；"
            "規劃批次＝開始前依 token 上限估算的批次，不等於實際 HTTP 請求次數；"
            "略過舊快取＝不沿用先前 AI 結果，會重新呼叫 AI。"
        )
        self.start_btn = kit.button(
            "開始機翻", "primary", icon=ft.Icons.PLAY_ARROW, on_click=self.start_clicked
        )
        self.preview_btn = kit.button(
            "先預覽（不呼叫 AI）",
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
        self.stat_reused = kit.stat_card(
            "沿用其他版本",
            "—",
            icon=ft.Icons.CONTENT_COPY,
            tone="dia",
            expand=1,
            head_height=STAT_HEAD_HEIGHT,
        )
        self.stat_written = kit.stat_card(
            "已寫入（AI 機翻）",
            "—",
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
            tone="em",
            expand=1,
            head_height=STAT_HEAD_HEIGHT,
        )
        self.stat_flagged = kit.stat_card(
            "特殊字元不一致",
            "—",
            icon=ft.Icons.WARNING_AMBER,
            tone="gold",
            expand=1,
            action=self.view_flagged_btn,
            head_height=STAT_HEAD_HEIGHT,
        )
        self.stat_remaining = kit.stat_card(
            "此範圍仍未翻譯",
            "—",
            icon=ft.Icons.PENDING_OUTLINED,
            tone="neutral",
            expand=1,
            head_height=STAT_HEAD_HEIGHT,
        )
        self._stat_cards = (
            self.stat_reused,
            self.stat_written,
            self.stat_flagged,
            self.stat_remaining,
        )
        self.run_card = kit.section_card(
            "2　機翻",
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
                    self.repair_progress_panel,
                    self.live_text,
                    self.log_view,
                    self.log_explainer,
                    ft.Row(list(self._stat_cards), spacing=10),
                ],
                spacing=12,
            ),
            icon=ft.Icons.AUTO_AWESOME,
            tone="em",
        )

    def _build_repair_progress_panel(self) -> None:
        self.repair_progress_source = ft.Text("0 / 0（0%）", size=15, color=C.TEXT)
        self.repair_progress_ai = ft.Text("0 / 0", size=15, color=C.TEXT)
        self.repair_progress_batches = ft.Text("0 / 0", size=15, color=C.TEXT)
        self.repair_progress_elapsed = ft.Text("00:00", size=13, color=C.MUTED)
        self.repair_progress_eta = ft.Text("計算中", size=13, color=C.MUTED)
        self.repair_progress_current = ft.Text(
            "等待開始", size=13, color=C.TEXT, selectable=True
        )
        self.repair_progress_panel = ft.Container(
            visible=False,
            padding=12,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=10,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                "Mod 資料庫｜譯文格式修復",
                                size=14,
                                weight=ft.FontWeight.W_600,
                            ),
                            ft.Text("AI 翻譯中", size=12, color=C.EM),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text("來源項目處理進度", size=11, color=C.MUTED),
                                    self.repair_progress_source,
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            ft.Column(
                                [
                                    ft.Text("AI 修復項目", size=11, color=C.MUTED),
                                    self.repair_progress_ai,
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            ft.Column(
                                [
                                    ft.Text("規劃批次", size=11, color=C.MUTED),
                                    self.repair_progress_batches,
                                ],
                                expand=True,
                                spacing=2,
                            ),
                        ],
                        spacing=16,
                    ),
                    ft.Row(
                        [
                            ft.Text("目前執行：", size=11, color=C.MUTED),
                            self.repair_progress_current,
                        ],
                        spacing=4,
                    ),
                    ft.Row(
                        [
                            ft.Text("已耗時", size=11, color=C.MUTED),
                            self.repair_progress_elapsed,
                            ft.Text("　預估剩餘：", size=11, color=C.MUTED),
                            self.repair_progress_eta,
                        ],
                        spacing=2,
                    ),
                ],
                spacing=8,
            ),
        )

    def _bind_kpi_titles(self) -> None:
        self._kpi_titles = {
            "first": self.stat_reused,
            "second": self.stat_written,
            "third": self.stat_flagged,
            "fourth": self.stat_remaining,
        }
        self._kpi_mode = "normal"
        self._set_kpi_mode("normal")

    def _build_repair_card(self) -> None:
        controls = create_repair_card(
            self._repair_mode,
            on_mode_change=self._on_repair_mode_changed,
            on_preview=self.preview_retranslation,
            on_confirm=self.confirm_retranslation,
            on_newline_change=lambda _e=None: self._on_repair_newline_changed(),
            on_filter_change=lambda _e=None: (
                retranslation_controller.refresh_preview_cards(self)
            ),
            on_page=lambda page: retranslation_controller.refresh_preview_cards(
                self, page=page
            ),
            page_size=retranslation_controller.PREVIEW_PAGE_SIZE,
        )
        self.repair_card = controls.card
        self.repair_mode_group = controls.mode_group
        self.repair_preview_btn = controls.preview_button
        self.repair_start_btn = controls.start_button
        self.repair_newline_row = controls.newline_row
        self.repair_sync_row = controls.sync_row
        self.repair_preview_text = controls.preview_text
        self.repair_explainer = controls.explainer
        self.repair_samples = controls.samples
        self.repair_preview_search = controls.preview_search
        self.repair_preview_source_filter = controls.preview_source_filter
        self.repair_preview_issue_filter = controls.preview_issue_filter
        self.repair_preview_count = controls.preview_count
        self.repair_preview_pager = controls.pager
        self.repair_summary_text = controls.summary

    def _repair_default_text(self) -> str:
        return repair_copy(self._repair_mode)[0]

    def _repair_explanation(self) -> str:
        return repair_copy(self._repair_mode)[1]

    def _on_repair_mode_changed(self, e=None) -> None:
        value = getattr(getattr(e, "control", None), "value", None)
        if value not in {"quality_mismatch", "same_source_ai"}:
            return
        if value == self._repair_mode:
            return
        self._repair_mode = value
        self.repair_newline_row.visible = value == "quality_mismatch"
        self.repair_sync_row.visible = value == "quality_mismatch"
        self.repair_explainer.value = self._repair_explanation()
        self._reset_stats(mode="repair")
        self._clear_repair_preview()
        self._safe_update()

    def _on_repair_newline_changed(self) -> None:
        """換行數規則變了，既有預覽不再代表實際會送 AI 的項目，需重新預覽。"""
        self._reset_stats(mode="repair")
        self._clear_repair_preview()
        self._safe_update()

    # ------------------------------------------------------------------ 範圍
    def refresh_scope(self) -> None:
        """切到本頁籤時重讀資料庫的版本與模組清單，保留目前選擇。"""
        db = self._get_db()
        if (
            self._repair_preview is not None
            and self._repair_preview_db_identity != self._database_identity(db)
        ):
            self._clear_repair_preview()
        versions = db.versions() if db else []
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        if self.version_dd.value not in versions:
            self.version_dd.value = versions[0] if versions else None
        self._refresh_mods()
        self._refresh_counts()

    def apply_refresh_snapshot(self, snapshot: dict) -> None:
        """Apply worker-loaded scope and counts without blocking the page thread."""
        identity = snapshot.get("identity")
        if (
            self._repair_preview is not None
            and self._repair_preview_db_identity != identity
        ):
            self._clear_repair_preview()
        versions = snapshot.get("versions", [])
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        self.version_dd.value = snapshot.get("version")
        mods = snapshot.get("mods", [])
        kit.set_dropdown_options(
            self.mod_dd, [(ALL_MODS, "全部模組"), *((m, m) for m in mods)]
        )
        self.mod_dd.value = snapshot.get("mod", ALL_MODS)
        if snapshot.get("error"):
            self.count_text.value = f"資料載入失敗：{snapshot['error']}"
        elif not versions:
            self.count_text.value = "資料庫還沒有資料，請先到「掃描匯入」建立"
        else:
            self._set_count_text(
                snapshot.get("missing", 0), snapshot.get("reusable", 0)
            )
        self.refresh_indicator.value = str(snapshot.get("error") or "")
        self.refresh_indicator.visible = bool(snapshot.get("error"))

    @staticmethod
    def _database_identity(db):
        return retranslation_controller.database_identity(db)

    def _refresh_mods(self) -> None:
        db = self._get_db()
        version = self.version_dd.value
        mods = db.mods(version) if db and version else []
        kit.set_dropdown_options(
            self.mod_dd, [(ALL_MODS, "全部模組"), *((m, m) for m in mods)]
        )
        if self.mod_dd.value not in [ALL_MODS, *mods]:
            self.mod_dd.value = ALL_MODS

    def _on_scope_changed(self, e=None) -> None:
        if e is not None and getattr(e, "control", None) is self.version_dd:
            self.mod_dd.value = ALL_MODS
            self._refresh_mods()
        self._clear_repair_preview()
        self._refresh_counts()
        self._safe_update()

    def mod_ids(self) -> tuple[str, ...]:
        value = self.mod_dd.value
        return () if not value or value == ALL_MODS else (value,)

    def limit(self) -> int:
        try:
            return max(0, int((self.limit_field.value or "").strip() or 0))
        except ValueError:
            return DEFAULT_LIMIT

    def _refresh_counts(self) -> None:
        db, version = self._get_db(), self.version_dd.value
        if not db or not version:
            self.count_text.value = "資料庫還沒有資料，請先到「掃描匯入」建立"
            return
        missing = db.count_untranslated(version, self.mod_ids())
        reusable = (
            db.count_reusable(version, self.mod_ids()) if self.reuse_row.value else 0
        )
        self._set_count_text(missing, reusable)

    def _set_count_text(self, missing: int, reusable: int) -> None:
        limit = self.limit()
        to_ai = max(0, missing - reusable)  # 沿用其他版本的不送 AI
        will = min(to_ai, limit) if limit else to_ai
        text = f"此範圍有 {format_count(missing)} 筆未翻譯"
        if reusable:
            text += (
                f"，其中約 {format_count(reusable)} 筆其他版本已有相同譯文、"
                "開始時會直接沿用（原文不同的不算）"
            )
        text += f"；上限 {format_count(limit)} 筆" if limit else "；上限為 0（不限）"
        text += (
            f"，本次送 AI 翻譯{'最多 ' if limit else '全部 '}{format_count(will)} 筆"
            f"（預估約 {estimate_batch_count(will):,} 批）"
        )
        if not limit and will > LARGE_RUN_WARNING:
            text += "；筆數很多，會消耗大量 API 額度，建議先按「先預覽」確認"
        self.count_text.value = text

    def _on_limit_changed(self) -> None:
        self._clear_repair_preview()
        self._refresh_counts()
        self._safe_update()

    def _clear_repair_preview(self) -> None:
        retranslation_controller.clear_preview(self)

    def _update_repair_start_button(self, *, running: bool | None = None) -> None:
        retranslation_controller.update_start_button(self, running=running)

    def build_options(self, *, dry_run: bool = False) -> TranslateOptions:
        return TranslateOptions(
            version=self.version_dd.value or "",
            mod_ids=self.mod_ids(),
            limit=self.limit(),
            dry_run=dry_run,
            reuse_other_versions=bool(self.reuse_row.value),
            new_cache_shard=bool(self.cache_row.value),
            repair_newline_only=(
                self._repair_mode == "quality_mismatch"
                and bool(self.repair_newline_row.value)
            ),
            repair_sync_other_versions=(
                self._repair_mode == "quality_mismatch"
                and bool(self.repair_sync_row.value)
            ),
        )

    def start_clicked(self, _e=None, *, dry_run: bool = False) -> None:
        if self._running:
            show_snack(self._page, "機翻正在執行中", C.GOLD)
            return
        if not self.version_dd.value:
            log_warning("Mod 資料庫機翻未開始：尚未選擇遊戲版本")
            self._set_status("請先選擇遊戲版本", "red")
            self._safe_update()
            return
        self.session = tag_session(
            TaskSession(), "Mod 資料庫機翻", "moddb", page=self._page
        )
        self._run_version = str(self.version_dd.value or "")
        self._set_status("預覽中" if dry_run else "機翻中", "dia")
        self._set_running(True)
        self.progress_bar.value = 0
        self.live_text.value = ""
        self.log_view.clear()
        self.log_view.height = 0
        self.log_view.visible = False
        self._reset_stats(mode="normal")
        self._safe_update()
        launched = launch_page_operation(
            self._page,
            lambda: run_moddb_translate_service(
                self.build_options(dry_run=dry_run), self.session
            ),
            name="Mod 資料庫機翻",
            owner="moddb-translate",
            task_session=self.session,
            fallback_launcher=retranslation_controller.launch_standalone_worker,
        )
        if not launched:
            self._set_running(False)
            self._set_status("應用程式正在關閉，未啟動機翻", "gold")
            show_snack(self._page, "應用程式正在關閉，無法啟動新任務", C.GOLD)
            self._safe_update()
            return
        self._running = True
        if not self._poller.running:
            self._poller.start(self._page, self._poll)

    def preview_retranslation(self, _e=None) -> None:
        retranslation_controller.preview(self, _e)

    def confirm_retranslation(self, _e=None) -> None:
        retranslation_controller.confirm(self, _e)

    def _start_retranslation(self, preview: SameSourceAIRepairPreview) -> None:
        self._page.pop_dialog()
        if self._running:
            return
        if self._repair_preview is not preview:
            show_snack(
                self._page,
                "資料庫或範圍已變更，這份預覽已失效；請重新預覽後再開始。",
                C.GOLD,
            )
            return
        if self._database_identity(self._get_db()) != self._repair_preview_db_identity:
            self._clear_repair_preview()
            show_snack(
                self._page,
                "資料庫或來源優先序已變更，這份預覽已失效；請重新預覽後再開始。",
                C.GOLD,
            )
            self._safe_update()
            return
        self._reset_stats(mode="repair")
        options = self.build_options()
        session = tag_session(
            TaskSession(),
            "Mod 資料庫特殊字元修復"
            if preview.mode == "quality_mismatch"
            else "Mod 資料庫舊 AI 重翻",
            "moddb",
            page=self._page,
        )
        self.session = session
        self._run_version = str(options.version)
        self._set_status(
            "來源譯文修復中" if preview.mode == "quality_mismatch" else "舊 AI 重翻中",
            "dia",
        )
        self._set_running(True)
        self.progress_bar.value = 0
        self.live_text.value = ""
        self.log_view.clear()
        self.log_view.height = 0
        self.log_view.visible = False
        self.repair_summary_text.value = (
            "正在修復所選來源譯文；結果仍不一致、失敗或資料已變動時會保留原譯文。"
            if preview.mode == "quality_mismatch"
            else "重翻進行中；未成功完成的項目會保留舊譯文。"
        )
        self._safe_update()
        launched, launch_error = self._launch_retranslation_worker(
            preview, options, session
        )
        if not launched:
            self.session = None
            self._set_running(False)
            if launch_error is None:
                self._set_status("應用程式正在關閉，未啟動舊 AI 重翻", "gold")
                self.repair_summary_text.value = "應用程式正在關閉，未啟動重翻。"
                show_snack(self._page, "應用程式正在關閉，無法啟動新任務", C.GOLD)
            else:
                self._set_status("重翻無法啟動", "red")
                self.repair_summary_text.value = f"重翻未啟動：{launch_error}"
            self._safe_update()
            return
        self._running = True
        if not self._poller.running:
            self._poller.start(self._page, self._poll)

    def _launch_retranslation_worker(self, preview, options, session):
        """Register the repair worker with the page-owned operation lifecycle."""
        name = (
            "Mod 資料庫特殊字元修復"
            if preview.mode == "quality_mismatch"
            else "Mod 資料庫舊 AI 重翻"
        )
        try:
            launched = launch_page_operation(
                self._page,
                lambda: run_moddb_retranslate_service(
                    options,
                    session,
                    preview.entries,
                    mode=preview.mode,
                    review_preview=preview,
                ),
                name=name,
                owner="moddb-retranslate",
                task_session=session,
                commit=CommitPolicy.PARTIAL_ALLOWED,
                durability=DurabilityPolicy.USER_ACTION,
                shutdown=ShutdownPolicy.CANCEL_AND_DRAIN,
                fallback_launcher=retranslation_controller.launch_standalone_worker,
            )
        except Exception as exc:  # noqa: BLE001 - caller restores the UI state
            log_warning(f"{name}無法啟動：{exc!r}")
            return False, exc
        return launched, None

    def cancel_clicked(self, _e=None) -> None:
        if self.session is None or not self._running:
            return
        self.session.request_cancel()
        log_info(
            "Mod 資料庫機翻：使用者要求取消，等待目前批次結束（已完成的項目已提交）"
        )
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
        """輪詢失敗時收尾：恢復按鈕、狀態標明原因，避免畫面卡在「機翻中」。"""
        self._running = False
        self._set_running(False)
        self._set_status("畫面更新失敗，請查看日誌（任務可能已結束）", "red")
        self._safe_update()

    async def _poll(self, alive=lambda: True) -> None:
        while alive() and self._running:
            try:
                self.sync_from_session()
            except Exception as exc:  # noqa: BLE001 - 輪詢失敗不能讓畫面永遠卡在「執行中」
                log_warning(f"機翻輪詢中止：{exc!r}")
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
        log_entries = snap.get("logs", []) or []
        self.log_view.sync_entries(log_entries, update=False)
        has_logs = bool(log_entries)
        self.log_view.height = 200 if has_logs else 0
        self.log_view.visible = has_logs
        status = (snap.get("status") or "").upper()
        summary = snap.get("summary") or {}
        if summary:
            self._apply_summary(summary, final=status in ("DONE", "ERROR"))
        live = summary.get("live")
        if summary.get("operation") in {
            "retranslate_same_source_ai",
            "repair_special_character_mismatch",
        }:
            self._apply_repair_live(live, summary=summary)
        if live and status not in ("DONE", "ERROR"):
            current_live = tick_live(live)
            if summary.get("operation") == "repair_special_character_mismatch":
                self.live_text.value = format_repair_live(current_live)
            else:
                self.live_text.value = format_live(current_live)
        if status in ("DONE", "ERROR"):
            if summary.get("batches"):
                self.live_text.value = (
                    f"共送出 {summary['batches']:,} 批，"
                    f"耗時 {format_duration(summary.get('elapsed_sec'))}"
                )
            if status == "ERROR":
                partial = summary.get("status")
                self._set_status(
                    f"未完成（{partial}）"
                    if partial not in (None, "DONE")
                    else "機翻發生錯誤",
                    "red",
                )
            elif getattr(session, "cancel_requested", False):
                self._set_status("已取消", "gold")
            elif summary.get("dry_run"):
                self._set_status("預覽完成", "em")
            elif summary.get("status") not in (None, "DONE"):
                self._set_status(f"未完成（{summary.get('status')}）", "gold")
            else:
                if summary.get("operation") == "repair_special_character_mismatch":
                    self._set_status("譯文修復完成", "em")
                elif summary.get("operation") == "retranslate_same_source_ai":
                    self._set_status("舊 AI 重翻完成", "em")
                else:
                    self._set_status("機翻完成", "em")
            self._running = False
            self._set_running(False)
            if self._on_finished and not summary.get("dry_run"):
                self._on_finished()
            self._refresh_counts()
        self._safe_update()

    def _apply_summary(self, s: dict, *, final: bool = True) -> None:
        if not s:
            return
        if s.get("operation") in {
            "retranslate_same_source_ai",
            "repair_special_character_mismatch",
        }:
            self._apply_repair_summary(s, final=final)
            return
        self._apply_translation_summary(s)

    def _apply_repair_summary(self, s: dict, *, final: bool) -> None:
        self._repair_summary = dict(s)
        self._repair_summary_final = final
        quality_repair = s.get("operation") == "repair_special_character_mismatch"
        self.repair_progress_panel.visible = True
        self.result_inspector.set_repair_results(s)
        self._set_kpi_mode(
            "repair",
            repair_condition=(
                "quality_mismatch" if quality_repair else "same_source_ai"
            ),
        )
        self.stat_reused.set_value(
            format_count(s.get("candidates")),
            delta="本次預覽選中的來源譯文" if quality_repair else "本次預覽選中的候選",
        )
        self.stat_written.set_value(
            format_count(s.get("updated")),
            delta="原來源更新成功" if quality_repair else "compare-and-set 成功更新",
        )
        self.stat_flagged.set_value(
            format_count(s.get("flagged") if quality_repair else s.get("unchanged")),
            delta=(
                "AI 草稿待人工確認，原譯文保留"
                if quality_repair
                else "AI 結果與舊譯文相同"
            ),
        )
        self.stat_remaining.set_value(
            format_count(s.get("remaining") if final else None)
        )
        status = str(s.get("status") or "UNKNOWN").upper()
        progress = (
            ("修復完成" if quality_repair else "重翻完成")
            if final and status == "DONE"
            else "目前部分進度"
        )
        state_text = (
            ""
            if status == "DONE" and final
            else f"；狀態 {status}，數字為已提交／已確認的部分結果"
        )
        self.repair_summary_text.value = (
            f"{progress}：待處理來源項目 {s.get('candidates', 0)}；"
            f"更新 {s.get('updated', 0)}"
            + (
                f"（另同步其他版本 {s.get('synced_other_versions', 0)}）"
                if s.get("sync_other_versions")
                else ""
            )
            + "；"
            f"重翻後仍相同 {s.get('unchanged', 0)}；待人工確認 {s.get('flagged', 0)}；"
            f"資料已變動跳過 {s.get('skipped_changed', 0)}；"
            f"失敗 {s.get('failed', 0)}；"
            + (
                f"範圍內仍不一致 {s.get('remaining', 0)}"
                if quality_repair
                else f"範圍內仍符合條件 {s.get('remaining', 0)}"
            )
            + f"{state_text}"
            + (
                f"；快取未同步事件 {s.get('cache_failed', 0)}"
                if s.get("cache_failed")
                else ""
            )
            + (f"；最後錯誤：{s['last_error']}" if s.get("last_error") else "")
        )
        detail_parts = repair_summary_detail_lines(s)
        if detail_parts:
            self.repair_summary_text.value += "\n" + "\n".join(detail_parts)

    def _apply_repair_live(self, live: dict | None, *, summary: dict) -> None:
        """Render repair-only counters without mixing them with normal translation KPIs."""
        if not live:
            live = {
                "source_processed": summary.get("processed_candidates", 0),
                "source_total": summary.get("candidates", 0),
                "ai_completed": summary.get("ai_submitted_items", 0),
                "ai_total": summary.get("candidates", 0),
                "planned_batches_done": summary.get("batches", 0),
                "planned_batches_total": summary.get(
                    "planned_batches", summary.get("batches", 0)
                ),
                "actual_requests": summary.get("batches", 0),
                "elapsed_sec": summary.get("elapsed_sec", 0),
                "eta_sec": 0 if summary.get("status") == "DONE" else None,
            }
        source_done = int(live.get("source_processed", live.get("processed", 0)) or 0)
        source_total = int(live.get("source_total", live.get("total", 0)) or 0)
        source_percent = 100 * source_done / max(source_total, 1)
        self.repair_progress_source.value = (
            f"{source_done:,} / {source_total:,}（{source_percent:.0f}%）"
        )
        self.repair_progress_ai.value = (
            f"{int(live.get('ai_completed', 0) or 0):,} / "
            f"{int(live.get('ai_total', 0) or 0):,}"
        )
        self.repair_progress_batches.value = (
            f"{int(live.get('planned_batches_done', live.get('batch_done', 0)) or 0):,} / "
            f"{int(live.get('planned_batches_total', live.get('batch_est', 0)) or 0):,}"
        )
        self.repair_progress_elapsed.value = format_duration(live.get("elapsed_sec"))
        self.repair_progress_eta.value = (
            format_duration(live.get("eta_sec"))
            if live.get("eta_sec") is not None
            else "計算中"
        )
        profile = live.get("current_profile")
        batch = live.get("current_batch")
        batch_total = live.get("current_batch_total")
        status = live.get("current_status") or "等待開始"
        model = live.get("model_label")
        if profile and batch:
            current = f"{status}｜{profile} 項目（第 {batch} / {batch_total} 批）"
        else:
            current = str(status)
        if model:
            current += f"｜模型：{model}"
        self.repair_progress_current.value = current

    def _on_repair_review_changed(self, unresolved_count: int) -> None:
        if (
            self._repair_summary is None
            or self._repair_summary.get("operation")
            != "repair_special_character_mismatch"
        ):
            return
        summary = dict(self._repair_summary)
        summary["flagged"] = max(0, int(unresolved_count))
        self._apply_repair_summary(summary, final=self._repair_summary_final)
        self._safe_update()

    def _apply_translation_summary(self, s: dict) -> None:
        self._set_kpi_mode("normal")
        if s.get("dry_run"):
            self.stat_remaining.set_value(
                format_count(s.get("remaining")),
                delta=f"本次將翻譯 {s.get('candidates', 0):,} 筆",
            )
            return
        self.stat_reused.set_value(format_count(s.get("reused")))
        self.stat_written.set_value(
            format_count(s.get("written")),
            delta=f"AI 回傳 {s.get('translated', 0):,} 筆",
        )
        self._flagged_total = int(s.get("flagged") or 0)
        self._flagged_resolved = 0
        self.stat_flagged.set_value(
            format_count(s.get("flagged")),
            delta="未寫入，詳見日誌" if s.get("flagged") else "",
        )
        self.result_inspector.set_normal_results(s, self._run_version)
        self.stat_remaining.set_value(format_count(s.get("remaining")))

    def resolve_flagged(self, entry_id: int) -> None:
        """條目校對裡手動修好一筆「特殊字元不一致」：卡片數量同步減少。"""
        if not self.result_inspector.drop_normal_entry(entry_id):
            return
        self._flagged_resolved += 1
        left = max(0, self._flagged_total - self._flagged_resolved)
        self.stat_flagged.set_value(
            format_count(left), delta="未寫入，詳見日誌" if left else "已全部處理"
        )
        self._safe_update()

    def _set_kpi_mode(self, mode: str, *, repair_condition: str | None = None) -> None:
        self._kpi_mode = mode
        if repair_condition is not None:
            self._kpi_repair_condition = repair_condition
        elif mode == "repair":
            self._kpi_repair_condition = self._repair_mode
        labels = (
            (
                "本次來源譯文"
                if self._kpi_repair_condition == "quality_mismatch"
                else "本次候選",
                "成功更新來源譯文"
                if self._kpi_repair_condition == "quality_mismatch"
                else "成功更新 AI 譯文",
                "AI 草稿待人工確認"
                if self._kpi_repair_condition == "quality_mismatch"
                else "重翻後仍相同",
                "範圍內仍不一致"
                if self._kpi_repair_condition == "quality_mismatch"
                else "範圍內仍符合修復條件",
            )
            if mode == "repair"
            else (
                "沿用其他版本",
                "已寫入（AI 機翻）",
                "特殊字元不一致",
                "此範圍仍未翻譯",
            )
        )
        for key, label in zip(
            ("first", "second", "third", "fourth"), labels, strict=True
        ):
            self._kpi_titles[key].set_title(
                label,
                tooltip=(
                    "只計入 AI 有效回傳且內容與舊譯文完全相同的筆數；格式不符、失敗與資料競態跳過各自另計。"
                    if key == "third"
                    and mode == "repair"
                    and self._kpi_repair_condition == "same_source_ai"
                    else None
                ),
            )
        self.result_inspector.set_mode(mode, self._kpi_repair_condition)

    def _reset_stats(self, *, mode: str = "normal") -> None:
        self._repair_summary = None
        self.repair_progress_panel.visible = False
        self.repair_progress_current.value = "等待開始"
        self.repair_progress_source.value = "0 / 0（0%）"
        self.repair_progress_ai.value = "0 / 0"
        self.repair_progress_batches.value = "0 / 0"
        self.repair_progress_elapsed.value = "00:00"
        self.repair_progress_eta.value = "計算中"
        for card in self._stat_cards:
            card.set_value("—", delta="")
        self.result_inspector.clear()
        self._set_kpi_mode(mode)

    def _set_running(self, running: bool) -> None:
        self.start_btn.disabled = running
        self.preview_btn.disabled = running
        self.cancel_btn.disabled = not running
        self.repair_preview_btn.disabled = running or self._repair_preview_running
        self.repair_mode_group.disabled = running or self._repair_preview_running
        self._update_repair_start_button(running=running)

    def _set_status(self, text: str, tone: str = "neutral") -> None:
        set_chip_status(self.status_chip, text, tone)

    def _safe_update(self) -> None:
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響機翻本身
            log_debug(f"TranslatePanel update 略過：{exc}")
