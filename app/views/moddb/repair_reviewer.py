"""Single-entry, bounded-control review of rejected AI repair drafts."""

from __future__ import annotations

import asyncio

import flet as ft

from app.services_impl import moddb_repair_review_store
from app.services_impl.moddb_retranslate_service import (
    apply_and_record_repair_review,
    repair_output_issues,
)
from app.ui.design import C
from app.ui.sync_text_field import SyncTextField
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
        self.draft: ft.TextField | None = None
        self.format_status = ft.Text("", size=12, color=C.MUTED)
        self.status = ft.Text("", size=13, weight=ft.FontWeight.BOLD, color=C.MUTED)
        self.applying = False
        self.apply_button = ft.TextButton("確認套用", on_click=self._apply)
        self.close_button = ft.TextButton("關閉", on_click=lambda _e: self._close())
        self.action_buttons = [
            ft.TextButton("上一筆", on_click=lambda _e: self._move(-1)),
            ft.TextButton("下一筆", on_click=lambda _e: self._move(1)),
            ft.TextButton("保留舊譯文", on_click=self._keep_old),
            self.apply_button,
            self.close_button,
        ]
        self.status_filter_control = None
        self.body = ft.Column([], height=500, scroll=ft.ScrollMode.AUTO)
        self.position_text = ft.Text("", size=12, color=C.MUTED)
        self.content_box = ft.Container(content=self.body, width=820, height=560)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("AI 修復結果・逐筆審查"),
            content=self.content_box,
            actions=self._actions(),
        )

    def open(self) -> None:
        self.filtered_count = moddb_repair_review_store.index_count(
            self.store_path, self.run_id, self.status_filter
        )
        if not self.filtered_count:
            self.status_filter = "all"
            self.filtered_count = moddb_repair_review_store.index_count(
                self.store_path, self.run_id, self.status_filter
            )
        self.page_offset = 0
        self._load_index_page()
        if not self.filtered_count:
            return
        page_width = getattr(self.page, "width", None)
        page_height = getattr(self.page, "height", None)
        width = min(820, max(320, float(page_width) - 32)) if page_width else 820
        height = min(560, max(360, float(page_height) - 80)) if page_height else 560
        self.content_box.width = width
        self.content_box.height = height
        self.body.height = height - 24
        self._render()
        self.page.show_dialog(self.dialog)

    def _load_index_page(self) -> None:
        self.item_indices = moddb_repair_review_store.indices(
            self.store_path,
            self.run_id,
            self.status_filter,
            limit=self.page_size,
            offset=self.page_offset,
        )

    def _actions(self):
        return self.action_buttons

    def _render(self) -> None:
        if not self.item_indices:
            counts = moddb_repair_review_store.status_counts(
                self.store_path, self.run_id
            )
            total = sum(counts.values())
            self.item = None
            self.draft = None
            self.position_text.value = (
                f"待確認 {format_count(counts.get('pending', 0) + counts.get('invalid', 0))} · "
                f"已套用 {format_count(counts.get('applied', 0))} · "
                f"保留舊譯文 {format_count(counts.get('kept_old', 0))} · "
                f"資料已變動 {format_count(counts.get('stale', 0))} / {total}"
            )
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
        self._show_review_status(self.item.get("review_status", "pending"))
        counts = moddb_repair_review_store.status_counts(self.store_path, self.run_id)
        total = sum(counts.values())
        pending = counts.get("pending", 0) + counts.get("invalid", 0)
        self.position_text.value = (
            f"第 {self.page_offset + self.position + 1} / {self.filtered_count} 筆 · "
            f"待確認 {format_count(pending)} · "
            f"已套用 {format_count(counts.get('applied', 0))} · "
            f"保留舊譯文 {format_count(counts.get('kept_old', 0))} · "
            f"資料已變動 {format_count(counts.get('stale', 0))} / {total}"
        )
        item = self.item
        self.draft = SyncTextField(
            label="AI 草稿（可編輯）",
            value=str(item.get("draft") or ""),
            multiline=True,
            min_lines=7,
            max_lines=10,
            height=240,
            on_change=self._draft_changed,
        )
        self._update_format_status(
            str(item.get("en_us") or ""), str(item.get("draft") or "")
        )
        identity = (
            f"{item.get('version', '')} · {item.get('mod_id', '')} · "
            f"{item.get('kind', '')} · 原來源 #{item.get('source_id', '')}"
        )
        status_filter = self._filter_control()
        self.body.controls = [
            self.position_text,
            status_filter,
            self.status,
            ft.Text(identity, size=12, color=C.MUTED, selectable=True),
            ft.Text(str(item.get("key") or ""), size=12.5, selectable=True),
            ft.Text("英文原文", size=11, color=C.MUTED),
            ft.Text(str(item.get("en_us") or ""), selectable=True),
            ft.Text("保留中的舊譯文", size=11, color=C.MUTED),
            ft.Text(str(item.get("old_translation") or ""), selectable=True),
            ft.Text("未寫入的 AI 結果", size=11, color=C.MUTED),
            ft.Text(str(item.get("ai_translation") or ""), selectable=True),
            self.draft,
            self.format_status,
        ]
        self.page.update()

    def _filter_control(self):
        status_filter = ft.Dropdown(
            label="審查篩選",
            value=self.status_filter,
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
        issues = repair_output_issues(source, draft)
        if issues:
            self.format_status.value = "格式檢查：" + "；".join(issues)
            self.format_status.color = C.GOLD
        else:
            self.format_status.value = "格式檢查通過。"
            self.format_status.color = C.EM

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
        moddb_repair_review_store.save_review_state(
            self.store_path,
            self.run_id,
            int(self.item["item_index"]),
            status,
            value,
        )

    def _move(self, delta: int) -> None:
        if self.applying or not self.item_indices:
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

    def _change_filter(self, event) -> None:
        if self.applying:
            return
        self._save_draft()
        self.status_filter = event.control.value or "all"
        self.filtered_count = moddb_repair_review_store.index_count(
            self.store_path, self.run_id, self.status_filter
        )
        self.page_offset = 0
        self._load_index_page()
        self.position = 0
        self._render()

    def _keep_old(self, _event=None) -> None:
        if self.applying or self.item is None:
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
            return
        value = self.draft.value or ""
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
        self._render()

    def _close(self) -> None:
        if self.applying:
            return
        self._save_draft()
        self.closed = True
        self.page.pop_dialog()
        if self.on_close is not None:
            self.on_close(
                moddb_repair_review_store.status_counts(self.store_path, self.run_id)
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
