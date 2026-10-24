"""智慧校對視窗：執行、逐筆／批量審查與歷史批次的外殼。"""

from __future__ import annotations

from typing import Any

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    SMART_REVIEW_DEFAULT_SOURCE,
    preview_moddb_smart_review_resume_from_path,
)
from app.ui import kit
from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    close_page_dialog,
    dialog_dimensions,
    present_page_dialog,
)
from app.ui.snack import show_snack
from app.views.moddb.smart_review_batch_reviewer import SmartReviewBatchReviewer
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_history import SmartReviewHistory
from app.views.moddb.smart_review_job import job_for_page
from app.views.moddb.smart_review_reviewer import SmartReviewReviewer
from app.views.moddb.smart_review_run_panel import SmartReviewRunPanel
from translation_tool.utils.config_manager import get_smart_review_settings

_TABS = (
    ("run", "執行", ft.Icons.AUTO_AWESOME),
    ("review", "審查", ft.Icons.RATE_REVIEW_OUTLINED),
    ("history", "歷史批次", ft.Icons.HISTORY),
)


class SmartReviewDialog:
    """執行本地 AI 校對；結果只進入人工審核佇列，不會自動覆蓋譯文。

    校對在背景執行，關閉視窗不會中斷；重新開啟時會接續顯示進度與 log。
    """

    def __init__(
        self,
        page: ft.Page,
        get_db,
        version: str | None,
        on_complete,
        mod_id: str | None = None,
        kind: str | None = None,
        effective_source: int | None = SMART_REVIEW_DEFAULT_SOURCE,
        query: str = "",
    ):
        """建立三個分頁面板與對話框，並以條目頁的範圍作為預設條件。"""
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self.ctx = SmartReviewContext(page, get_db, on_complete)
        self.job = job_for_page(page)
        self.run_panel = SmartReviewRunPanel(
            self.ctx,
            self.job,
            version=version,
            mod_id=mod_id,
            kind=kind,
            effective_source=effective_source,
            query=query,
            on_open_review=self.open_review,
            on_hide=self._close,
        )
        self.reviewer = SmartReviewReviewer(
            self.ctx,
            on_changed=self.ctx.notify_parent,
            on_review_changed=lambda: self._refresh_opposite_review("single"),
        )
        self.batch_reviewer = SmartReviewBatchReviewer(
            self.ctx, on_changed=lambda: self._on_review_changed("batch")
        )
        self._review_run_id: str | None = None
        self._resume_seq = 0
        self._resume_dialog: ft.AlertDialog | None = None
        self._resume_model_field: ft.TextField | None = None
        self._resume_batch_field: ft.TextField | None = None
        self._resume_error_text: ft.Text | None = None
        self.review_mode = kit.Segmented(
            [
                ("single", "逐筆審查", ft.Icons.RATE_REVIEW_OUTLINED),
                ("batch", "批量處理", ft.Icons.LAYERS_OUTLINED),
            ],
            "single",
            self._on_review_mode,
        )
        self.review_panel = ft.Column(
            [
                self.review_mode,
                self.reviewer.control,
                self.batch_reviewer.control,
            ],
            spacing=10,
            expand=True,
        )
        self.batch_reviewer.control.visible = False
        self.history = SmartReviewHistory(
            self.ctx,
            on_open=self.open_review,
            on_resume=self._resume_run,
            active_run_id=self._active_run_id,
        )
        self.nav = kit.Segmented(list(_TABS), "run", self._on_tab)
        self._panels = {
            "run": self.run_panel.control,
            "review": self.review_panel,
            "history": self.history.control,
        }
        self._build_dialog()
        self._show_tab("run")

    def _active_run_id(self) -> str | None:
        """回傳正在執行的批次 id，供歷史清單判斷是否為執行中。"""
        return self.job.state.run_id if self.job.running else None

    def _build_dialog(self) -> None:
        """建立對話框外殼：分頁切換列與三個分頁內容。"""
        width, height = dialog_dimensions(
            self.page,
            width_ratio=0.88,
            max_width=1280,
            vertical_margin=150,
            max_height=900,
        )
        self.close_btn = ft.TextButton("關閉", on_click=self._close)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [
                    ft.Text("AI 智慧校對", weight=ft.FontWeight.BOLD),
                    kit.chip("候選模式・不會自動覆蓋譯文", "dia"),
                ],
                spacing=10,
                wrap=True,
            ),
            content=ft.Container(
                content=ft.Column(
                    [self.nav, *self._panels.values()],
                    spacing=12,
                    expand=True,
                ),
                width=width,
                height=height,
                data={
                    "marker": RESPONSIVE_DIALOG_MARKER,
                    "width_ratio": 0.88,
                    "max_width": 1280,
                    "vertical_margin": 150,
                    "max_height": 900,
                },
            ),
            actions=[self.close_btn],
            on_dismiss=self._on_dismiss,
        )

    def _on_tab(self, key: str) -> None:
        """立即切換分頁，並把該分頁需要的資料讀取交給背景工作。"""
        self._show_tab(key)
        if key == "history":
            self.history.refresh()
        elif key == "review":
            self._load_review_tab_if_needed()
        self.ctx.update()

    def _on_review_changed(self, source: str) -> None:
        """通知外層並刷新另一個審查模式的目前批次。"""
        self.ctx.notify_parent()
        self._refresh_opposite_review(source)

    def _refresh_opposite_review(self, source: str) -> None:
        """刷新未執行目前動作的審查模式，避免兩個面板保留舊狀態。"""
        if source != "single":
            self.reviewer.refresh()
        if source != "batch":
            self.batch_reviewer.refresh()

    def _on_review_mode(self, key: str) -> None:
        """在逐筆與批量審查間切換，保留兩邊的篩選與頁面狀態。"""
        self.reviewer.control.visible = key == "single"
        self.batch_reviewer.control.visible = key == "batch"
        self._load_review_tab_if_needed(mode=key)
        self.ctx.update()

    def _load_review_tab_if_needed(
        self, *, mode: str | None = None, force: bool = False
    ) -> None:
        """只在目前審查面板尚未載入批次時啟動背景讀取。"""
        if not self._review_run_id:
            return
        active_mode = mode or str(self.review_mode.value)
        if active_mode == "batch":
            if force or self.batch_reviewer.run_id != self._review_run_id:
                self.batch_reviewer.load_run(self._review_run_id)
            return
        if force or self.reviewer.state.run_id != self._review_run_id:
            self.reviewer.load_run(self._review_run_id)

    def _show_tab(self, key: str) -> None:
        """只顯示指定分頁，其餘隱藏但保留狀態。"""
        for name, control in self._panels.items():
            control.visible = name == key
        if self.nav.value != key:
            self.nav.select(key)

    def open_review(self, run_id: str) -> None:
        """切到審查分頁並載入指定批次。"""
        self._review_run_id = run_id
        self._show_tab("review")
        # 逐筆與批量面板的 load_run 都會把資料庫讀取排入背景，避免開啟
        # 歷史批次後切換到審查時阻塞對話框；批量面板仍延後到真的選取批量模式。
        self._load_review_tab_if_needed(force=True)
        self.ctx.update()

    def _resume_run(self, run_id: str) -> None:
        """讀取指定 run 的狀態後，以可編輯確認視窗啟動續跑。"""
        if self.job.running:
            show_snack(self.page, "智慧校對正在執行中。", C.GOLD)
            return
        database = self.ctx.database()
        if database is None:
            show_snack(self.page, "目前沒有可用的 Mod 資料庫。", C.RED)
            return
        self._resume_seq += 1
        seq = self._resume_seq
        path, priority = database
        self._show_resume_loading(run_id)

        def work() -> dict[str, Any]:
            return preview_moddb_smart_review_resume_from_path(
                path, priority, str(run_id)
            )

        def done(result: dict[str, Any] | None, error: str | None) -> None:
            if seq != self._resume_seq:
                return
            self._close_resume_dialog()
            if error:
                show_snack(self.page, f"無法讀取續跑狀態：{error}", C.RED)
                return
            if not isinstance(result, dict):
                show_snack(self.page, "無法讀取續跑狀態。", C.RED)
                return
            self._show_resume_confirmation(str(run_id), result)

        self.ctx.run_background(
            work,
            done,
            name="讀取智慧校對續跑狀態",
            still_valid=lambda: seq == self._resume_seq and not self.job.running,
        )

    def _show_resume_loading(self, run_id: str) -> None:
        """在讀取續跑狀態時顯示可見等待提示，避免使用者誤以為沒有反應。"""
        self._close_resume_dialog()
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("讀取續跑狀態", weight=ft.FontWeight.BOLD),
            content=ft.Column(
                [
                    ft.ProgressRing(),
                    ft.Text(
                        f"正在確認批次 {run_id} 的模型、批次大小與未完成數量…",
                        selectable=True,
                    ),
                ],
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _e: self._cancel_resume_preview())
            ],
        )
        self._resume_dialog = dialog
        present_page_dialog(self.page, dialog)

    def _cancel_resume_preview(self) -> None:
        """取消尚未完成的續跑狀態讀取。"""
        self._resume_seq += 1
        self._close_resume_dialog()

    def _resume_status_summary(self, counts: object) -> str:
        """把歷史狀態統計轉成確認視窗可讀文字。"""
        if not isinstance(counts, dict):
            return "狀態明細暫不可用"
        labels = {
            "applied": "已套用",
            "rejected": "已拒絕",
            "ready": "可套用",
            "no_change": "AI 不用修改",
            "needs_context": "需補 context",
            "invalid": "無效",
            "request_failed": "請求錯誤",
            "cancelled": "未完成／已取消",
            "pending": "待處理",
        }
        values = [
            f"{labels.get(str(status), str(status))} {int(count):,}"
            for status, count in counts.items()
            if int(count or 0) > 0
        ]
        return "；".join(values) if values else "沒有狀態明細"

    def _build_resume_fields(self, preview: dict[str, Any]) -> None:
        """建立續跑確認視窗的模型、批次與錯誤欄位。"""
        model = str(preview.get("model") or self.run_panel.model_dd.value or "")
        batch_size = int(
            preview.get("batch_size") or self.run_panel.batch_field.value or 0
        )
        self._resume_model_field = kit.text_field(
            "校對模型", value=model, hint="輸入要使用的模型名稱", width=300
        )
        self._resume_batch_field = kit.text_field(
            "每批（筆）", value=str(batch_size), hint="正整數", width=140
        )
        self._resume_error_text = ft.Text("", size=12, color=C.RED, selectable=True)

    def _show_resume_confirmation(self, run_id: str, preview: dict[str, Any]) -> None:
        """顯示指定 run 的摘要，並讓模型與批次大小由使用者輸入。"""
        scope = preview.get("scope")
        scope = scope if isinstance(scope, dict) else {}
        version = str(preview.get("version") or scope.get("version") or "-")
        scope_text = (
            f"{version} · {scope.get('mod_id') or '全部模組'} / "
            f"{scope.get('kind') or '全部類型'}"
        )
        selected = int(preview.get("selected_count") or 0)
        processed = int(preview.get("processed_count") or 0)
        pending = int(preview.get("pending_count") or 0)
        requested_limit = int(preview.get("requested_limit") or 0)
        limit_label = "不限" if requested_limit == 0 else f"{requested_limit:,}"
        self._build_resume_fields(preview)
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("確認繼續未完成校對", weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            f"批次：{run_id}", size=12, color=C.DIM, selectable=True
                        ),
                        ft.Text(f"範圍：{scope_text}", size=13, selectable=True),
                        ft.Text(
                            f"原批次 {selected:,} 筆；已處理 {processed:,} 筆；"
                            f"預計重新檢查 {pending:,} 筆；原上限 {limit_label}。",
                            size=13,
                            selectable=True,
                        ),
                        ft.Text(
                            "狀態："
                            + self._resume_status_summary(preview.get("status_counts")),
                            size=12,
                            color=C.MUTED,
                            selectable=True,
                        ),
                        kit.hint_text(
                            "確認後會再用目前資料庫的 CAS 快照檢查；已套用或已拒絕的候選不會悄悄重送。"
                        ),
                        ft.Row(
                            [self._resume_model_field, self._resume_batch_field],
                            spacing=10,
                            wrap=True,
                        ),
                        self._resume_error_text,
                    ],
                    tight=True,
                    spacing=10,
                ),
                width=620,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _e: self._close_resume_dialog()),
                kit.button(
                    "確認並繼續",
                    "primary",
                    icon=ft.Icons.PLAY_ARROW,
                    on_click=lambda _e, rid=run_id, data=preview: self._confirm_resume(
                        rid, data
                    ),
                ),
            ],
        )
        self._resume_dialog = dialog
        present_page_dialog(self.page, dialog)

    def _confirm_resume(self, run_id: str, preview: dict[str, Any]) -> None:
        """驗證確認視窗輸入，成功後切到執行頁並啟動指定 run。"""
        model = str(self._resume_model_field.value or "").strip()
        raw_batch = str(self._resume_batch_field.value or "").strip()
        settings = get_smart_review_settings()
        max_batch = int(settings["max_batch_size"])
        error = None
        if not model:
            error = "校對模型不可為空白。"
        else:
            try:
                batch_size = int(raw_batch)
            except (TypeError, ValueError):
                batch_size = 0
            if batch_size < 1 or batch_size > max_batch:
                error = f"每批必須介於 1 與 {max_batch} 筆。"
        if error:
            self._resume_error_text.value = error
            self._resume_error_text.color = C.RED
            self.page.update()
            return
        self._close_resume_dialog()
        self._show_tab("run")
        self.ctx.update()
        started = self.run_panel.resume_run(
            run_id,
            model=model,
            batch_size=batch_size,
            resume_preview=preview,
        )
        if started:
            show_snack(
                self.page,
                "已切換到執行頁面，正在繼續未完成校對。",
                C.EM,
            )

    def _close_resume_dialog(self) -> None:
        """關閉目前的續跑等待／確認視窗。"""
        dialog = self._resume_dialog
        self._resume_dialog = None
        if dialog is not None:
            close_page_dialog(self.page, dialog)

    def open(self) -> None:
        """開啟視窗，接續背景任務狀態並載入批次清單。"""
        try:
            present_page_dialog(self.page, self.dialog)
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟智慧校對視窗。", C.GOLD)
            return
        self.run_panel.attach()
        self.history.refresh()

    def _close(self, _e=None) -> None:
        """關閉視窗；背景校對不受影響，結果回來後會以通知告知。"""
        self._teardown()
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        """對話框被使用者從外部關閉時做同樣的清理。"""
        self._teardown()

    def _teardown(self) -> None:
        """停止輪詢並讓進行中的查詢結果失效，但不取消背景校對。"""
        if self.ctx.closed:
            return
        self._resume_seq += 1
        self._close_resume_dialog()
        self.ctx.close()
        self.run_panel.detach()
