"""智慧校對視窗：執行、逐筆／批量審查與歷史批次的外殼。"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_smart_review_service import SMART_REVIEW_DEFAULT_SOURCE
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
            on_resume=self.run_panel.resume_run,
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
        """切換分頁；進入歷史分頁時重新整理清單。"""
        self._show_tab(key)
        if key == "history":
            self.history.refresh()
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
        if (
            key == "batch"
            and self._review_run_id
            and self.batch_reviewer.run_id != self._review_run_id
        ):
            self.batch_reviewer.load_run(self._review_run_id)
        self.ctx.update()

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
        self.reviewer.load_run(run_id)
        # 批量候選是另一個分頁，等使用者真的切換過去才讀取，避免隱藏
        # 面板在逐筆審查時也啟動候選分頁查詢。
        if self.review_mode.value == "batch":
            self.batch_reviewer.load_run(run_id)
        self.ctx.update()

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
        self.ctx.close()
        self.run_panel.detach()
