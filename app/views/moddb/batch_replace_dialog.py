"""Paged, explicit preview and confirmation UI for Mod DB batch replacement."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import flet as ft

from app.services_impl.moddb_service import BatchReplacePlan, EntryFilter
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack

PREVIEW_PAGE_SIZE = 50


class BatchReplaceDialog:
    def __init__(self, page: ft.Page, get_db, criteria: EntryFilter, on_complete):
        self.page = page
        self.get_db = get_db
        self.base_criteria = criteria
        self.on_complete = on_complete
        self.plan: BatchReplacePlan | None = None
        self.selection_plan: BatchReplacePlan | None = None
        self.selected_roots: set[int] | None = None
        self.preview_page = 0
        self.show_skipped = False
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
        self.quality_ack = ft.Checkbox(
            label="我已檢查品質警告，仍要允許這些問題增加",
            value=False,
            visible=False,
            on_change=self._on_quality_ack,
        )
        self.summary = ft.Text(
            "輸入尋找與取代文字，再產生預覽。", size=12.5, color=C.MUTED
        )
        self.rows = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=300)
        self.page_text = ft.Text("尚未預覽", size=11.5, color=C.DIM)

    def _build_buttons(self) -> None:
        self.preview_btn = kit.button("產生預覽", "secondary", on_click=self._preview)
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
        self.skipped_btn = kit.button(
            "顯示略過項目",
            "ghost",
            size="sm",
            on_click=self._toggle_skipped,
            disabled=True,
        )
        self.apply_btn = kit.button(
            "確認並執行替換", "primary", on_click=self._apply, disabled=True
        )

    def _build_dialog(self) -> None:
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("批次字面取代"),
            content=ft.Column(
                [
                    ft.Text(
                        "套用目前清單的版本、模組、來源、狀態、時間及品質條件；只替換目前生效譯文。"
                        "不會修改機翻快取或替換規則。",
                        size=12,
                        color=C.MUTED,
                        selectable=True,
                    ),
                    ft.Row([self.find_field, self.replace_field], spacing=8),
                    self.propagate,
                    ft.Row(
                        [self.preview_btn, self.skipped_btn, self.quality_ack],
                        spacing=8,
                        wrap=True,
                    ),
                    self.summary,
                    self.rows,
                    ft.Row(
                        [self.previous_btn, self.page_text, self.next_btn],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                tight=True,
                spacing=10,
                width=780,
                scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _e: self.page.pop_dialog()),
                self.apply_btn,
            ],
        )

    def open(self) -> None:
        show_dialog = getattr(self.page, "show_dialog", None)
        if callable(show_dialog):
            show_dialog(self.dialog)
        else:
            show_snack(self.page, "目前畫面無法開啟批次替換確認視窗。", C.GOLD)

    def _invalidate(self, _e=None) -> None:
        self.plan = None
        self.selection_plan = None
        self.selected_roots = None
        self.quality_ack.value = False
        self.quality_ack.visible = False
        self.apply_btn.disabled = True
        self.rows.controls = []
        self.summary.value = "條件已變更，請重新產生預覽。"
        self.page_text.value = "尚未預覽"
        self._update_dialog()

    def _criteria(self) -> EntryFilter:
        if self.selected_roots is None:
            return self.base_criteria
        return replace(
            self.base_criteria, include_ids=tuple(sorted(self.selected_roots))
        )

    def _preview(self, _e=None) -> None:
        db = self.get_db()
        if db is None:
            self.summary.value = "目前沒有可用的資料庫。"
            self._update_dialog()
            return
        try:
            self.plan = db.preview_batch_replace(
                self._criteria(),
                self.find_field.value or "",
                self.replace_field.value or "",
                propagate=bool(self.propagate.value),
                confirmed_quality_worsening=bool(self.quality_ack.value),
            )
        except ValueError as exc:
            self.plan = None
            self.summary.value = str(exc)
            self._update_dialog()
            return
        if self.selection_plan is None:
            self.selection_plan = self.plan
            self.selected_roots = set(self.plan.root_ids)
        self.preview_page = 0
        self.quality_ack.visible = any(
            change.quality_worsened for change in self.plan.changes
        )
        self.quality_ack.value = self.plan.confirmed_quality_worsening
        self._render()

    def _on_quality_ack(self, _e=None) -> None:
        if self.plan is not None:
            # Rebuild the immutable plan so the explicit consent is covered by CAS.
            self._preview()

    def _turn(self, delta: int) -> None:
        plan = self.plan or self.selection_plan
        if plan is None:
            return
        rows = (
            plan.skipped
            if self.show_skipped
            else (self.selection_plan.changes if self.selection_plan else plan.changes)
        )
        pages = max(1, (len(rows) + PREVIEW_PAGE_SIZE - 1) // PREVIEW_PAGE_SIZE)
        self.preview_page = min(max(0, self.preview_page + delta), pages - 1)
        self._render()

    def _toggle_skipped(self, _e=None) -> None:
        self.show_skipped = not self.show_skipped
        self.preview_page = 0
        self.skipped_btn.text = (
            "顯示可替換項目" if self.show_skipped else "顯示略過項目"
        )
        self._render()

    def _toggle_root(self, entry_id: int, selected: bool) -> None:
        if self.selection_plan is None:
            return
        if self.selected_roots is None:
            self.selected_roots = set(self.selection_plan.root_ids)
        if selected:
            self.selected_roots.add(entry_id)
        else:
            self.selected_roots.discard(entry_id)
        self.plan = None
        self.quality_ack.value = False
        self.quality_ack.visible = False
        self.apply_btn.disabled = True
        self.summary.value = "選取範圍已變更；請重新產生預覽確認最終筆數。"
        self._render()

    def _render(self) -> None:
        plan = self.plan
        display_plan = plan or self.selection_plan
        if display_plan is None:
            self._update_dialog()
            return
        rows = (
            display_plan.skipped
            if self.show_skipped
            else (
                self.selection_plan.changes
                if self.selection_plan
                else display_plan.changes
            )
        )
        pages = max(1, (len(rows) + PREVIEW_PAGE_SIZE - 1) // PREVIEW_PAGE_SIZE)
        start = self.preview_page * PREVIEW_PAGE_SIZE
        visible = rows[start : start + PREVIEW_PAGE_SIZE]
        self.page_text.value = (
            f"第 {self.preview_page + 1}/{pages} 頁，每頁最多 {PREVIEW_PAGE_SIZE} 筆"
        )
        self.previous_btn.disabled = self.preview_page == 0
        self.next_btn.disabled = self.preview_page >= pages - 1
        self.skipped_btn.disabled = not display_plan.skipped
        if plan is None:
            self.summary.value = (
                f"已選取 {len(self.selected_roots or ()):,d} 筆根條目；"
                "請重新產生預覽查看跨版本影響與最終更新數。"
            )
        else:
            self.summary.value = (
                f"篩選條目 {len(plan.root_ids):,} 筆；將更新 {plan.update_count:,} 筆；"
                f"跨版本候選 {plan.extra_candidate_count:,} 筆（納入 {plan.extra_version_count:,} 筆）；"
                f"略過 {plan.skipped_count:,} 筆。"
            )
        self.rows.controls = self._render_rows(visible)
        quality_ok = not self.quality_ack.visible or bool(self.quality_ack.value)
        self.apply_btn.disabled = plan is None or not plan.changes or not quality_ok
        self._update_dialog()

    def _render_rows(self, visible) -> list[ft.Control]:
        controls: list[ft.Control] = []
        for row in visible:
            if self.show_skipped:
                controls.append(
                    ft.Text(
                        f"略過・{row.mc_version}・{row.key}：{row.reason}",
                        size=11.5,
                        color=C.GOLD,
                        selectable=True,
                    )
                )
            else:
                selected = (
                    self.selected_roots is None or row.entry_id in self.selected_roots
                )
                checkbox: ft.Control = (
                    ft.Checkbox(
                        value=selected,
                        label=f"{row.mc_version}・{row.key}",
                        on_change=lambda e, i=row.entry_id: self._toggle_root(
                            i, bool(e.control.value)
                        ),
                    )
                    if not row.is_extra_version
                    else ft.Text(
                        f"跨版本・{row.mc_version}・{row.key}", size=11.5, color=C.DIM
                    )
                )
                controls.append(
                    ft.Row(
                        [
                            checkbox,
                            ft.Text(
                                f"{row.old_zh_tw} → {row.new_zh_tw}",
                                size=11.5,
                                color=C.TEXT,
                                selectable=True,
                                expand=True,
                            ),
                            ft.Text(
                                "品質變差" if row.quality_worsened else "",
                                size=10.5,
                                color=C.GOLD,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    )
                )
        return controls or [kit.hint_text("此頁沒有條目")]

    def _apply(self, _e=None) -> None:
        db = self.get_db()
        if db is None or self.plan is None:
            return
        if self.quality_ack.visible and not self.quality_ack.value:
            show_snack(self.page, "請先確認品質警告，或取消這次替換。", C.GOLD)
            return
        try:
            result = db.execute_batch_replace(self.plan, actor="使用者")
        except ValueError as exc:
            self.plan = None
            self.apply_btn.disabled = True
            self.summary.value = str(exc)
            self._update_dialog()
            return
        except sqlite3.Error as exc:
            show_snack(self.page, f"批次替換失敗（資料庫錯誤：{exc}）", C.RED)
            return
        self.page.pop_dialog()
        show_snack(
            self.page,
            f"已批次替換 {result.updated:,} 筆；略過 {result.skipped:,} 筆。可在記錄中整批還原。",
            C.EM,
            text_color=C.ON_EM,
        )
        self.on_complete(result)

    def _update_dialog(self) -> None:
        try:
            self.dialog.update()
        except RuntimeError:
            pass
