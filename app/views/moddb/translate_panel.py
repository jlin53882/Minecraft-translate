"""app/views/moddb/translate_panel.py：批次機翻（選版本／模組 → 預覽 → 機翻 → 結果）。

把資料庫裡「沒有任何譯文」的條目交給機翻引擎，結果以「AI 機翻」來源寫回；
只填空白、不覆蓋任何既有譯文，之後匯入或人工校對的譯文會自動優先。
流程與掃描匯入頁相同：背景執行緒＋畫面輪詢。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import flet as ft

from app.services_impl.moddb_retranslate_service import (
    SameSourceAIRepairPreview,
    cache_profile_label,
    preview_same_source_ai_retranslation,
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
from app.tasks.operation_registry import launch_page_operation
from app.tasks.task_session import TaskSession, tag_session
from app.ui import kit
from app.ui.design import C
from app.ui.kit.inputs import BUTTON_HEIGHTS
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style, set_chip_status
from app.views._log import LogView, load_ui_logging_config
from app.views.moddb.formatting import (
    format_count,
    source_label,
)
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
        # on_view_flagged(條目 id 清單, {id: AI 譯文}, 版本)：跳到條目校對檢視「特殊字元不一致」的條目
        self._on_view_flagged = on_view_flagged
        self._flagged: dict[int, str] = {}
        self._run_version = ""
        self._repair_preview: SameSourceAIRepairPreview | None = None
        self._repair_preview_db_identity: tuple[str, tuple[int, ...]] | None = None
        self.session: TaskSession | None = None
        self._running = False
        self._poller = PollerHandle()

        self._build_scope_card()
        self._build_run_card()
        self._build_repair_card()
        self.controls = [self.scope_card, self.run_card, self.repair_card]

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
                        "翻完會檢查換行、§ 格式碼、%s 佔位符是否與原文一致，不一致者不寫入並在日誌列出。"
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
        self.view_flagged_btn = kit.button(
            "檢視",
            "secondary",
            size="sm",
            icon=ft.Icons.FILTER_ALT_OUTLINED,
            tooltip="跳到條目校對，只看這次「特殊字元不一致、沒寫入」的條目（已預填 AI 譯文）",
            on_click=lambda _e: self._view_flagged(),
        )
        self.view_flagged_btn.visible = False
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

    def _build_repair_card(self) -> None:
        self.repair_preview_btn = kit.button(
            "預覽符合條件的舊 AI 譯文",
            "secondary",
            icon=ft.Icons.VISIBILITY_OUTLINED,
            on_click=self.preview_retranslation,
        )
        self.repair_start_btn = kit.button(
            "重新翻譯",
            "primary",
            icon=ft.Icons.AUTO_AWESOME,
            on_click=self.confirm_retranslation,
        )
        self.repair_preview_text = ft.Text(
            "來源：AI 機翻。人工、模組自帶及其他來源不會被重新翻譯。",
            size=13,
            color=C.TEXT,
            selectable=True,
        )
        self.repair_samples = ft.Column(spacing=4)
        self.repair_summary_text = ft.Text(
            "重翻結果會只更新 AI 機翻來源，不會改動人工或其他來源。",
            size=12.5,
            color=C.MUTED,
            selectable=True,
        )
        self.repair_card = kit.section_card(
            "3　舊 AI 譯文修復",
            ft.Column(
                [
                    kit.hint_text(
                        "重新翻譯「目前生效來源為 AI 機翻，且譯文與原文完全相同」的舊資料。"
                        "會略過舊快取；只更新所選版本／模組中的 AI 來源，不會跨版本同步。"
                        "PR #177 的同文重試仍依設定執行。操作方式：先按「預覽符合條件的舊 AI 譯文」，"
                        "檢查候選筆數與樣本，再按「重新翻譯」並確認開始。版本、模組或筆數上限變更後，"
                        "預覽會失效，必須重新預覽。"
                    ),
                    ft.Row(
                        [self.repair_preview_btn, self.repair_start_btn],
                        spacing=10,
                        wrap=True,
                    ),
                    self.repair_preview_text,
                    self.repair_samples,
                    self.repair_summary_text,
                ],
                spacing=10,
            ),
            icon=ft.Icons.BUILD_CIRCLE_OUTLINED,
            tone="gold",
        )

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

    @staticmethod
    def _database_identity(db) -> tuple[str, tuple[int, ...]] | None:
        if db is None:
            return None
        path = getattr(db, "path", None)
        path_identity = (
            str(Path(path).resolve()) if path is not None else f"object:{id(db)}"
        )
        return path_identity, tuple(getattr(db, "priority", ()))

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
        self._repair_preview = None
        self._repair_preview_db_identity = None
        self.repair_preview_text.value = (
            "來源：AI 機翻。人工、模組自帶及其他來源不會被重新翻譯。"
        )
        self.repair_samples.controls = []
        self._update_repair_start_button()

    def _update_repair_start_button(self, *, running: bool | None = None) -> None:
        is_running = self._running if running is None else running
        has_empty_preview = (
            self._repair_preview is not None
            and self._repair_preview.selected_count == 0
        )
        self.repair_start_btn.disabled = is_running or has_empty_preview

    # ------------------------------------------------------------------ 事件
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
        self._reset_stats()
        self._safe_update()
        launched = launch_page_operation(
            self._page,
            lambda: run_moddb_translate_service(
                self.build_options(dry_run=dry_run), self.session
            ),
            name="Mod 資料庫機翻",
            owner="moddb-translate",
            task_session=self.session,
            fallback_launcher=lambda target: threading.Thread(
                target=target, daemon=True
            ).start(),
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
        if self._running:
            show_snack(self._page, "機翻正在執行中", C.GOLD)
            return
        self._clear_repair_preview()
        if not self.version_dd.value:
            self.repair_preview_text.value = "請先選擇遊戲版本。"
            self._safe_update()
            return
        db = self._get_db()
        if db is None:
            self.repair_preview_text.value = "無法開啟 Mod 資料庫。"
            self._safe_update()
            return

        options = self.build_options()
        try:
            preview = preview_same_source_ai_retranslation(db, options)
        except Exception as exc:  # noqa: BLE001 - preview errors stay in the UI
            self.repair_preview_text.value = f"預覽失敗：{exc}"
            log_warning(f"Mod 資料庫舊 AI 重翻預覽失敗：{exc!r}")
            self._safe_update()
            return

        self._repair_preview = preview
        self._repair_preview_db_identity = self._database_identity(db)
        selected = preview.selected_count
        cap = f"上限 {format_count(options.limit)} 筆" if options.limit else "不限筆數"
        breakdown = (
            "、".join(
                f"{cache_profile_label(cache_type)}：{format_count(count)} 筆"
                for cache_type, count in preview.profile_counts
            )
            or "無符合類型"
        )
        self.repair_preview_text.value = (
            f"符合條件：{format_count(preview.total_candidates)} 筆；{cap}，"
            f"本次將重翻 {format_count(selected)} 筆。\n"
            f"來源：{source_label(preview.source)}。人工、模組自帶及其他來源不會被重新翻譯。\n"
            f"翻譯 profile：{breakdown}\n"
            f"預估：約 {format_count(preview.estimated_batches)} 批。"
        )
        self.repair_samples.controls = [
            ft.Text(
                f"[{cache_profile_label(preview.entry_cache_types[index])}] "
                f"[{source_label(preview.source)}] {row.mod_id} / {row.key}\n"
                f"原文／目前 AI 譯文：{row.en_us}",
                size=12,
                color=C.MUTED,
                selectable=True,
            )
            for index, row in enumerate(preview.entries[:5])
        ]
        self._update_repair_start_button()
        self._safe_update()

    def confirm_retranslation(self, _e=None) -> None:
        preview = self._repair_preview
        if self._running:
            return
        if preview is None:
            show_snack(
                self._page,
                "請先按「預覽符合條件的舊 AI 譯文」，檢查候選範圍與筆數後再重新翻譯。",
                C.GOLD,
            )
            return
        if preview.selected_count == 0:
            show_snack(self._page, "目前沒有符合條件的舊 AI 譯文可重新翻譯。", C.GOLD)
            return
        show_dialog = getattr(self._page, "show_dialog", None)
        if not callable(show_dialog):
            show_snack(self._page, "目前畫面無法顯示確認視窗，未開始重翻", C.GOLD)
            return
        show_dialog(
            ft.AlertDialog(
                title=ft.Text("確認重新翻譯舊 AI 譯文"),
                content=ft.Text(
                    f"即將重翻 {preview.selected_count:,} 筆目前生效來源為「AI 機翻」且"
                    "譯文與原文相同的項目。只更新 AI 來源；人工及其他來源不會更動，"
                    "也不會同步到其他版本。",
                    selectable=True,
                    width=440,
                ),
                actions=[
                    ft.TextButton(
                        "取消", on_click=lambda _e=None: self._page.pop_dialog()
                    ),
                    ft.TextButton(
                        "開始重新翻譯",
                        on_click=lambda _e=None: self._start_retranslation(preview),
                    ),
                ],
            )
        )

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
        options = self.build_options()
        self.session = tag_session(TaskSession(), "Mod 資料庫舊 AI 重翻", "moddb")
        self._run_version = str(options.version)
        self._set_status("舊 AI 重翻中", "dia")
        self._set_running(True)
        self.progress_bar.value = 0
        self.live_text.value = ""
        self.log_view.clear()
        self.repair_summary_text.value = "重翻進行中；未成功完成的項目會保留舊譯文。"
        self._safe_update()
        threading.Thread(
            target=run_moddb_retranslate_service,
            args=(options, self.session, preview.entries),
            daemon=True,
        ).start()
        self._running = True
        if not self._poller.running:
            self._poller.start(self._page, self._poll)

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

    # ------------------------------------------------------------------ 輪詢
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
        live = (snap.get("summary") or {}).get("live")
        if live and status not in ("DONE", "ERROR"):
            self.live_text.value = format_live(
                tick_live(live)
            )  # 已用時間每次輪詢都更新
        if status in ("DONE", "ERROR"):
            summary = snap.get("summary") or {}
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
                self._set_status("機翻完成", "em")
            self._apply_summary(summary)
            self._running = False
            self._set_running(False)
            if self._on_finished and not summary.get("dry_run"):
                self._on_finished()
            self._refresh_counts()
        self._safe_update()

    def _apply_summary(self, s: dict) -> None:
        if not s:
            return
        if s.get("operation") == "retranslate_same_source_ai":
            self.repair_summary_text.value = (
                f"候選 {s.get('candidates', 0)}；更新 {s.get('updated', 0)}；"
                f"仍相同 {s.get('unchanged', 0)}；格式檢查未通過 {s.get('flagged', 0)}；"
                f"資料已變動跳過 {s.get('skipped_changed', 0)}；"
                f"失敗 {s.get('failed', 0)}；範圍內仍符合條件 {s.get('remaining', 0)}"
                + (
                    f"；快取未同步 {s.get('cache_failed', 0)} 筆"
                    if s.get("cache_failed")
                    else ""
                )
            )
            return
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
        self._flagged = dict(s.get("flagged_entries") or {})
        self.view_flagged_btn.visible = bool(self._flagged)
        self.stat_remaining.set_value(format_count(s.get("remaining")))

    def _reset_stats(self) -> None:
        for card in self._stat_cards:
            card.set_value("—", delta="")
        self._flagged = {}
        self.view_flagged_btn.visible = False

    def _view_flagged(self) -> None:
        """跳到條目校對，只看這次特殊字元不一致、沒寫入的條目。"""
        if self._flagged and self._on_view_flagged and self._run_version:
            self._on_view_flagged(
                list(self._flagged), dict(self._flagged), self._run_version
            )

    def _set_running(self, running: bool) -> None:
        self.start_btn.disabled = running
        self.preview_btn.disabled = running
        self.cancel_btn.disabled = not running
        self.repair_preview_btn.disabled = running
        self._update_repair_start_button(running=running)

    def _set_status(self, text: str, tone: str = "neutral") -> None:
        set_chip_status(self.status_chip, text, tone)

    def _safe_update(self) -> None:
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響機翻本身
            log_debug(f"TranslatePanel update 略過：{exc}")
