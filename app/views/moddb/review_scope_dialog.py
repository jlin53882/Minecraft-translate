"""CAS-backed preview and confirmation for manual review actions."""

from __future__ import annotations

import sqlite3

import flet as ft

from app.services_impl.moddb_service import EntryRow, ReviewPreviewItem, TranslationDB
from app.services_impl.moddb_source_service import source_catalog_for, source_label
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.formatting import impact_text, shorten
from translation_tool.utils.log_unit import log_exception, log_info, log_warning


class ReviewScopeController:
    """Keep review preview, scope choice and execution out of the entries view."""

    def __init__(self, panel):
        self.panel = panel

    def update_impact(self) -> None:
        panel = self.panel
        panel._update_format_hints()
        entry, db = panel.selected, panel.db()
        text = panel.pending_text()
        changed = bool(entry and db and text and text != entry.zh_tw)
        panel.save_btn.disabled = not changed
        ready = bool(
            entry
            and db
            and entry.zh_tw
            and text == entry.zh_tw
            and entry.review_status != "reviewed"
            and not changed
        )
        panel.confirm_btn.visible = ready
        panel.confirm_btn.disabled = not ready
        panel._review_preview = []
        if changed:
            impacts = db.preview_manual(entry.id, text, propagate=panel.sync_row.value)
            panel.impact_text.value = impact_text(entry.mc_version, text, impacts)
            panel.impact_box.visible = True
        elif ready:
            self._set_review_impact(entry, db)
        else:
            panel.impact_box.visible = False

    def _set_review_impact(self, entry: EntryRow, db: TranslationDB) -> None:
        panel = self.panel
        try:
            preview = db.preview_manual_review(
                entry.id,
                expected_zh_tw=entry.zh_tw,
                expected_source=entry.source,
                expected_review_status=entry.review_status,
                expected_checker=entry.checker,
                propagate=panel.sync_row.value,
            )
        except ValueError as exc:
            panel.impact_text.value = str(exc)
            panel.impact_box.visible = True
            panel.confirm_btn.disabled = True
            return
        panel._review_preview = preview
        included = [item for item in preview if item.included]
        skipped = [item for item in preview if not item.included]
        lines = [
            f"本次審核：{entry.mc_version}（{source_label(entry.source, source_catalog_for(db), entry.review_status)}）",
            "將同步審核："
            + (
                "、".join(
                    item.mc_version for item in included if item.entry_id != entry.id
                )
                or "無其他版本"
            ),
        ]
        if skipped:
            lines.append(
                "略過："
                + "；".join(f"{item.mc_version}：{item.reason}" for item in skipped)
            )
        lines.extend(
            f"{item.mc_version} 將取代非生效人工譯文：{shorten(item.manual_text or '', 36)}"
            for item in included
            if item.manual_text is not None and item.manual_text != item.text
        )
        panel.impact_text.value = "\n".join(lines)
        panel.impact_box.visible = True

    def confirm(self, _e=None) -> None:
        panel = self.panel
        entry, db = panel.selected, panel.db()
        if (
            not entry
            or not db
            or not entry.zh_tw
            or panel.pending_text() != entry.zh_tw
        ):
            show_snack(
                panel._page,
                "編輯框必須仍與目前選取的生效譯文相同，才能審核。",
                C.GOLD,
            )
            return
        try:
            preview = db.preview_manual_review(
                entry.id,
                expected_zh_tw=entry.zh_tw,
                expected_source=entry.source,
                expected_review_status=entry.review_status,
                expected_checker=entry.checker,
                propagate=panel.sync_row.value,
            )
        except ValueError as exc:
            self._reject(entry, exc)
            return
        self._show_scope(entry, db, preview, bool(panel.sync_row.value))

    def _show_scope(
        self,
        entry: EntryRow,
        db: TranslationDB,
        preview: list[ReviewPreviewItem],
        propagate: bool,
    ) -> None:
        panel = self.panel
        included = [item for item in preview if item.included]
        skipped = [item for item in preview if not item.included]
        lines = [
            f"審核：{item.mc_version}・{item.text}"
            + (
                f"（將取代非生效人工譯文：{shorten(item.manual_text or '', 36)}）"
                if item.manual_text is not None and item.manual_text != item.text
                else ""
            )
            for item in included
        ]
        lines.extend(f"略過：{item.mc_version}・{item.reason}" for item in skipped)
        content = ft.Column(
            [
                ft.Text(
                    "請確認下列版本與人工來源變更，再選擇審核範圍。",
                    size=12.5,
                    color=C.MUTED,
                    selectable=True,
                ),
                ft.Column(
                    [
                        ft.Text(line, size=12, color=C.TEXT, selectable=True)
                        for line in lines
                    ],
                    spacing=5,
                    scroll=ft.ScrollMode.AUTO,
                    height=280,
                ),
            ],
            tight=True,
            width=520,
            spacing=10,
        )
        actions = [
            ft.TextButton("取消", on_click=lambda _e=None: panel._page.pop_dialog())
        ]
        others = sum(item.included and item.entry_id != entry.id for item in preview)
        if propagate and others:
            actions.append(
                ft.TextButton(
                    "僅審核目前版本",
                    on_click=lambda _e=None: self.execute(entry, db, False),
                )
            )
            label = f"審核預覽範圍（{len(included)} 版）"
        else:
            label = "確認審核目前版本"
        actions.append(
            ft.TextButton(
                label,
                on_click=lambda _e=None: self.execute(entry, db, propagate, preview),
            )
        )
        show_dialog = getattr(panel._page, "show_dialog", None)
        if callable(show_dialog):
            show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("確認審核範圍"),
                    content=content,
                    actions=actions,
                )
            )
        else:
            show_snack(
                panel._page, "目前畫面無法顯示審核範圍確認視窗，未執行審核。", C.GOLD
            )

    def execute(
        self,
        entry: EntryRow,
        db: TranslationDB,
        propagate: bool,
        preview: list[ReviewPreviewItem] | None = None,
    ) -> None:
        panel = self.panel
        try:
            expected = preview or db.preview_manual_review(
                entry.id,
                expected_zh_tw=entry.zh_tw,
                expected_source=entry.source,
                expected_review_status=entry.review_status,
                expected_checker=entry.checker,
                propagate=False,
            )
            done = db.review_manual(
                entry.id,
                expected_zh_tw=entry.zh_tw,
                expected_source=entry.source,
                expected_review_status=entry.review_status,
                expected_checker=entry.checker,
                expected_preview=expected,
                actor="使用者",
                propagate=propagate,
            )
        except (ValueError, sqlite3.Error) as exc:
            self._reject(entry, exc, database=isinstance(exc, sqlite3.Error))
            return
        panel._page.pop_dialog()
        others = [change.mc_version for change in done if not change.is_self]
        log_info(
            f"Mod 資料庫人工審核：{entry.mc_version} {entry.mod_id} {entry.key}"
            f"（同步 {len(others)} 個版本：{'、'.join(others) or '無'}）"
        )
        message = (
            f"已審核，並同步 {len(others)} 個版本：{'、'.join(others)}"
            if others
            else "已標記為人工-已審核"
        )
        panel._load_list(page=panel.pager.current_page, keep_scroll=True)
        panel.saved_text.value = message
        show_snack(panel._page, message, C.EM, text_color=C.ON_EM)
        if panel._on_changed:
            panel._on_changed()
        panel._safe_update()

    def _reject(
        self, entry: EntryRow, exc: Exception, *, database: bool = False
    ) -> None:
        panel = self.panel
        if database:
            log_exception(
                f"Mod 資料庫審核失敗：{entry.mc_version} {entry.mod_id} {entry.key}"
            )
            show_snack(
                panel._page, f"審核失敗（資料庫錯誤：{exc}）；詳情見後台 log", C.RED
            )
            return
        log_warning(
            f"Mod 資料庫審核被拒絕：{entry.mc_version} {entry.mod_id} {entry.key}（{exc!r}）"
        )
        show_snack(panel._page, str(exc), C.GOLD)
        panel._load_list(page=panel.pager.current_page, keep_scroll=True)
        panel._safe_update()
