"""Single-entry, bounded-control review of rejected AI repair drafts."""

from __future__ import annotations

import asyncio

import flet as ft

from app.services_impl import moddb_repair_review_store
from app.services_impl.moddb_format_diff_service import compare_format_diff
from app.services_impl.moddb_retranslate_service import (
    apply_and_record_repair_review,
    translation_issues,
)
from app.ui import design
from app.ui.design import C
from app.ui.snack import show_snack
from app.ui.sync_text_field import SyncTextField
from app.views.moddb.format_diff_review import FormatDiffReview
from app.views.moddb.format_diff_viewer import FormatDiffViewer
from app.views.moddb.formatting import format_count


class RepairReviewer:
    def __init__(self, page, store_path: str, run_id: str, on_close=None) -> None:
        self.page = page
        self.store_path = store_path
        self.run_id = run_id
        self.on_close = on_close
        self.status_filter = "pending"
        self.page_size = 100
        self.page_offset = 0
        self.filtered_count = 0
        self.closed = False
        self.item_indices: list[int] = []
        self.position = 0
        self.item: dict | None = None
        self.group_members: list[dict] = []
        self.draft: ft.TextField | None = None
        self.format_status = ft.Text("", size=12, color=C.MUTED)
        self.format_diff_viewer = FormatDiffViewer(switch_label="顯示格式差異")
        self.format_review = FormatDiffReview(
            context_label="AI 修復預覽",
            panel_width=240,
        )
        self.status = ft.Text("", size=13, weight=ft.FontWeight.BOLD, color=C.MUTED)
        self.applying = False
        self.previous_button = ft.OutlinedButton(
            "上一筆", icon=ft.Icons.ARROW_BACK, on_click=lambda _e: self._move(-1)
        )
        self.next_button = ft.OutlinedButton(
            "下一筆", icon=ft.Icons.ARROW_FORWARD, on_click=lambda _e: self._move(1)
        )
        self.keep_button = ft.OutlinedButton(
            "保留舊譯文", icon=ft.Icons.RESTORE, on_click=self._keep_old
        )
        self.apply_button = ft.FilledButton(
            "確認套用", icon=ft.Icons.CHECK, on_click=self._apply
        )
        self.close_button = ft.TextButton(
            "關閉", icon=ft.Icons.CLOSE, on_click=lambda _e: self._close()
        )
        self.action_buttons = [
            self.previous_button,
            self.next_button,
            self.keep_button,
            self.apply_button,
            self.close_button,
        ]
        self.status_filter_control = None
        self.progress_bar = ft.ProgressBar(
            value=0, height=5, color=C.EM, bgcolor=C.TRACK
        )
        self.body = ft.Column([], height=660, scroll=ft.ScrollMode.AUTO, spacing=12)
        self.position_text = ft.Text(
            "", size=13, color=C.TEXT, weight=ft.FontWeight.W_600
        )
        self.content_box = ft.Container(content=self.body, width=1120, height=720)
        self.apply_confirmation_dialog: ft.AlertDialog | None = None
        self._pending_apply_value: str | None = None
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("AI 修復結果・逐筆審查"),
            content=self.content_box,
            actions=self._actions(),
        )

    def open(self) -> None:
        self.filtered_count = moddb_repair_review_store.index_count(
            self.store_path, self.run_id, self.status_filter, grouped=True
        )
        if not self.filtered_count:
            self.status_filter = "all"
            self.filtered_count = moddb_repair_review_store.index_count(
                self.store_path, self.run_id, self.status_filter, grouped=True
            )
        self.page_offset = 0
        self._load_index_page()
        if not self.filtered_count:
            return
        page_width = getattr(self.page, "width", None)
        page_height = getattr(self.page, "height", None)
        width = min(1120, max(420, float(page_width) - 32)) if page_width else 1120
        height = min(760, max(500, float(page_height) - 80)) if page_height else 760
        self.content_box.width = width
        self.content_box.height = height
        self.body.height = height - 28
        self._render()
        self.page.show_dialog(self.dialog)

    def _load_index_page(self) -> None:
        self.item_indices = moddb_repair_review_store.indices(
            self.store_path,
            self.run_id,
            self.status_filter,
            limit=self.page_size,
            offset=self.page_offset,
            grouped=True,
        )

    def _actions(self):
        return [
            ft.Row(
                [
                    ft.Row(
                        [self.previous_button, self.next_button],
                        spacing=6,
                        wrap=True,
                    ),
                    ft.Row(
                        [self.keep_button, self.close_button, self.apply_button],
                        spacing=6,
                        wrap=True,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
            )
        ]

    def _status_summary(self, counts: dict[str, int], total: int) -> str:
        pending = counts.get("pending", 0) + counts.get("invalid", 0)
        return (
            f"待確認 {format_count(pending)} · "
            f"已套用 {format_count(counts.get('applied', 0))} · "
            f"保留舊譯文 {format_count(counts.get('kept_old', 0))} · "
            f"資料已變動 {format_count(counts.get('stale', 0))} / {total}"
        )

    def _update_progress(self, counts: dict[str, int], total: int) -> None:
        current = self.page_offset + self.position + 1
        self.position_text.value = (
            f"第 {current} / {self.filtered_count} 筆\n"
            f"{self._status_summary(counts, total)}"
        )
        self.progress_bar.value = (
            min(1.0, current / self.filtered_count) if self.filtered_count else 0
        )

    def _card(
        self,
        title: str,
        value: str,
        *,
        tone: str = "neutral",
        height: int | None = None,
    ) -> ft.Container:
        tone_colors = {
            "neutral": (C.MUTED, C.PANEL, C.LINE),
            "gold": (C.GOLD, C.GOLD_BG, C.GOLD),
            "dia": (C.DIA, C.DIA_BG, C.DIA),
        }
        color, bgcolor, line = tone_colors.get(tone, tone_colors["neutral"])
        text = ft.Text(
            value or "（無內容）",
            size=13,
            color=C.TEXT,
            selectable=True,
            height=height,
        )
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=11.5, color=color),
                    text,
                ],
                spacing=8,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            bgcolor=bgcolor,
            border=ft.Border.all(1, line),
            border_radius=design.RADIUS_CONTROL,
        )

    def _source_text(self, item: dict) -> str:
        """「原來源 #2」；同一組相同內容有多個來源時列出全部，確認後一併套用。"""
        sources = sorted(
            {int(m.get("source_id", 0)) for m in self.group_members}
            | {int(item.get("source_id", 0))}
        )
        if len(sources) <= 1:
            return f"原來源 #{item.get('source_id', '')}"
        listed = "、".join(f"#{s}" for s in sources)
        return f"原來源 {listed}（相同內容，共 {len(sources)} 個來源，一併審查）"

    def _identity_card(self, item: dict) -> ft.Container:
        identity = ft.Row(
            [
                ft.Text(str(item.get("version") or ""), size=12, color=C.MUTED),
                ft.Text("·", size=12, color=C.DIM),
                ft.Text(str(item.get("mod_id") or ""), size=12, color=C.MUTED),
                ft.Text("·", size=12, color=C.DIM),
                ft.Text(str(item.get("kind") or ""), size=12, color=C.MUTED),
                ft.Text("·", size=12, color=C.DIM),
                ft.Text(
                    self._source_text(item),
                    size=12,
                    color=C.GOLD,
                ),
            ],
            spacing=6,
            wrap=True,
        )
        return ft.Container(
            content=ft.Column(
                [
                    identity,
                ],
                spacing=8,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=11),
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
        )

    def _review_columns(self, item: dict) -> ft.Row:
        available = float(self.content_box.width or 1120)
        column_width = max(300, (available - 16) / 2)
        left = ft.Column(
            [
                self._card("英文原文", str(item.get("en_us") or ""), tone="dia"),
                self._card(
                    "保留中的舊譯文",
                    str(item.get("old_translation") or ""),
                    tone="gold",
                ),
                self._card(
                    "未寫入的 AI 結果",
                    str(item.get("ai_translation") or ""),
                ),
            ],
            spacing=10,
            width=column_width,
        )
        draft_card = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "AI 草稿（可編輯）",
                        size=13,
                        weight=ft.FontWeight.W_600,
                        color=C.EM,
                    ),
                    ft.Text(
                        "可直接修改；內容會即時保存為草稿，格式檢查結果會顯示在下方。",
                        size=11.5,
                        color=C.MUTED,
                    ),
                    self.format_status,
                    self.format_review.control,
                ],
                spacing=10,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=14),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.EM_LINE),
            border_radius=design.RADIUS_CONTROL,
            width=column_width,
        )
        return ft.Row(
            [left, draft_card],
            spacing=16,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _render(self) -> None:
        if not self.item_indices:
            counts = moddb_repair_review_store.status_counts(
                self.store_path, self.run_id, grouped=True
            )
            total = sum(counts.values())
            self.item = None
            self.draft = None
            self.position_text.value = self._status_summary(counts, total)
            self.status.value = "此篩選目前沒有項目。請切換審查篩選或關閉視窗。"
            self.status.color = C.MUTED
            self.body.controls = [
                self.position_text,
                self._filter_control(),
                self.status,
            ]
            self.page.update()
            return
        index = self.item_indices[self.position]
        self.item = moddb_repair_review_store.load_item(
            self.store_path, self.run_id, index
        )
        if self.item is None:
            self.status.value = "無法載入此筆審查資料。"
            self.page.update()
            return
        self.group_members = [
            moddb_repair_review_store.load_item(self.store_path, self.run_id, i)
            for i in moddb_repair_review_store.group_indices(
                self.store_path, self.run_id, index
            )
        ]
        self.group_members = [m for m in self.group_members if m is not None]
        self._show_review_status(self.item.get("review_status", "pending"))
        counts = moddb_repair_review_store.status_counts(
            self.store_path, self.run_id, grouped=True
        )
        total = sum(counts.values())
        self._update_progress(counts, total)
        item = self.item
        self.draft = SyncTextField(
            label="修改後的譯文",
            value=str(item.get("draft") or ""),
            multiline=True,
            min_lines=10,
            max_lines=16,
            height=360,
            width=max(240.0, float(self.content_box.width or 1120) / 2 - 36),
            on_change=self._draft_changed,
        )
        self.format_review.set_editable_control(self.draft)
        self._update_format_status(
            str(item.get("en_us") or ""), str(item.get("draft") or "")
        )
        status_filter = self._filter_control()
        self.body.controls = [
            self.position_text,
            status_filter,
            self.status,
            self.progress_bar,
            ft.Text(
                str(item.get("key") or ""),
                size=13,
                color=C.TEXT,
                selectable=True,
                font_family=design.FONT_MONO,
            ),
            self._identity_card(item),
            self._review_columns(item),
        ]
        self.page.update()

    def _filter_control(self):
        status_filter = ft.Dropdown(
            label="審查篩選",
            value=self.status_filter,
            width=190,
            options=[
                ft.dropdown.Option(key, label)
                for key, label in (
                    ("pending", "待審查"),
                    ("applied", "已套用"),
                    ("kept_old", "保留舊譯文"),
                    ("stale", "資料已變動"),
                    ("invalid", "待確認草稿"),
                    ("all", "全部狀態"),
                )
            ],
        )
        status_filter.on_select = self._change_filter
        self.status_filter_control = status_filter
        status_filter.disabled = self.applying
        return status_filter

    def _update_format_status(self, source: str, draft: str) -> None:
        kind = str((self.item or {}).get("kind") or "")
        diff_result = compare_format_diff(source, draft, kind=kind)
        messages = [*diff_result.legacy_messages, *diff_result.guide_issues]
        if messages:
            self.format_status.value = "格式檢查：" + "；".join(messages)
            self.format_status.color = C.GOLD
        else:
            self.format_status.value = "格式檢查通過。"
            self.format_status.color = C.EM
        self.format_diff_viewer.render(source, draft, diff_result)
        self.format_review.render(source, draft, diff_result)

    def _draft_changed(self, _event=None) -> None:
        if self.applying or self.item is None or self.draft is None:
            return
        self._save_draft(status="pending")
        self._update_format_status(
            str(self.item.get("en_us") or ""), self.draft.value or ""
        )
        self.page.update()

    def _show_review_status(self, status: str) -> None:
        source_id = self.item.get("source_id", "?") if self.item else "?"
        messages = {
            "applied": (
                f"✓ 已套用至原來源 #{source_id}；修復內容與歷史紀錄已保存。",
                C.EM,
            ),
            "kept_old": ("已保留舊譯文；沒有寫入翻譯資料，也沒有新增歷史。", C.EM),
            "stale": ("資料已變動；草稿保留，請重新讀取並比較。", C.GOLD),
            "invalid": ("此筆草稿待確認，可由你決定是否套用。", C.GOLD),
            "pending": ("待審查：尚未套用。", C.MUTED),
        }
        message, color = messages.get(status, ("", C.MUTED))
        self.status.value = message
        self.status.color = color

    def _save_draft(self, _event=None, status=None) -> None:
        if self.applying:
            return
        if self.item is None or self.draft is None:
            return
        value = self.draft.value or ""
        if status is None:
            unchanged = value == str(self.item.get("draft") or "")
            status = (
                self.item.get("review_status", "pending") if unchanged else "pending"
            )
        self.item["draft"] = value
        self.item["review_status"] = status
        indices = {int(self.item["item_index"])} | {
            int(m["item_index"])
            for m in self.group_members
            if m.get("review_status") not in {"applied", "kept_old"}
        }
        for index in sorted(indices):
            moddb_repair_review_store.save_review_state(
                self.store_path, self.run_id, index, status, value
            )

    def _move(self, delta: int) -> None:
        if self.applying or not self.item_indices:
            if not self.applying and not self.item_indices:
                show_snack(self.page, "目前篩選沒有可移動的審查項目。", C.DIA)
            return
        self._save_draft()
        target = self.position + delta
        if 0 <= target < len(self.item_indices):
            self.position = target
            self._render()
        elif (
            delta > 0
            and self.page_offset + len(self.item_indices) < self.filtered_count
        ):
            self.page_offset += self.page_size
            self.position = 0
            self._load_index_page()
            self._render()
        elif delta < 0 and self.page_offset > 0:
            self.page_offset = max(0, self.page_offset - self.page_size)
            self._load_index_page()
            self.position = len(self.item_indices) - 1
            self._render()
        else:
            message = (
                "已經是最後一筆，沒有下一筆待審查項目。"
                if delta > 0
                else "已經是第一筆，沒有更早的待審查項目。"
            )
            show_snack(self.page, message, C.DIA, duration=2600)

    def _change_filter(self, event) -> None:
        if self.applying:
            return
        self._save_draft()
        self.status_filter = event.control.value or "all"
        self.filtered_count = moddb_repair_review_store.index_count(
            self.store_path, self.run_id, self.status_filter, grouped=True
        )
        self.page_offset = 0
        self._load_index_page()
        self.position = 0
        self._render()

    def _keep_old(self, _event=None) -> None:
        if self.applying or self.item is None:
            return
        if self.item.get("review_status") in {"applied", "kept_old"}:
            show_snack(self.page, "這筆項目已經完成審查，無需再次處理。", C.DIA)
            return
        self._save_draft(status="kept_old")
        self.status.value = "已保留舊譯文；沒有寫入翻譯資料，也沒有新增歷史。"
        self.status.color = C.EM
        self._render()

    def _apply(self, _event=None) -> None:
        if self.applying:
            return
        self._save_draft()
        if self.item is None or self.draft is None:
            show_snack(self.page, "目前沒有可套用的審查項目。", C.DIA)
            return
        if self.item.get("review_status") in {"applied", "kept_old"}:
            show_snack(self.page, "這筆項目已經完成審查，無需再次套用。", C.DIA)
            return
        value = self.draft.value or ""
        if not value.strip():
            show_snack(self.page, "AI 草稿不能是空白，請先輸入翻譯內容。", C.GOLD)
            return
        issues = translation_issues(
            str(self.item.get("en_us") or ""),
            value,
            kind=str(self.item.get("kind") or ""),
            repair=not self.item.get("strict_newline"),
        )
        if issues:
            self._open_apply_confirmation(value, issues)
            return
        self._start_apply(value)

    def _open_apply_confirmation(self, value: str, issues: list[str]) -> None:
        self._pending_apply_value = value
        details = "\n".join(f"・{issue}" for issue in issues)
        self.apply_confirmation_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER, color=C.GOLD),
                    ft.Text("確認套用格式仍有差異"),
                ],
                spacing=8,
            ),
            content=ft.Column(
                [
                    ft.Text("這份草稿仍有格式檢查問題，但可以由人工確認後套用："),
                    ft.Container(
                        content=ft.Text(details, color=C.GOLD, selectable=True),
                        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                        bgcolor=C.GOLD_BG,
                        border=ft.Border.all(1, C.GOLD),
                        border_radius=design.RADIUS_CONTROL,
                    ),
                    ft.Text("請確認是否仍要寫入翻譯資料與歷史紀錄。", color=C.MUTED),
                ],
                tight=True,
                spacing=12,
            ),
            actions=[
                ft.TextButton(
                    "返回修改", on_click=lambda _e: self._dismiss_apply_confirmation()
                ),
                ft.FilledButton(
                    "仍要套用",
                    icon=ft.Icons.CHECK,
                    on_click=lambda _e: self._confirm_apply_with_issues(),
                ),
            ],
        )
        show_dialog = getattr(self.page, "show_dialog", None)
        if callable(show_dialog):
            show_dialog(self.apply_confirmation_dialog)
        else:
            self._pending_apply_value = None
            self.apply_confirmation_dialog = None
            show_snack(
                self.page,
                "草稿仍有格式差異，目前頁面無法開啟確認視窗。",
                C.GOLD,
            )

    def _dismiss_apply_confirmation(self) -> None:
        self._pending_apply_value = None
        self.apply_confirmation_dialog = None
        pop_dialog = getattr(self.page, "pop_dialog", None)
        if callable(pop_dialog):
            pop_dialog()

    def _confirm_apply_with_issues(self) -> None:
        value = self._pending_apply_value
        self._dismiss_apply_confirmation()
        if value is not None:
            self._start_apply(value)

    def _start_apply(self, value: str) -> None:
        self.status.value = "正在重新驗證來源與 revision…"
        self.applying = True
        self._set_controls_disabled(True)
        self.page.update()
        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):
            run_task(self._apply_async, dict(self.item), value)
        else:
            self.applying = False
            self._set_controls_disabled(False)
            self.status.value = "目前頁面無法啟動背景工作，草稿已保存。"
            self.page.update()

    async def _apply_async(self, item: dict, value: str) -> None:
        try:
            result = await asyncio.to_thread(
                apply_and_record_repair_review,
                item.get("database_identity", ""),
                self.store_path,
                self.run_id,
                item,
                value,
            )
        except Exception as exc:  # noqa: BLE001 - keep the draft and show the worker failure
            if not self.closed:
                self.applying = False
                self._set_controls_disabled(False)
                self.status.value = f"套用失敗，草稿已保留：{exc}"
                self.status.color = C.GOLD
                self.page.update()
            return
        if self.closed:
            return
        self.applying = False
        self._set_controls_disabled(False)
        status = "applied" if result == "applied" else "stale"
        self.status.value = (
            "已套用，原來源與歷史均已保留。"
            if status == "applied"
            else "資料已變動；草稿保留，請重新讀取並比較。"
        )
        self.status.color = C.EM if status == "applied" else C.GOLD
        self.filtered_count = moddb_repair_review_store.index_count(
            self.store_path, self.run_id, self.status_filter, grouped=True
        )
        self.page_offset = min(
            self.page_offset,
            max(0, ((self.filtered_count - 1) // self.page_size) * self.page_size),
        )
        self._load_index_page()
        if self.item_indices:
            self.position = min(self.position, len(self.item_indices) - 1)
        self._render()
        counts = moddb_repair_review_store.status_counts(
            self.store_path, self.run_id, grouped=True
        )
        pending = counts.get("pending", 0) + counts.get("invalid", 0)
        if status == "applied":
            message = (
                f"✅ 已套用，原來源與歷史均已保留。剩餘待確認 {pending} 筆。"
                if pending
                else "✅ 已套用最後一筆待確認內容，審查完成。"
            )
            show_snack(self.page, message, C.EM, duration=3200)
        else:
            show_snack(
                self.page,
                "資料已變動，這次沒有覆蓋原內容；草稿已保留。",
                C.GOLD,
                duration=3600,
            )

    def _close(self) -> None:
        if self.applying:
            return
        self._save_draft()
        self.closed = True
        self.page.pop_dialog()
        if self.on_close is not None:
            self.on_close(
                moddb_repair_review_store.status_counts(
                    self.store_path, self.run_id, grouped=True
                )
            )

    def _set_controls_disabled(self, disabled: bool) -> None:
        for button in self.action_buttons:
            button.disabled = disabled
        if self.status_filter_control is not None:
            self.status_filter_control.disabled = disabled
        if self.draft is not None:
            self.draft.read_only = disabled


def open_repair_reviewer(page, store_path: str, run_id: str, on_close=None) -> None:
    RepairReviewer(page, store_path, run_id, on_close=on_close).open()
