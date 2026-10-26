"""CAS-backed preview and confirmation for manual review actions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import flet as ft

from app.services_impl.moddb_service import EntryRow, ReviewPreviewItem, TranslationDB
from app.services_impl.moddb_source_service import (
    source_catalog_for,
    source_label,
    source_tone,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    OperationPresentation,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.page_post import has_background_scheduler, post_to_ui
from app.ui.snack import show_snack
from app.views.moddb import entry_writes
from app.views.moddb.format_issue_rendering import quality_issue_summary
from app.views.moddb.formatting import impact_text, shorten
from translation_tool.utils.log_unit import log_exception, log_info, log_warning


@dataclass(frozen=True)
class ImpactResult:
    """某段譯文對應的儲存／審核影響（由資料庫算出，與畫面無關）。

    ``kind``：``none``（沒有影響可顯示）、``changed``（待儲存，``text`` 為影響說明）、
    ``ready``（可審核，``text`` 為審核範圍說明）、``review_error``（審核預覽被拒絕）。
    """

    entry_id: int
    pending_text: str
    kind: str = "none"
    text: str = ""
    preview: tuple[ReviewPreviewItem, ...] = ()


def compute_impact(
    db: TranslationDB, entry: EntryRow, text: str, propagate: bool
) -> ImpactResult:
    """查詢「把輸入框內容存成這筆譯文」或「審核目前譯文」會影響哪些版本。

    只讀資料庫，可在背景執行緒呼叫；``text`` 是輸入框的內容（``pending_text``）。
    """
    if text and text != entry.zh_tw:
        impacts = db.preview_manual(entry.id, text, propagate=propagate)
        return ImpactResult(
            entry.id, text, "changed", impact_text(entry.mc_version, text, impacts)
        )
    ready = bool(
        entry.zh_tw
        and text == entry.zh_tw
        and entry.review_status not in ("reviewed", "legacy_unknown")
    )
    if not ready:
        return ImpactResult(entry.id, text)
    try:
        preview = db.preview_manual_review(
            entry.id,
            expected_zh_tw=entry.zh_tw,
            expected_source=entry.source,
            expected_review_status=entry.review_status,
            expected_checker=entry.checker,
            propagate=propagate,
        )
    except ValueError as exc:
        return ImpactResult(entry.id, text, "review_error", str(exc))
    return ImpactResult(
        entry.id,
        text,
        "ready",
        _review_impact_text(entry, db, preview),
        tuple(preview),
    )


def _review_impact_text(
    entry: EntryRow, db: TranslationDB, preview: list[ReviewPreviewItem]
) -> str:
    included = [item for item in preview if item.included]
    skipped = [item for item in preview if not item.included]
    lines = [
        f"本次審核：{entry.mc_version}（{source_label(entry.source, source_catalog_for(db), entry.review_status)}）",
        "將同步審核："
        + (
            "、".join(item.mc_version for item in included if item.entry_id != entry.id)
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
    return "\n".join(lines)


class ReviewScopeController:
    """Keep review preview, scope choice and execution out of the entries view."""

    def __init__(self, panel):
        self.panel = panel
        self._impact_ticket = 0
        self._executing = False

    def update_impact(self) -> None:
        """同步版本：格式提示、按鈕狀態與影響說明（查詢在呼叫端執行緒）。"""
        panel = self.panel
        panel._update_format_hints()
        entry, db = panel.selected, panel.db()
        text = panel.pending_text()
        if not entry or not db:
            self.apply_impact(None)
            return
        self.apply_impact(compute_impact(db, entry, text, bool(panel.sync_row.value)))

    def request_impact(self) -> None:
        """依輸入框內容重算影響預覽（查詢在背景；較新的請求會讓舊結果作廢）。"""
        panel = self.panel
        entry, db = panel.selected, panel.db()
        if entry is None or db is None:
            return
        propagate = bool(panel.sync_row.value)
        text = panel.pending_text()
        if not has_background_scheduler(panel._page):
            self.apply_impact(compute_impact(db, entry, text, propagate))
            return
        self._impact_ticket += 1
        ticket = self._impact_ticket

        def load() -> None:
            result = None
            try:
                result = compute_impact(db, entry, text, propagate)
            except Exception:  # noqa: BLE001 - 影響預覽失敗不影響編輯
                log_exception(f"Mod 資料庫影響預覽失敗：id={entry.id}")
            post_to_ui(
                panel._page,
                self._apply_requested,
                ticket,
                entry.id,
                text,
                result,
                label="Mod 資料庫影響預覽",
            )

        try:
            launch_page_operation(
                panel._page,
                load,
                name="Mod 資料庫影響預覽",
                owner="moddb-entry-impact",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                shutdown=ShutdownPolicy.DRAIN_ONLY,
                presentation=OperationPresentation.MAINTENANCE,
            )
        except Exception as exc:  # noqa: BLE001 - 無法啟動時保留上一份預覽
            log_warning(f"Mod 資料庫影響預覽無法啟動：{exc!r}")

    def _apply_requested(self, ticket, entry_id, text, result) -> None:
        panel = self.panel
        if (
            ticket != self._impact_ticket
            or result is None
            or panel.selected is None
            or panel.selected.id != entry_id
            or panel.pending_text() != text
        ):
            return  # 輸入又變了、換了條目，或預覽失敗
        self.apply_impact(result)
        panel._flush_editor()

    def update_buttons(self, entry: EntryRow | None = None) -> None:
        """只依輸入框與生效譯文決定儲存／審核按鈕的啟用狀態（不查資料庫）。"""
        panel = self.panel
        entry = entry or panel.selected
        text = panel.pending_text()
        changed = bool(entry and text and text != entry.zh_tw)
        panel.save_btn.disabled = panel._save_running or not changed
        ready = bool(
            entry
            and entry.zh_tw
            and text == entry.zh_tw
            and entry.review_status not in ("reviewed", "legacy_unknown")
        )
        panel.confirm_btn.visible = ready
        panel.confirm_btn.disabled = not ready
        self._update_clear_review_button(entry, changed)

    def update_actions_without_impact(self, entry: EntryRow) -> None:
        """設定編輯區的初始按鈕狀態，不查詢跨版本影響預覽。"""
        panel = self.panel
        panel._update_format_hints()
        self.update_buttons(entry)
        panel._review_preview = []
        panel.impact_box.visible = False

    def _update_clear_review_button(
        self, entry: EntryRow | None, changed: bool
    ) -> None:
        """「解除人工審核」只在 reviewed／legacy_unknown 顯示；有未儲存的修改時停用。"""
        panel = self.panel
        panel.clear_review_btn.visible = bool(
            entry and entry.review_status in ("reviewed", "legacy_unknown")
        )
        panel.clear_review_btn.disabled = panel._save_running or changed

    def apply_impact(self, result: ImpactResult | None) -> None:
        """把影響說明顯示在編輯區；``result`` 為 None 代表沒有可用資料庫。"""
        panel = self.panel
        self.update_buttons()
        panel._review_preview = []
        if result is None or result.kind == "none":
            panel.impact_box.visible = False
            return
        panel.impact_text.value = result.text
        panel.impact_box.visible = True
        if result.kind == "ready":
            panel._review_preview = list(result.preview)
        elif result.kind == "review_error":
            panel.confirm_btn.disabled = True

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

    def clear_review(self, _e=None) -> None:
        """解除目前生效譯文的人工審核標記，不改譯文與來源。"""
        panel = self.panel
        entry, db = panel.selected, panel.db()
        if (
            not entry
            or not db
            or not entry.zh_tw
            or panel.pending_text() != entry.zh_tw
            or entry.review_status not in ("reviewed", "legacy_unknown")
        ):
            show_snack(
                panel._page,
                "目前條目不是可解除的人工審核狀態，請先重新整理。",
                C.GOLD,
            )
            return

        def work():
            db.clear_manual_review(
                entry.id,
                expected_zh_tw=entry.zh_tw,
                expected_source=entry.source,
                expected_review_status=entry.review_status,
                expected_checker=entry.checker,
                actor="使用者",
            )

        def apply(_value, error) -> None:
            if error is not None:
                self._apply_clear_error(entry, error)
                return
            message = "已解除人工審核，譯文與來源未變更"
            log_info(
                f"Mod 資料庫解除人工審核：{entry.mc_version} {entry.mod_id} {entry.key}"
            )
            show_snack(panel._page, message, C.EM, text_color=C.ON_EM)
            entry_writes.after_data_change(panel, message)

        if not entry_writes.run_write(
            panel._page,
            work,
            apply,
            name="Mod 資料庫解除人工審核",
            owner="moddb-clear-review",
            label="Mod 資料庫解除人工審核結果",
        ):
            show_snack(panel._page, "解除審核無法啟動，或應用程式正在關閉。", C.GOLD)

    def _apply_clear_error(self, entry: EntryRow, error: BaseException) -> None:
        if isinstance(error, ValueError):
            self._reject_clear(entry, error)
        elif isinstance(error, sqlite3.Error):
            log_exception(
                f"Mod 資料庫解除人工審核失敗：{entry.mc_version} "
                f"{entry.mod_id} {entry.key}"
            )
            show_snack(
                self.panel._page,
                f"解除人工審核失敗（資料庫錯誤：{error}）；詳情見後台 log",
                C.RED,
            )
        else:
            raise error

    def _reject_clear(self, entry: EntryRow, exc: Exception) -> None:
        panel = self.panel
        log_warning(
            f"Mod 資料庫解除人工審核被拒絕：{entry.mc_version} "
            f"{entry.mod_id} {entry.key}（{exc!r}）"
        )
        show_snack(panel._page, str(exc), C.GOLD)
        entry_writes.after_data_change(panel)

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
        quality_messages = quality_issue_summary(
            entry.quality_issues, entry.whitespace_note
        )
        quality = (
            "格式檢查：" + "、".join(quality_messages)
            if quality_messages
            else "格式檢查通過"
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
        """審核寫入在背景執行；完成後才關閉確認視窗並重整清單。"""
        if self._executing:
            return
        panel = self.panel
        self._executing = True

        def work():
            expected = preview or db.preview_manual_review(
                entry.id,
                expected_zh_tw=entry.zh_tw,
                expected_source=entry.source,
                expected_review_status=entry.review_status,
                expected_checker=entry.checker,
                propagate=False,
            )
            return db.review_manual(
                entry.id,
                expected_zh_tw=entry.zh_tw,
                expected_source=entry.source,
                expected_review_status=entry.review_status,
                expected_checker=entry.checker,
                expected_preview=expected,
                actor="使用者",
                propagate=propagate,
            )

        def apply(done, error) -> None:
            self._executing = False
            if error is not None:
                if isinstance(error, ValueError | sqlite3.Error):
                    self._reject(
                        entry, error, database=isinstance(error, sqlite3.Error)
                    )
                    return
                raise error
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
            show_snack(panel._page, message, C.EM, text_color=C.ON_EM)
            entry_writes.after_data_change(panel, message)

        launched = entry_writes.run_write(
            panel._page,
            work,
            apply,
            name="Mod 資料庫人工審核",
            owner="moddb-manual-review",
            label="Mod 資料庫審核結果",
            on_failure=lambda: setattr(self, "_executing", False),
        )
        if not launched:
            self._executing = False
            show_snack(panel._page, "審核無法啟動，或應用程式正在關閉。", C.GOLD)

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
        entry_writes.after_data_change(panel)
