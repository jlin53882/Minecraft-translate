"""資料庫重複鍵清理：預覽高信心路徑別名、合併並可復原。"""

from __future__ import annotations

import sqlite3

import flet as ft

from app.services_impl import moddb_cleanup_service as svc
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    close_page_dialog,
    dialog_dimensions,
    present_page_dialog,
)
from app.ui.page_post import post_to_ui
from app.ui.snack import show_snack
from app.ui.update_scope import isolate, update_regions
from app.views.moddb.formatting import format_count, kind_label
from translation_tool.utils.log_unit import log_exception


class DuplicateCleanupDialog:
    """重複鍵清理的預覽／套用／復原流程。"""

    def __init__(self, page: ft.Page, get_db, version: str, on_complete):
        self.page = page
        self.get_db = get_db
        self.version = version
        self.on_complete = on_complete
        self._busy = False
        self._closed = False
        self._generation = 0
        self._plan = None
        self._batches = []
        self._build_controls()
        self._build_dialog()

    def _build_controls(self) -> None:
        self.summary = ft.Text("請按「預覽合併」掃描目前版本。", size=13, color=C.GOLD)
        self.details = ft.Column(spacing=3, tight=True, scroll=ft.ScrollMode.AUTO)
        self.preview_btn = kit.button("預覽合併", "secondary", on_click=self._preview)
        self.apply_btn = kit.button(
            "確認合併", "primary", on_click=self._apply, disabled=True
        )
        self.batch_dd = kit.dropdown(label="尚未還原的合併", dense=True, width=430)
        self.revert_btn = kit.button(
            "還原選取的合併", "secondary", on_click=self._revert, disabled=True
        )
        self.revert_info = ft.Text("", size=12, color=C.MUTED)
        self.status = ft.Text("", size=12, color=C.MUTED, visible=False)
        self.progress = ft.ProgressBar(visible=False)
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

    def _build_dialog(self) -> None:
        width, height = dialog_dimensions(self.page)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"清理掃描重複鍵（{self.version}）"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "只會合併同版本、同模組、同類型、同原文的語系路徑別名。"
                            "翻譯來源衝突或原文不同的候選只列出，不會自動處理。",
                            size=12.5,
                            color=C.MUTED,
                        ),
                        self.summary,
                        ft.Container(
                            self.details,
                            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                            bgcolor=C.PANEL2,
                            border=ft.Border.all(1, C.LINE),
                            border_radius=design.RADIUS_CONTROL,
                            height=min(260, max(140, height // 3)),
                        ),
                        ft.Row([self.preview_btn], spacing=8),
                        ft.Text("復原", weight=ft.FontWeight.BOLD),
                        ft.Row([self.batch_dd, self.revert_btn], wrap=True, spacing=8),
                        self.revert_info,
                        self.status,
                        self.progress,
                    ],
                    tight=True,
                    spacing=9,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=min(width, 760),
                height=height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[self.close_btn, self.apply_btn],
            on_dismiss=self._on_dismiss,
        )

    def open(self) -> None:
        self._closed = False
        self._generation += 1
        try:
            isolate(self.dialog)
            present_page_dialog(self.page, self.dialog)
            self._load_batches()
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟重複鍵清理視窗。", C.GOLD)

    def _close(self, _e=None) -> None:
        if self._busy:
            show_snack(self.page, "資料庫處理中，請稍候。", C.GOLD)
            return
        self._closed = True
        self._generation += 1
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        self._closed = True
        self._generation += 1

    def _preview(self, _e=None) -> None:
        db = self.get_db()
        if db is None or self._busy:
            return
        previewed_db = db
        generation = self._generation
        self._plan = None
        self._set_busy(True, "掃描重複鍵並建立預覽…")

        def work() -> None:
            plan = error = None
            try:
                plan = svc.preview_duplicate_cleanup(db, self.version)
            except (ValueError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports UI error
                error = exc
                log_exception(f"重複鍵清理預覽失敗：{self.version}")
            self._post(self._after_preview, plan, error, generation, previewed_db)

        self._launch(work, "Mod 資料庫重複鍵預覽", CommitPolicy.PARTIAL_ALLOWED)

    def _after_preview(self, plan, error, generation, previewed_db) -> None:
        if not self._current(generation):
            return
        self._set_busy(False)
        if self.get_db() is not previewed_db:
            self._plan = None
            self.summary.value = "資料庫已變動，請重新預覽。"
            self.summary.color = C.GOLD
            show_snack(self.page, "資料庫已變動，請重新預覽。", C.GOLD)
            self._safe_update()
            return
        if error is not None:
            self.summary.value = f"預覽失敗：{error}"
            self.summary.color = C.RED
            self._safe_update()
            return
        self._plan = (plan, previewed_db)
        self.summary.value = self._summary(plan)
        self.summary.color = C.TEXT if plan.group_count else C.GOLD
        self.details.controls = self._group_lines(plan)
        self.apply_btn.disabled = plan.group_count == 0
        self.apply_btn.content = f"確認合併 {format_count(plan.duplicate_count)} 筆"
        self._safe_update()

    def _apply(self, _e=None) -> None:
        if self._busy:
            return
        if self._plan is None:
            show_snack(self.page, "請先按「預覽合併」，確認候選後才能執行。", C.GOLD)
            return
        plan, previewed_db = self._plan
        db = self.get_db()
        if db is not previewed_db:
            self._plan = None
            show_snack(self.page, "資料庫已變動，請重新預覽。", C.GOLD)
            return
        generation = self._generation
        self._set_busy(True, "合併重複鍵中…")

        def work() -> None:
            result = error = None
            try:
                result = svc.apply_duplicate_cleanup(db, self.version, plan.token)
            except (ValueError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports UI error
                error = exc
                log_exception(f"重複鍵清理套用失敗：{self.version}")
            self._post(self._after_apply, result, error, generation)

        self._launch(
            work,
            "Mod 資料庫重複鍵合併",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _after_apply(self, plan, error, generation) -> None:
        if not self._current(generation):
            return
        self._set_busy(False)
        if error is not None:
            self._plan = None
            self.summary.value = f"合併未執行：{error}"
            self.summary.color = C.RED
            show_snack(self.page, f"合併未執行：{error}", C.RED)
            self._safe_update()
            return
        self._plan = None
        self.summary.value = (
            f"已合併 {format_count(plan.duplicate_count)} 筆，可從下方批次復原。"
        )
        self.summary.color = C.EM
        self.apply_btn.disabled = True
        self._load_batches()
        if plan.batch_id:
            self.batch_dd.value = plan.batch_id
        if self.on_complete:
            self.on_complete()
        self._safe_update()

    def _load_batches(self) -> None:
        db = self.get_db()
        self._batches = (
            [batch for batch in svc.duplicate_cleanup_batches(db) if not batch.complete]
            if db
            else []
        )
        kit.set_dropdown_options(
            self.batch_dd,
            [(batch.batch_id, self._batch_label(batch)) for batch in self._batches],
        )
        self.revert_btn.disabled = not self._batches
        self.revert_info.value = "還沒有重複鍵合併紀錄" if not self._batches else ""

    @staticmethod
    def _batch_label(batch) -> str:
        return (
            f"{batch.created_at}（UTC）　{format_count(batch.group_count)} 組／"
            f"{format_count(batch.entry_count)} 筆"
        )

    def _revert(self, _e=None) -> None:
        db = self.get_db()
        if db is None or not self.batch_dd.value or self._busy:
            return
        generation = self._generation
        self._set_busy(True, "復原重複鍵合併中…")

        def work() -> None:
            result = error = None
            try:
                result = svc.revert_duplicate_cleanup(db, self.batch_dd.value)
            except (ValueError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports UI error
                error = exc
                log_exception(f"重複鍵清理復原失敗：{self.batch_dd.value}")
            self._post(self._after_revert, result, error, generation)

        self._launch(
            work,
            "Mod 資料庫重複鍵復原",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _after_revert(self, result, error, generation) -> None:
        if not self._current(generation):
            return
        self._set_busy(False)
        if error is not None:
            show_snack(self.page, f"復原失敗：{error}", C.RED)
            return
        message = f"已復原 {format_count(result.restored_groups)} 組"
        if result.conflicts:
            message += (
                f"；{format_count(len(result.conflicts))} 組因復原後資料已變動而保留"
            )
        self._load_batches()
        if self.on_complete:
            self.on_complete()
        show_snack(self.page, message, C.EM, text_color=C.ON_EM)
        self._safe_update()

    @staticmethod
    def _summary(plan) -> str:
        message = (
            f"共找到 {format_count(len(plan.candidates))} 組候選；"
            f"可安全合併 {format_count(plan.group_count)} 組／"
            f"{format_count(plan.duplicate_count)} 筆"
        )
        if plan.skipped:
            message += "；" + "、".join(
                f"{format_count(count)} 組{label}"
                for label, count in plan.skipped.items()
            )
        return message

    @staticmethod
    def _group_lines(plan) -> list[ft.Control]:
        lines: list[ft.Control] = []
        for group in plan.candidates[:30]:
            keys = "、".join(entry.key for entry in group.entries)
            state = "可合併" if group.safe else "需人工確認"
            lines.append(
                ft.Text(
                    f"{state}｜{kind_label(group.kind)}／{group.mod_id}／"
                    f"{group.canonical_key}\n  {keys}",
                    size=11.5,
                    color=C.TEXT if group.safe else C.GOLD,
                    selectable=True,
                )
            )
        if len(plan.candidates) > 30:
            lines.append(
                ft.Text(
                    f"另有 {format_count(len(plan.candidates) - 30)} 組未展開。",
                    size=11.5,
                    color=C.MUTED,
                )
            )
        return lines or [ft.Text("目前沒有發現路徑別名重複。", size=12, color=C.MUTED)]

    def _launch(self, work, name, commit, *, durability=DurabilityPolicy.RECOMPUTABLE):
        try:
            launched = launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-duplicate-cleanup",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                commit=commit,
                durability=durability,
                shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
            )
        except Exception as exc:  # noqa: BLE001 - restore dialog when worker cannot launch
            self._set_busy(False)
            log_exception(f"{name}無法啟動")
            show_snack(self.page, f"{name}無法啟動：{exc}", C.RED)
            return
        if not launched:
            self._set_busy(False)
            show_snack(self.page, "應用程式正在關閉，未啟動操作。", C.GOLD)

    def _post(self, callback, *args) -> None:
        post_to_ui(
            self.page,
            callback,
            *args,
            label="Mod 資料庫重複鍵清理結果",
            on_failure=self._mark_post_failure,
        )

    def _current(self, generation) -> bool:
        return not self._closed and generation == self._generation

    def _mark_post_failure(self) -> None:
        self._busy = False

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.preview_btn.disabled = busy
        self.apply_btn.disabled = (
            busy or self._plan is None or self._plan[0].group_count == 0
        )
        self.batch_dd.disabled = busy
        self.revert_btn.disabled = busy or not self._batches
        self.close_btn.disabled = busy
        self.status.visible = busy
        self.status.value = text
        self.progress.visible = busy
        self.progress.value = None if busy else 0

    def _safe_update(self) -> None:
        update_regions(self.dialog)  # 只重畫這個視窗；視窗已關閉時略過
