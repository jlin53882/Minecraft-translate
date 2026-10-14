"""CAS-backed preview and confirmation for manual review actions."""

from __future__ import annotations

import sqlite3

import flet as ft

from app.services_impl.moddb_service import EntryRow, ReviewPreviewItem, TranslationDB
from app.services_impl.moddb_source_service import (
    source_catalog_for,
    source_label,
    source_tone,
)
from app.ui import design, kit
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
        panel.save_btn.disabled = panel._save_running or not changed
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
        show_dialog = getattr(panel._page, "show_dialog", None)
        if not callable(show_dialog):
            show_snack(
                panel._page, "目前畫面無法顯示審核範圍確認視窗，未執行審核。", C.GOLD
            )
            return
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                "確認條目審核", size=20, weight=ft.FontWeight.BOLD, color=C.TEXT
            ),
            actions_alignment=ft.MainAxisAlignment.END,
            scrollable=True,
            title_padding=ft.Padding(left=24, top=20, right=24, bottom=8),
            content_padding=ft.Padding(left=24, top=8, right=24, bottom=8),
            actions_padding=ft.Padding(left=24, top=8, right=24, bottom=20),
            inset_padding=ft.Padding.symmetric(horizontal=20, vertical=24),
            shape=ft.RoundedRectangleBorder(radius=design.RADIUS_CARD),
        )
        # 開關預設沿用編輯面板的「同步其他版本」；在視窗內切換會重算審核範圍
        sync_switch = ft.Switch(value=propagate)

        def fill(items: list[ReviewPreviewItem], sync: bool) -> None:
            included = [item for item in items if item.included]
            skipped = [item for item in items if not item.included]
            dialog.content = self._build_scope_content(
                entry, db, included, skipped, sync_switch
            )
            dialog.actions = self._build_scope_actions(entry, db, items, included, sync)

        def on_sync_change(_e=None) -> None:
            sync = bool(sync_switch.value)
            try:
                items = db.preview_manual_review(
                    entry.id,
                    expected_zh_tw=entry.zh_tw,
                    expected_source=entry.source,
                    expected_review_status=entry.review_status,
                    expected_checker=entry.checker,
                    propagate=sync,
                )
            except ValueError as exc:
                panel._page.pop_dialog()
                self._reject(entry, exc)
                return
            fill(items, sync)
            panel._page.update()

        sync_switch.on_change = on_sync_change
        fill(preview, propagate)
        show_dialog(dialog)

    def _build_scope_content(
        self,
        entry: EntryRow,
        db: TranslationDB,
        included: list[ReviewPreviewItem],
        skipped: list[ReviewPreviewItem],
        sync_switch: ft.Switch,
    ) -> ft.Column:
        """Build the review explanation without changing the review scope."""
        return ft.Column(
            [
                ft.Text(
                    "確認原文、目前生效譯文與版本範圍；確認後會標為人工已審核，生效來源不變。",
                    size=12.5,
                    color=C.MUTED,
                    selectable=True,
                ),
                self._build_scope_summary(included, skipped),
                ft.Divider(height=1, color=C.LINE),
                self._build_entry_comparison(entry, db),
                ft.Divider(height=1, color=C.LINE),
                self._build_version_scope(entry, db, included, skipped),
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text(
                                    "同步審核其他版本",
                                    size=14,
                                    weight=ft.FontWeight.W_600,
                                    color=C.TEXT,
                                ),
                                ft.Text(
                                    "原文相同的其他版本會一併改用此譯文並標為已審核（可還原）；關閉則只審核目前版本。",
                                    size=11.5,
                                    color=C.MUTED,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        sync_switch,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(
                    "確認後會重新檢查資料是否被其他操作修改；只會處理上面列出的版本，"
                    "不會修改英文原文或略過版本。開啟同步時，其他版本目前不同的譯文也會被取代"
                    "（可從歷史還原）。",
                    size=11.5,
                    color=C.MUTED,
                    selectable=True,
                ),
            ],
            width=760,
            spacing=16,
            tight=True,
        )

    @staticmethod
    def _build_scope_summary(
        included: list[ReviewPreviewItem], skipped: list[ReviewPreviewItem]
    ) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                [
                    kit.stat_card("本次審核條目", "1 筆", tone="dia", expand=True),
                    kit.stat_card(
                        "審核版本", str(len(included)), tone="em", expand=True
                    ),
                    kit.stat_card(
                        "略過版本", str(len(skipped)), tone="gold", expand=True
                    ),
                ],
                spacing=12,
            ),
            padding=ft.Padding.only(bottom=2),
        )

    def _build_entry_comparison(self, entry: EntryRow, db: TranslationDB) -> ft.Column:
        catalog = source_catalog_for(db)
        source_name = source_label(entry.source, catalog, entry.review_status)
        quality = (
            "格式檢查通過"
            if not entry.quality_issues
            else "格式檢查：" + "、".join(entry.quality_issues)
        )
        return ft.Column(
            [
                ft.Text("條目內容", size=15, weight=ft.FontWeight.BOLD, color=C.TEXT),
                ft.Text(entry.mod_id, size=12.5, color=C.MUTED),
                ft.Text(
                    entry.key,
                    size=13,
                    color=C.DIA,
                    font_family=design.FONT_MONO,
                    selectable=True,
                ),
                ft.Row(
                    [
                        self._build_language_box(
                            "英文原文", "en_us", entry.en_us, "dia"
                        ),
                        self._build_language_box(
                            "繁體中文譯文", "zh_tw", entry.zh_tw, "em", quality
                        ),
                    ],
                    spacing=12,
                ),
                ft.Row(
                    [
                        ft.Text("目前生效來源", size=12, color=C.MUTED),
                        kit.chip(source_name, source_tone(entry.source)),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=8,
        )

    @staticmethod
    def _build_language_box(
        label: str,
        language: str,
        value: str,
        tone: str,
        status: str | None = None,
    ) -> ft.Container:
        palette = design.tone(tone)
        heading: list[ft.Control] = [
            ft.Text(f"{label}・{language}", size=12, color=C.MUTED),
        ]
        if status:
            heading.append(kit.chip(status, tone))
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(heading, spacing=8),
                    ft.Text(
                        value or "（內容為空）", size=15, color=C.TEXT, selectable=True
                    ),
                ],
                spacing=8,
                tight=True,
            ),
            width=360,
            padding=12,
            bgcolor=palette.bg,
            border=ft.Border.all(1, palette.line),
            border_radius=design.RADIUS_CONTROL,
        )

    def _build_version_scope(
        self,
        entry: EntryRow,
        db: TranslationDB,
        included: list[ReviewPreviewItem],
        skipped: list[ReviewPreviewItem],
    ) -> ft.Column:
        items = [
            self._build_version_item(entry, item) for item in [*included, *skipped]
        ]
        return ft.Column(
            [
                ft.Text(
                    "版本審核範圍", size=15, weight=ft.FontWeight.BOLD, color=C.TEXT
                ),
                ft.Column(items, spacing=8, tight=True),
            ],
            spacing=10,
            tight=True,
        )

    @staticmethod
    def _build_version_item(entry: EntryRow, item: ReviewPreviewItem) -> ft.Container:
        if item.included:
            tone = "em"
            status = "本次審核" if item.entry_id == entry.id else "同步審核"
            icon = ft.Icons.CHECK_CIRCLE_OUTLINE
            if item.entry_id == entry.id:
                detail = "將審核目前顯示的繁體中文譯文"
            else:
                detail = "原文與目前譯文相同，將同步審核此版本"
                if item.text != entry.zh_tw:
                    detail = (
                        "原文相同，將以目前譯文取代此版本的生效譯文："
                        f"{shorten(item.text, 36) or '（無）'}"
                    )
            if item.manual_text is not None and item.manual_text != item.text:
                detail += f"；將取代非生效人工譯文：{shorten(item.manual_text, 36)}"
        else:
            tone = "neutral"
            status = "略過"
            icon = ft.Icons.REMOVE_CIRCLE_OUTLINE
            detail = f"{item.reason}，不會同步審核此版本"
        palette = design.tone(tone)
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, size=19, color=palette.fg),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        item.mc_version,
                                        size=14,
                                        weight=ft.FontWeight.BOLD,
                                        color=C.TEXT,
                                    ),
                                    kit.chip(status, tone),
                                ],
                                spacing=8,
                            ),
                            ft.Text(detail, size=12, color=C.MUTED, selectable=True),
                        ],
                        spacing=5,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=12,
            bgcolor=design.tone("em").bg if item.included else C.PANEL2,
            border=ft.Border.all(1, palette.line),
            border_radius=design.RADIUS_CONTROL,
        )

    def _build_scope_actions(
        self,
        entry: EntryRow,
        db: TranslationDB,
        preview: list[ReviewPreviewItem],
        included: list[ReviewPreviewItem],
        propagate: bool,
    ) -> list[ft.Control]:
        panel = self.panel
        actions = [
            kit.button(
                "取消", "ghost", on_click=lambda _e=None: panel._page.pop_dialog()
            )
        ]
        others = sum(item.included and item.entry_id != entry.id for item in preview)
        if propagate and others:
            label = f"確認審核 {len(included)} 版"
        else:
            label = "確認審核目前版本"
        actions.append(
            kit.button(
                label,
                "primary",
                icon=ft.Icons.VERIFIED_OUTLINED,
                on_click=lambda _e=None: self.execute(entry, db, propagate, preview),
            )
        )
        return actions

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
            else "已標記為人工已審核（來源不變）"
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
