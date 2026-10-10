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
from app.ui.status_chip import apply_status_style, set_chip_status
from app.views._log import LogView, load_ui_logging_config
from app.views.moddb import retranslation_controller
from app.views.moddb.formatting import format_count
from app.views.moddb.repair_card import create_repair_card, repair_copy
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
        self.result_inspector = TranslationResultInspector(page, on_view_flagged)
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
            "同時寫入翻譯快取",
            "結果除了寫進資料庫，也寫入「快取資料」資料夾的翻譯快取，原本的機器翻譯頁也能直接命中。"
            "（設定裡 enable_cache_saving 關閉時不會寫入）",
            True,
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
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"))
        apply_status_style(self.status_chip, "neutral")
        self.progress_bar = kit.progress_bar(0, "em", height=8)
        # 批次、已處理筆數、已用時間、預估剩餘時間、預計完成時刻（每批結束更新）
        self.live_text = ft.Text("", size=12.5, color=C.MUTED, selectable=True)
        ui_cfg = load_ui_logging_config(load_config)
        self.log_view = LogView(
            page=self._page, mode="tail", tail_lines=ui_cfg.get("tail_lines", 250)
        )
        self.log_view.height = 200
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
                    self.live_text,
                    self.log_view,
                    ft.Row(list(self._stat_cards), spacing=10),
                ],
                spacing=12,
            ),
            icon=ft.Icons.AUTO_AWESOME,
            tone="em",
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
        )
        self.repair_card = controls.card
        self.repair_mode_group = controls.mode_group
        self.repair_preview_btn = controls.preview_button
        self.repair_start_btn = controls.start_button
        self.repair_preview_text = controls.preview_text
        self.repair_explainer = controls.explainer
        self.repair_samples = controls.samples
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
        self.repair_explainer.value = self._repair_explanation()
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
            write_cache=bool(self.cache_row.value),
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
                    options, session, preview.entries, mode=preview.mode
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
        self.log_view.sync_entries(snap.get("logs", []) or [], update=False)
        status = (snap.get("status") or "").upper()
        summary = snap.get("summary") or {}
        if summary:
            self._apply_summary(summary, final=status in ("DONE", "ERROR"))
        live = summary.get("live")
        if live and status not in ("DONE", "ERROR"):
            self.live_text.value = format_live(
                tick_live(live)
            )  # 已用時間每次輪詢都更新
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
        quality_repair = s.get("operation") == "repair_special_character_mismatch"
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
                "AI 結果仍不一致，舊譯文保留"
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
            f"{progress}：候選 {s.get('candidates', 0)}；更新 {s.get('updated', 0)}；"
            f"重翻後仍相同 {s.get('unchanged', 0)}；格式檢查未通過 {s.get('flagged', 0)}；"
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
        detail_parts = self._repair_summary_detail_lines(s)
        if detail_parts:
            self.repair_summary_text.value += "\n" + "\n".join(detail_parts)

    def _repair_summary_detail_lines(self, s: dict) -> list[str]:
        """Format repair-only units and cache receipts shown beneath the summary."""
        detail_parts = []
        if "ai_representatives" in s:
            detail_parts.append(
                f"AI 代表計劃 {s.get('ai_representatives', 0)}；"
                f"實際送入引擎 {s.get('ai_submitted_items', 0)} items"
                "（不是 API/HTTP 次數）；"
                f"驗證通過 {s.get('ai_validated_items', 0)}；"
                f"等價映射候選 {s.get('dedup_mapped_candidates', 0)}；"
                f"已共用驗證結果 {s.get('dedup_reused_candidates', 0)}"
            )
            detail_parts.append(
                f"來源列進度 {s.get('processed_candidates', 0)}/"
                f"{s.get('candidates', 0)}；未送出候選 "
                f"{s.get('not_submitted_candidates', 0)}；未處理候選 "
                f"{s.get('unprocessed_candidates', 0)}"
            )
        if "cache_keys_changed" in s:
            changed = s.get("cache_keys_changed")
            saved = s.get("cache_keys_saved")
            detail_parts.append(
                "快取 key 變更 "
                f"{changed if changed is not None else '未知'}；成功落盤 "
                f"{saved if saved is not None else '未知'}"
                + (f"（{s['cache_stats_note']}）" if s.get("cache_stats_note") else "")
                + f"；新增失敗來源列 {s.get('cache_add_failed', 0)}；"
                f"尚未確認落盤 keys {s.get('cache_save_failed', 0)}"
            )
        return detail_parts

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
        self.stat_flagged.set_value(
            format_count(s.get("flagged")),
            delta="未寫入，詳見日誌" if s.get("flagged") else "",
        )
        self.result_inspector.set_normal_results(s, self._run_version)
        self.stat_remaining.set_value(format_count(s.get("remaining")))

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
                "AI 結果未通過格式檢查"
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
