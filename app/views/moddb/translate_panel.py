"""app/views/moddb/translate_panel.py：批次機翻（選版本／模組 → 預覽 → 機翻 → 結果）。

把資料庫裡「沒有任何譯文」的條目交給機翻引擎，結果以「AI 機翻」來源寫回；
只填空白、不覆蓋任何既有譯文，之後匯入或人工校對的譯文會自動優先。
流程與掃描匯入頁相同：背景執行緒＋畫面輪詢。
"""

from __future__ import annotations

import asyncio
import threading

import flet as ft

from app.services_impl.moddb_translate_service import (
    DEFAULT_LIMIT,
    TranslateOptions,
    run_moddb_translate_service,
)
from app.tasks.task_session import TaskSession, tag_session
from app.ui import kit
from app.ui.design import C
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style, set_chip_status
from app.views._log import LogView, load_ui_logging_config
from app.views.moddb.formatting import format_count
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_debug, log_info, log_warning

_POLL_INTERVAL_SEC = 0.2
ALL_MODS = "__all__"


class TranslatePanel(ft.Column):
    """批次機翻頁籤。"""

    def __init__(self, page: ft.Page, get_db, on_finished=None):
        super().__init__(expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self._get_db = get_db
        self._on_finished = on_finished
        self.session: TaskSession | None = None
        self._running = False
        self._poller = PollerHandle()

        self._build_scope_card()
        self._build_run_card()
        self.controls = [self.scope_card, self.run_card]

    # ------------------------------------------------------------------ 建構
    def _build_scope_card(self) -> None:
        self.version_dd = kit.dropdown(
            label="遊戲版本", dense=True, width=220, on_select=self._on_scope_changed
        )
        self.mod_dd = kit.dropdown(
            label="模組", dense=True, width=260, on_select=self._on_scope_changed
        )
        self.limit_field = kit.text_field(
            "單次上限（筆）",
            hint="0 = 不限",
            value=str(DEFAULT_LIMIT),
            width=160,
            on_change=lambda _e: self._refresh_counts(),
        )
        self.count_text = ft.Text("", size=13, color=C.TEXT)
        self.reuse_row = kit.SwitchRow(
            "先沿用其他版本的相同譯文",
            "其他版本已有「模組、鍵值、原文都相同」的譯文時直接補上，不呼叫 AI、不耗額度",
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
            "沿用其他版本", "—", icon=ft.Icons.CONTENT_COPY, tone="dia", expand=1
        )
        self.stat_written = kit.stat_card(
            "已寫入（AI 機翻）",
            "—",
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
            tone="em",
            expand=1,
        )
        self.stat_flagged = kit.stat_card(
            "特殊字元不一致", "—", icon=ft.Icons.WARNING_AMBER, tone="gold", expand=1
        )
        self.stat_remaining = kit.stat_card(
            "此範圍仍未翻譯",
            "—",
            icon=ft.Icons.PENDING_OUTLINED,
            tone="neutral",
            expand=1,
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
                    self.log_view,
                    ft.Row(list(self._stat_cards), spacing=10),
                ],
                spacing=12,
            ),
            icon=ft.Icons.AUTO_AWESOME,
            tone="em",
        )

    # ------------------------------------------------------------------ 範圍
    def refresh_scope(self) -> None:
        """切到本頁籤時重讀資料庫的版本與模組清單，保留目前選擇。"""
        db = self._get_db()
        versions = db.versions() if db else []
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        if self.version_dd.value not in versions:
            self.version_dd.value = versions[0] if versions else None
        self._refresh_mods()
        self._refresh_counts()

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
        limit = self.limit()
        will = missing if not limit else min(missing, limit)
        self.count_text.value = f"此範圍有 {format_count(missing)} 筆未翻譯，本次最多翻譯 {format_count(will)} 筆"

    # ------------------------------------------------------------------ 事件
    def build_options(self, *, dry_run: bool = False) -> TranslateOptions:
        return TranslateOptions(
            version=self.version_dd.value or "",
            mod_ids=self.mod_ids(),
            limit=self.limit(),
            dry_run=dry_run,
            reuse_other_versions=bool(self.reuse_row.value),
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
        self.session = tag_session(TaskSession(), "Mod 資料庫機翻", "moddb")
        self._set_status("預覽中" if dry_run else "機翻中", "dia")
        self._set_running(True)
        self.progress_bar.value = 0
        self.log_view.clear()
        self._reset_stats()
        self._safe_update()
        threading.Thread(
            target=run_moddb_translate_service,
            args=(self.build_options(dry_run=dry_run), self.session),
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
            "Mod 資料庫機翻：使用者要求取消，等待目前批次結束（已完成的批次已寫入）"
        )
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
                log_debug(f"機翻輪詢停止：{exc}")
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
                self._set_status("機翻發生錯誤", "red")
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
        self.stat_remaining.set_value(format_count(s.get("remaining")))

    def _reset_stats(self) -> None:
        for card in self._stat_cards:
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
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響機翻本身
            log_debug(f"TranslatePanel update 略過：{exc}")
