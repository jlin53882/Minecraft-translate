"""Read-only inspection actions for translation and repair KPI results."""

from __future__ import annotations

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.repair_reviewer import open_repair_reviewer


def _repair_detail_card(item: dict, index: int) -> ft.Container:
    """Build one read-only comparison of the retained translation and rejected AI text."""
    identity = (
        f"{item.get('version', '')} · {item.get('mod_id', '')} · "
        f"{item.get('kind', '')} · 來源 #{item.get('source_id', '')}"
    )
    issues = "、".join(item.get("issues") or ())
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(f"{index}. {identity}", size=12, color=C.MUTED),
                ft.Text(str(item.get("key") or ""), size=12.5, selectable=True),
                ft.Text("特殊字元差異：" + issues, size=12, color=C.GOLD),
                ft.Text("英文原文", size=11, color=C.MUTED),
                ft.Text(str(item.get("en_us") or ""), selectable=True),
                ft.Text("保留的舊譯文", size=11, color=C.MUTED),
                ft.Text(str(item.get("old_translation") or ""), selectable=True),
                ft.Text("未寫入的 AI 結果", size=11, color=C.MUTED),
                ft.Text(str(item.get("ai_translation") or ""), selectable=True),
            ],
            spacing=3,
            tight=True,
        ),
        padding=10,
        border=ft.Border.all(1, C.LINE),
        border_radius=8,
    )


class TranslationResultInspector:
    """Own the KPI action and the read-only views for flagged translation results."""

    def __init__(
        self, page, on_view_flagged=None, on_repair_review_changed=None
    ) -> None:
        self._page = page
        self._on_view_flagged = on_view_flagged
        self._on_repair_review_changed = on_repair_review_changed
        self._normal_entries: dict[int, str] = {}
        self._normal_version = ""
        self._repair_entries: list[dict] = []
        self._repair_omitted = 0
        self._repair_store_path = ""
        self._repair_run_id = ""
        self._repair_reviewable_count = 0
        self._mode = "normal"
        self._repair_condition = "quality_mismatch"
        self.button = kit.button(
            "檢視",
            "secondary",
            size="sm",
            icon=ft.Icons.FILTER_ALT_OUTLINED,
            on_click=lambda _e: self.inspect(),
        )
        self._sync_button()

    def set_mode(self, mode: str, repair_condition: str) -> None:
        self._mode = mode
        self._repair_condition = repair_condition
        self._sync_button()

    @property
    def normal_entries(self) -> dict[int, str]:
        return self._normal_entries

    def set_normal_results(self, summary: dict, version: str) -> None:
        self._normal_entries = dict(summary.get("flagged_entries") or {})
        self._normal_version = version
        self._repair_entries = []
        self._repair_omitted = 0
        self._repair_store_path = ""
        self._repair_run_id = ""
        self._repair_reviewable_count = 0
        self._sync_button()

    def set_repair_results(self, summary: dict) -> None:
        self._repair_entries = list(summary.get("flagged_entries") or ())
        self._repair_omitted = int(summary.get("flagged_entries_omitted") or 0)
        self._repair_store_path = str(summary.get("review_store_path") or "")
        self._repair_run_id = str(summary.get("review_run_id") or "")
        self._repair_reviewable_count = int(summary.get("reviewable_results") or 0)
        self._sync_button()

    def clear(self) -> None:
        self._normal_entries = {}
        self._normal_version = ""
        self._repair_entries = []
        self._repair_omitted = 0
        self._repair_store_path = ""
        self._repair_run_id = ""
        self._repair_reviewable_count = 0
        self._sync_button()

    def _sync_button(self) -> None:
        self.button.tooltip = (
            "逐筆檢視、編輯與確認 AI 草稿；每筆會以原來源和 revision 重新驗證"
            if self._mode == "repair"
            else "跳到條目校對，只看這次特殊字元不一致、未寫入的條目"
        )
        self.button.visible = (
            self._mode == "normal" and bool(self._normal_entries)
        ) or (
            self._mode == "repair"
            and self._repair_condition == "quality_mismatch"
            and bool(self._repair_entries or self._repair_reviewable_count)
        )

    def inspect(self) -> None:
        if self._mode == "repair":
            self._show_repair_results()
        elif self._normal_entries and self._on_view_flagged and self._normal_version:
            self._on_view_flagged(
                list(self._normal_entries),
                dict(self._normal_entries),
                self._normal_version,
            )

    def _show_repair_results(self) -> None:
        if (
            self._repair_run_id
            and self._repair_store_path
            and self._repair_reviewable_count
        ):

            def on_close(counts: dict[str, int]) -> None:
                if self._on_repair_review_changed is not None:
                    unresolved = sum(counts.values()) - counts.get("applied", 0)
                    self._on_repair_review_changed(unresolved)

            open_repair_reviewer(
                self._page,
                self._repair_store_path,
                self._repair_run_id,
                on_close=on_close,
            )
            return
        if not self._repair_entries:
            return
        show_dialog = getattr(self._page, "show_dialog", None)
        if not callable(show_dialog):
            show_snack(self._page, "目前畫面無法顯示修復結果。", C.GOLD)
            return
        cards = [
            _repair_detail_card(item, index)
            for index, item in enumerate(self._repair_entries, start=1)
        ]
        note = (
            f"目前顯示前 {len(cards):,} 筆；另有 {self._repair_omitted:,} 筆未附詳細內容。"
            if self._repair_omitted
            else f"共 {len(cards):,} 筆。這些 AI 結果未寫入，原譯文已保留。"
        )
        show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("AI 結果未通過格式檢查"),
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(note, size=12, color=C.MUTED),
                            ft.ListView(controls=cards, spacing=8, height=500),
                        ],
                        tight=True,
                        spacing=8,
                    ),
                    width=820,
                    height=560,
                ),
                actions=[
                    ft.TextButton(
                        "關閉", on_click=lambda _e=None: self._page.pop_dialog()
                    )
                ],
            )
        )
