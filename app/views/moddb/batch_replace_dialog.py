"""Paged, explicit preview and confirmation UI for Mod DB batch replacement."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from time import monotonic

import flet as ft

from app.services_impl.moddb_batch_operation import (
    BatchOperationOutcome,
    BatchOperationResult,
    launch_batch_replace_job,
)
from app.services_impl.moddb_service import (
    BatchReplacePlan,
    BatchReplaceResult,
    EntryFilter,
)
from app.services_impl.moddb_source_service import source_catalog_for
from app.tasks.task_session import TaskSession
from app.ui import kit
from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    close_page_dialog,
    dialog_dimensions,
    present_page_dialog,
)
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.views.moddb.batch_replace_preview import render_batch_preview_rows
from translation_tool.utils.redaction import redact_secrets

PREVIEW_PAGE_SIZE = 50
PROGRESS_UPDATE_INTERVAL = 0.35


class BatchReplaceDialog:
    def __init__(
        self,
        page: ft.Page,
        get_db,
        criteria: EntryFilter,
        on_complete,
        *,
        operation_launcher: Callable[..., object] | None = None,
    ):
        self.page = page
        self.get_db = get_db
        self.base_criteria = criteria
        self.on_complete = on_complete
        self._operation_launcher = operation_launcher
        self.plan: BatchReplacePlan | None = None
        self.selection_plan: BatchReplacePlan | None = None
        self.excluded_roots: set[int] = set()
        self.only_checked = True
        self.preview_page = 0
        self.show_skipped = False
        self._skipped_pages: dict[int, tuple] = {}
        self._requested_skipped_page = 0
        self._generation = 0
        self._job_generation: int | None = None
        self._job_kind: str | None = None
        self._job_session: TaskSession | None = None
        self._job_result: BatchOperationResult | None = None
        self._job_handled = False
        self._busy = False
        self._dialog_open = False
        self._final_open = False
        self._last_progress_paint = 0.0
        self._last_progress_stage = ""
        self._last_progress_value: float | None = None
        self._quality_ack_plan: BatchReplacePlan | None = None
        self._quality_ack_generation: int | None = None
        self._final_plan: BatchReplacePlan | None = None
        self._final_generation: int | None = None
        self._plan_catalog = source_catalog_for(None)
        self._poller = PollerHandle()
        self._build_fields()
        self._build_buttons()
        self._build_dialog()

    def _build_fields(self) -> None:
        self.find_field = kit.text_field(
            "尋找", expand=True, on_change=self._invalidate
        )
        self.replace_field = kit.text_field(
            "取代為", expand=True, on_change=self._invalidate
        )
        self.propagate = ft.Switch(
            label="同步其他版本（需譯文、來源與狀態完全相同）",
            value=False,
            on_change=self._invalidate,
        )
        self.only_checked_control = ft.Checkbox(
            label="僅處理已勾選的根條目",
            value=True,
            on_change=self._on_scope_change,
        )
        self.quality_ack = ft.Checkbox(
            label="我已檢查品質警告，仍要允許問題增加",
            value=False,
            visible=False,
            on_change=self._on_quality_ack,
        )
        self.summary = ft.Text(
            "尚未預覽",
            size=14,
            weight=ft.FontWeight.BOLD,
        )
        self.progress_text = ft.Text("", size=11.5, color=C.DIM, visible=False)
        self.progress_bar = ft.ProgressBar(value=0, visible=False)
        self.rows = ft.Column(
            [kit.hint_text("輸入尋找文字後，按「產生預覽」。")],
            spacing=4,
            scroll=ft.ScrollMode.AUTO,
            height=340,
        )
        self.page_text = ft.Text("尚未預覽", size=11.5, color=C.DIM)

    def _build_buttons(self) -> None:
        self.preview_btn = kit.button("產生預覽", "secondary", on_click=self._preview)
        self.cancel_job_btn = kit.button(
            "取消預覽",
            "ghost",
            size="sm",
            on_click=self._cancel_preview,
            disabled=True,
        )
        self.cancel_job_btn.visible = False
        self.previous_btn = kit.button(
            "上一頁",
            "ghost",
            size="sm",
            on_click=lambda _e: self._turn(-1),
            disabled=True,
        )
        self.next_btn = kit.button(
            "下一頁",
            "ghost",
            size="sm",
            on_click=lambda _e: self._turn(1),
            disabled=True,
        )
        self.page_nav = ft.Row(
            [self.previous_btn, self.page_text, self.next_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            visible=False,
        )
        self.skipped_btn = kit.button(
            "查看略過原因",
            "ghost",
            size="sm",
            on_click=self._toggle_skipped,
            disabled=True,
        )
        self.apply_btn = kit.button(
            "檢查並確認寫入範圍",
            "primary",
            on_click=self._open_final_confirmation,
            disabled=True,
        )
        self.confirm_final_btn = kit.button(
            "確認並開始不可取消的寫入",
            "primary",
            on_click=self._confirm_apply,
            disabled=True,
        )
        self.final_summary = ft.Text("", size=13, selectable=True)
        self.final_ack = ft.Checkbox(
            label="我已核對上方範圍與筆數，確認執行",
            value=False,
            on_change=self._on_final_ack,
        )

    def _build_dialog(self) -> None:
        dialog_width, dialog_height = dialog_dimensions(self.page)
        self.dialog = self._build_preview_dialog(dialog_width, dialog_height)
        self.final_dialog = self._build_confirmation_dialog(dialog_width, dialog_height)

    def _build_preview_dialog(self, dialog_width: int, dialog_height: int):
        return ft.AlertDialog(
            modal=True,
            title=ft.Text("批次尋找與取代"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "依目前篩選搜尋生效譯文；預覽不會寫入，確認後才會取代。",
                            size=12,
                            color=C.MUTED,
                            selectable=True,
                        ),
                        ft.Row([self.find_field, self.replace_field], spacing=8),
                        self.propagate,
                        ft.Row(
                            [
                                self.only_checked_control,
                                self.preview_btn,
                                self.cancel_job_btn,
                                self.skipped_btn,
                                self.quality_ack,
                            ],
                            spacing=8,
                            wrap=True,
                        ),
                        self.progress_text,
                        self.progress_bar,
                        self.summary,
                        self.rows,
                        self.page_nav,
                    ],
                    tight=True,
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=dialog_width,
                height=dialog_height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[
                ft.TextButton("取消", on_click=self._close_dialog),
                self.apply_btn,
            ],
            on_dismiss=self._on_dismiss,
        )

    def _build_confirmation_dialog(self, dialog_width: int, dialog_height: int):
        return ft.AlertDialog(
            modal=True,
            title=ft.Text("最後確認：批次替換"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "這是實際寫入範圍。資料庫會在交易內重新比對預覽快照；"
                            "寫入開始後不可取消。",
                            size=12.5,
                            color=C.MUTED,
                            selectable=True,
                        ),
                        self.final_summary,
                        self.final_ack,
                    ],
                    tight=True,
                    spacing=12,
                ),
                width=min(dialog_width, 520),
                height=min(dialog_height, 320),
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[
                ft.TextButton("返回預覽", on_click=self._close_final),
                self.confirm_final_btn,
            ],
            on_dismiss=self._on_final_dismiss,
        )

    def open(self) -> None:
        try:
            self._dialog_open = True
            present_page_dialog(self.page, self.dialog)
            if self._busy and self._job_session is not None:
                self._poller.start(self.page, self._poll)
                self._sync_worker()
        except (AttributeError, RuntimeError):
            self._dialog_open = False
            show_snack(self.page, "目前畫面無法開啟批次替換確認視窗。", C.GOLD)

    def _close_dialog(self, _e=None) -> None:
        if self._busy and self._job_kind == "preview":
            self._cancel_preview()
        if self._busy and self._job_kind == "apply":
            show_snack(self.page, "資料庫寫入中，請等待交易完成。", C.GOLD)
            return
        self._dialog_open = False
        self._generation += 1
        self._clear_plan_and_ack()
        self._poller.stop()
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        was_open = self._dialog_open
        self._dialog_open = False
        if was_open and self._busy and self._job_kind in {"preview", "skipped"}:
            self._cancel_preview()
        if was_open and self._job_kind != "apply":
            self._generation += 1
            self._clear_plan_and_ack()
        self._poller.stop()

    def _on_final_dismiss(self, _e=None) -> None:
        self._final_open = False

    def _invalidate(self, _e=None) -> None:
        if self._busy:
            return
        self._generation += 1
        self._clear_plan_and_ack()
        self.selection_plan = None
        self.excluded_roots.clear()
        self.preview_page = 0
        self.show_skipped = False
        self.apply_btn.disabled = True
        self.rows.controls = [kit.hint_text("條件已變更，請重新預覽。")]
        self.summary.value = "條件已變更，請重新預覽。"
        self.page_text.value = "尚未預覽"
        self.page_nav.visible = False
        self.previous_btn.disabled = True
        self.next_btn.disabled = True
        self.skipped_btn.disabled = True
        self.skipped_btn.text = "查看略過原因"
        self._update_dialog()

    def _on_scope_change(self, _e=None) -> None:
        if self._busy or self.selection_plan is None:
            return
        self.only_checked = bool(self.only_checked_control.value)
        self._invalidate_selection("範圍已變更，請重新預覽。")

    def _on_quality_ack(self, _e=None) -> None:
        if self._busy or self.plan is None:
            return
        approved = bool(self.quality_ack.value) and bool(
            self.plan.quality_worsened_count
        )
        self.quality_ack.value = approved
        self.plan = replace(self.plan, confirmed_quality_worsening=approved)
        self._quality_ack_plan = self.plan if approved else None
        self._quality_ack_generation = self._generation if approved else None
        self._final_plan = None
        self._final_generation = None
        self._render()

    def _clear_plan_and_ack(self) -> None:
        self.plan = None
        self._skipped_pages.clear()
        self._quality_ack_plan = None
        self._quality_ack_generation = None
        self._final_plan = None
        self._final_generation = None
        self.quality_ack.value = False
        self.quality_ack.visible = False

    def _quality_ack_is_valid(self) -> bool:
        if self.plan is None:
            return False
        if not self.plan.quality_worsened_count:
            return True
        return (
            bool(self.quality_ack.value)
            and self.plan.confirmed_quality_worsening
            and self._quality_ack_plan is self.plan
            and self._quality_ack_generation == self._generation
        )

    def _invalidate_selection(self, message: str) -> None:
        self._generation += 1
        self._clear_plan_and_ack()
        self.apply_btn.disabled = True
        self.rows.controls = []
        self.summary.value = message
        self.preview_page = 0
        self._render()

    def _preview(self, _e=None) -> None:
        self._start_preview()

    def _start_preview(self) -> None:
        if self._busy:
            return
        db = self.get_db()
        if db is None:
            self.plan = None
            self.summary.value = "目前沒有可用的資料庫。"
            self._update_dialog()
            return
        root_ids = self.selection_plan.root_ids if self.selection_plan else None
        selection_criteria = (
            self.selection_plan.criteria if self.selection_plan else None
        )
        excluded_roots = self.excluded_roots
        only_checked = self.only_checked
        find_text = str(self.find_field.value or "")
        replace_text = str(self.replace_field.value or "")
        propagate = bool(self.propagate.value)
        self._clear_plan_and_ack()
        self.show_skipped = False
        self.preview_page = 0
        self._plan_catalog = source_catalog_for(db)
        self._busy = True
        self._generation += 1
        generation = self._generation
        self._set_job_ui("preview", "正在建立預覽…")

        def work(session: TaskSession):
            criteria = self._criteria_for_selection(
                self.base_criteria,
                root_ids,
                excluded_roots,
                only_checked,
                selection_criteria=selection_criteria,
            )
            return db.preview_batch_replace(
                criteria,
                find_text,
                replace_text,
                propagate=propagate,
                confirmed_quality_worsening=False,
                progress_callback=lambda stage, value: self._record_progress(
                    session, generation, stage, value
                ),
            )

        self._start_job("preview", generation, work)

    @staticmethod
    def _criteria_for_selection(
        base: EntryFilter,
        root_ids: tuple[int, ...] | None,
        excluded_roots: set[int],
        only_checked: bool,
        *,
        selection_criteria: EntryFilter | None = None,
    ) -> EntryFilter:
        if root_ids is None:
            return base
        if only_checked:
            if selection_criteria is not None:
                return replace(
                    selection_criteria,
                    exclude_ids=tuple(
                        dict.fromkeys(
                            (*selection_criteria.exclude_ids, *sorted(excluded_roots))
                        )
                    ),
                )
            return replace(
                base,
                include_ids=tuple(i for i in root_ids if i not in excluded_roots),
                exclude_ids=tuple(base.exclude_ids),
            )
        if not excluded_roots:
            return base
        return replace(
            base,
            include_ids=base.include_ids,
            exclude_ids=tuple(
                dict.fromkeys((*base.exclude_ids, *sorted(excluded_roots)))
            ),
        )

    def _record_progress(
        self, session: TaskSession, generation: int, stage: str, value: float
    ) -> None:
        session.set_progress(value)
        session.set_summary(
            {
                "state": "working",
                "kind": self._job_kind,
                "generation": generation,
                "stage": stage,
            }
        )

    def _start_job(self, kind: str, generation: int, work: Callable):
        result_channel = BatchOperationResult(kind, generation)
        self._job_generation = generation
        self._job_kind = kind
        session, launched = launch_batch_replace_job(
            self.page,
            kind=kind,
            generation=generation,
            work=work,
            operation_launcher=self._operation_launcher,
            result_channel=result_channel,
        )
        self._job_session = session
        self._job_result = result_channel
        self._job_handled = False
        self._last_progress_paint = 0.0
        self._last_progress_stage = ""
        self._last_progress_value = None
        if launched is False or launched is None:
            result_channel.discard()
            self._job_result = None
            self._busy = False
            self._set_job_ui(None, "應用程式正在關閉，未啟動批次作業。")
            return
        self._poller.start(self.page, self._poll)
        # Inline test launchers and very short operations can finish before the
        # first event-loop poll is scheduled.
        if session.is_finished:
            self._sync_worker()

    async def _poll(self, alive) -> None:
        while alive():
            self._sync_worker()
            if self._job_handled or self._job_session is None:
                return
            await asyncio.sleep(0.12)

    def _sync_worker(self) -> None:
        session = self._job_session
        if session is None or self._job_handled:
            return
        snapshot = session.snapshot()
        summary = snapshot.get("summary") or {}
        if summary.get("state") == "working":
            if summary.get("generation") == self._job_generation:
                stage = str(summary.get("stage") or "背景作業進行中")
                stage_key = stage.partition("（")[0]
                progress = snapshot.get("progress", 0.0)
                now = monotonic()
                if stage_key != self._last_progress_stage or (
                    now - self._last_progress_paint >= PROGRESS_UPDATE_INTERVAL
                    and (
                        stage != self.progress_text.value
                        or progress != self._last_progress_value
                    )
                ):
                    self.progress_text.value = stage
                    self.progress_bar.value = progress
                    self._last_progress_stage = stage_key
                    self._last_progress_paint = now
                    self._last_progress_value = progress
                    self._update_dialog()
            return
        if not session.is_finished:
            return
        self._job_handled = True
        self._busy = False
        self._poller.stop()
        kind = summary.get("kind", self._job_kind)
        generation = summary.get("generation")
        channel = self._job_result
        outcome = channel.take_for_summary(session, summary) if channel else None
        self._job_result = None
        if kind == "preview" and generation == self._generation:
            self._finish_preview(summary, outcome)
        elif kind == "preview":
            self._set_job_ui(None, "預覽已失效或對話框已關閉；請重新產生預覽。")
        elif kind == "skipped" and generation == self._generation:
            self._finish_skipped(summary, outcome)
        elif kind == "apply":
            self._finish_apply(summary, outcome)

    def _finish_preview(self, summary: dict, outcome: BatchOperationOutcome | None):
        state = summary.get("state")
        if (
            state == "complete"
            and outcome is not None
            and isinstance(outcome.result, BatchReplacePlan)
        ):
            self.plan = outcome.result
            self._skipped_pages.clear()
            if self.selection_plan is None:
                self.selection_plan = self.plan
                self.excluded_roots.clear()
            self.preview_page = 0
            self.quality_ack.visible = self.plan.quality_worsened_count > 0
            self.quality_ack.value = False
            self.plan = replace(self.plan, confirmed_quality_worsening=False)
            self._quality_ack_plan = None
            self._quality_ack_generation = None
            if self.plan.quality_mixed_count:
                self.quality_ack.label = (
                    "我已檢查混合品質變化（部分問題改善、部分增加），仍要允許增加"
                )
            else:
                self.quality_ack.label = "我已檢查品質警告，仍要允許問題增加"
            self.summary.value = ""
            self._render(update=False)
        elif state == "cancelled" or (outcome and outcome.state == "cancelled"):
            self.plan = None
            self.summary.value = "預覽已取消，沒有可執行的替換計畫。"
            self.apply_btn.disabled = True
        elif state == "error" or (outcome and outcome.state == "error"):
            error = outcome.error if outcome else None
            self.plan = None
            self.apply_btn.disabled = True
            self.summary.value = self._error_message(error)
            if isinstance(error, ValueError):
                show_snack(self.page, redact_secrets(error), C.GOLD)
        self._set_job_ui(None, self.summary.value)

    def _start_skipped_load(self, page_index: int) -> None:
        if self._busy or self.plan is None:
            return
        db = self.get_db()
        if db is None:
            show_snack(self.page, "目前沒有可用的資料庫。", C.RED)
            return
        plan = self.plan
        generation = self._generation
        self._requested_skipped_page = page_index
        self.preview_page = page_index
        self.show_skipped = True
        self._busy = True
        self._render(update=False)
        self._set_job_ui("skipped", "正在載入略過原因…")

        def work(session: TaskSession):
            return db.load_batch_replace_skipped_page(
                plan,
                offset=page_index * PREVIEW_PAGE_SIZE,
                limit=PREVIEW_PAGE_SIZE,
                progress_callback=lambda stage, value: self._record_progress(
                    session, generation, stage, value
                ),
            )

        self._start_job("skipped", generation, work)

    def _finish_skipped(
        self, summary: dict, outcome: BatchOperationOutcome | None
    ) -> None:
        state = summary.get("state")
        if (
            state == "complete"
            and outcome is not None
            and isinstance(outcome.result, tuple)
            and self.plan is not None
        ):
            self._skipped_pages[self._requested_skipped_page] = outcome.result
            self.show_skipped = True
            self._render(update=False)
            message = "略過原因已載入。"
        elif state == "cancelled" or (outcome and outcome.state == "cancelled"):
            self.show_skipped = False
            self._render(update=False)
            message = "略過原因載入已取消。"
        else:
            error = outcome.error if outcome else None
            self.show_skipped = False
            self._render(update=False)
            message = self._error_message(error)
            show_snack(self.page, message, C.RED)
        self._set_job_ui(None, message)

    def _finish_apply(self, summary: dict, outcome: BatchOperationOutcome | None):
        if (
            summary.get("state") == "complete"
            and outcome is not None
            and isinstance(outcome.result, BatchReplaceResult)
        ):
            result = outcome.result
            self._set_job_ui(None, "批次替換已提交。")
            if self._dialog_open:
                self._dialog_open = False
                close_page_dialog(self.page, self.dialog)
            show_snack(
                self.page,
                f"已批次替換 {result.updated:,} 筆；略過 {result.skipped:,} 筆。可在記錄中整批還原。",
                C.EM,
                text_color=C.ON_EM,
            )
            self.on_complete(result)
        else:
            error = outcome.error if outcome else None
            message = self._error_message(error)
            if isinstance(error, ValueError):
                self.plan = None
                self.summary.value = f"{message} 重新預覽後才能繼續。"
                self._render(update=False)
            self._set_job_ui(
                None, self.summary.value if isinstance(error, ValueError) else message
            )
            show_snack(self.page, message, C.RED)

    @staticmethod
    def _error_message(error) -> str:
        if (
            isinstance(error, sqlite3.OperationalError)
            and "locked" in str(error).lower()
        ):
            return "資料庫目前被其他作業鎖定，請稍後重新預覽並操作。"
        return f"批次替換失敗：{redact_secrets(error)}"

    def _set_job_ui(self, kind: str | None, message: str) -> None:
        self.preview_btn.disabled = kind is not None
        self.find_field.disabled = kind is not None
        self.replace_field.disabled = kind is not None
        self.propagate.disabled = kind is not None
        self.only_checked_control.disabled = kind is not None
        self.quality_ack.disabled = kind is not None
        self.cancel_job_btn.visible = kind in {"preview", "skipped"}
        self.cancel_job_btn.text = "取消載入" if kind == "skipped" else "取消預覽"
        self.cancel_job_btn.disabled = kind not in {"preview", "skipped"}
        self.progress_text.visible = kind is not None
        self.progress_bar.visible = kind is not None
        self.progress_text.value = message
        if kind is None:
            self.progress_bar.value = 0
        self.apply_btn.disabled = (
            kind is not None
            or self.plan is None
            or not self.plan.changes
            or not self._quality_ack_is_valid()
        )
        self._update_dialog()

    def _cancel_preview(self, _e=None) -> None:
        if not self._busy or self._job_kind not in {"preview", "skipped"}:
            return
        kind = self._job_kind
        if self._job_session is not None:
            self._job_session.request_cancel()
        if self._job_result is not None:
            self._job_result.discard()
        if kind == "preview":
            self._generation += 1
            self._clear_plan_and_ack()
        else:
            self.show_skipped = False
        self.cancel_job_btn.disabled = True
        self.progress_text.value = (
            "取消要求已送出，等待查詢停止…"
            if kind == "preview"
            else "取消要求已送出，等待載入停止…"
        )
        self._update_dialog()

    def _turn(self, delta: int) -> None:
        if self.plan is None or self._busy:
            return
        total = self.plan.skipped_count if self.show_skipped else len(self.plan.changes)
        pages = max(1, (total + PREVIEW_PAGE_SIZE - 1) // PREVIEW_PAGE_SIZE)
        page_index = min(max(0, self.preview_page + delta), pages - 1)
        self.preview_page = page_index
        if (
            self.show_skipped
            and not self.plan.skipped_details_loaded
            and page_index not in self._skipped_pages
        ):
            self._start_skipped_load(page_index)
            return
        self._render()

    def _toggle_skipped(self, _e=None) -> None:
        if self.plan is None or self._busy:
            return
        self.show_skipped = not self.show_skipped
        self.preview_page = 0
        if (
            self.show_skipped
            and not self.plan.skipped_details_loaded
            and 0 not in self._skipped_pages
        ):
            self._start_skipped_load(0)
            return
        self._render()

    def _toggle_root(self, entry_id: int, selected: bool) -> None:
        if self._busy or self.selection_plan is None:
            return
        if selected:
            self.excluded_roots.discard(entry_id)
        else:
            self.excluded_roots.add(entry_id)
        self._invalidate_selection("勾選範圍已變更，請重新預覽。")

    def _display_rows(self):
        if self.show_skipped:
            if self.plan is None:
                return None
            if self.plan.skipped_details_loaded:
                return self.plan.skipped
            return self._skipped_pages.get(self.preview_page, ())
        if self.plan is not None:
            return self.plan.changes
        if self.selection_plan is not None:
            return self.selection_plan.root_changes
        return ()

    def _render(self, *, update: bool = True) -> None:
        rows = self._display_rows()
        if rows is None:
            self.rows.controls = [kit.hint_text("範圍已變更，請重新預覽。")]
            self.page_text.value = "待重新預覽"
            self.page_nav.visible = False
            self.previous_btn.disabled = True
            self.next_btn.disabled = True
            self.skipped_btn.disabled = True
            self.skipped_btn.text = self._skipped_button_label()
            self.apply_btn.disabled = True
            if update:
                self._update_dialog()
            return
        total = (
            self.plan.skipped_count
            if self.plan is not None and self.show_skipped
            else len(rows)
        )
        pages = max(1, (total + PREVIEW_PAGE_SIZE - 1) // PREVIEW_PAGE_SIZE)
        start = self.preview_page * PREVIEW_PAGE_SIZE
        if self.show_skipped and self.plan and not self.plan.skipped_details_loaded:
            visible = rows
        else:
            visible = rows[start : start + PREVIEW_PAGE_SIZE]
        self.page_nav.visible = total > 0
        self.page_text.value = (
            f"第 {self.preview_page + 1}/{pages} 頁，每頁最多 {PREVIEW_PAGE_SIZE} 筆"
            if self.plan is not None
            else "待重新預覽（不會提交）"
        )
        self.previous_btn.disabled = (
            self.plan is None or self.preview_page == 0 or self._busy
        )
        self.next_btn.disabled = (
            self.plan is None or self.preview_page >= pages - 1 or self._busy
        )
        self.skipped_btn.disabled = (
            self.plan is None or not self.plan.skipped_count or self._busy
        )
        self.skipped_btn.text = self._skipped_button_label()
        self.rows.controls = self._render_rows(visible)
        if self.plan is None:
            self.summary.value = "範圍已變更，請重新預覽。"
        else:
            result = f"可替換 {self.plan.update_count:,d} 筆"
            if self.plan.extra_version_count:
                result += f"（含跨版本 {self.plan.extra_version_count:,d} 筆）"
            if self.plan.skipped_count:
                result += f"　·　略過 {self.plan.skipped_count:,d} 筆"
            self.summary.value = result
        quality_ok = self._quality_ack_is_valid()
        self.apply_btn.disabled = (
            self.plan is None or not self.plan.changes or not quality_ok or self._busy
        )
        if update:
            self._update_dialog()

    def _render_rows(self, visible) -> list[ft.Control]:
        return render_batch_preview_rows(
            visible,
            catalog=self._plan_catalog,
            excluded_roots=self.excluded_roots,
            selection_active=self.selection_plan is not None,
            show_skipped=self.show_skipped,
            busy=self._busy,
            on_toggle=self._toggle_root,
            empty_message=(
                "正在載入略過原因…"
                if self.show_skipped and self._busy
                else "尚未載入此頁略過原因"
                if self.show_skipped
                and self.plan
                and not self.plan.skipped_details_loaded
                else "沒有略過項目"
                if self.show_skipped
                else "沒有可替換項目"
                if self.plan is not None
                else "尚未產生預覽"
            ),
        )

    def _skipped_button_label(self) -> str:
        if self.plan is None:
            return "查看略過原因"
        if self.show_skipped:
            return f"查看可替換項目（{self.plan.update_count:,d}）"
        return f"查看略過原因（{self.plan.skipped_count:,d}）"

    def _open_final_confirmation(self, _e=None) -> None:
        if self._busy or self.plan is None or not self.plan.changes:
            return
        if not self._quality_ack_is_valid():
            show_snack(self.page, "請先確認品質警告，或取消這次替換。", C.GOLD)
            return
        self._final_generation = self._generation
        self._final_plan = self.plan
        plan = self._final_plan
        self.final_summary.value = (
            f"處理範圍：{'僅已勾選根條目' if self.only_checked else '目前篩選範圍'}\n"
            f"個別排除 {len(self.excluded_roots):,d} 個根條目\n"
            f"根條目 {len(plan.root_ids):,d}；實際寫入 {plan.update_count:,d}；"
            f"其中跨版本 {plan.extra_version_count:,d}\n"
            f"略過 {plan.skipped_count:,d}；跨版本衝突 {plan.conflict_count:,d}\n"
            f"品質惡化 {plan.quality_worsened_count:,d} 筆；"
            f"品質混合變化 {plan.quality_mixed_count:,d} 筆\n"
            "若資料庫快照與此預覽不同，交易會拒絕寫入並要求重新預覽。"
        )
        self.final_ack.value = False
        self.confirm_final_btn.disabled = True
        self._final_open = True
        present_page_dialog(self.page, self.final_dialog)

    def _on_final_ack(self, _e=None) -> None:
        self.confirm_final_btn.disabled = not bool(self.final_ack.value)
        self._update_final_dialog()

    def _close_final(self, _e=None) -> None:
        if self._final_open:
            close_page_dialog(self.page, self.final_dialog)
        self._final_open = False

    def _update_final_dialog(self) -> None:
        try:
            self.final_dialog.update()
        except RuntimeError:
            pass

    def _confirm_apply(self, _e=None) -> None:
        plan = getattr(self, "_final_plan", None)
        generation = getattr(self, "_final_generation", None)
        if (
            self._busy
            or not bool(self.final_ack.value)
            or plan is None
            or self.plan is not plan
            or generation != self._generation
            or not self._quality_ack_is_valid()
        ):
            show_snack(self.page, "預覽範圍已變更，請重新核對預覽。", C.GOLD)
            return
        close_page_dialog(self.page, self.final_dialog)
        self._final_open = False
        self._busy = True
        generation = self._generation
        db = self.get_db()
        if db is None:
            self._busy = False
            self.plan = None
            self.summary.value = "資料庫已切換或關閉，請重新預覽。"
            self._render()
            return
        self._set_job_ui("apply", "重新檢查資料庫快照…")

        def work(session: TaskSession):
            return db.execute_batch_replace(
                plan,
                actor="使用者",
                progress_callback=lambda stage, value: self._record_progress(
                    session, generation, stage, value
                ),
            )

        self._start_job("apply", generation, work)

    def _update_dialog(self) -> None:
        try:
            self.dialog.update()
        except RuntimeError:
            pass
