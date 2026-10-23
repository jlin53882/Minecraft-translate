"""智慧校對視窗的「歷史批次」分頁：列出過去的校對批次並可重新開啟審查。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    list_moddb_smart_review_runs_from_path,
)
from app.ui import kit
from app.ui.design import C
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_run_status import run_status_label

_PAGE_SIZE = 20


class SmartReviewHistory:
    """列出歷史批次，點「開啟審查」即可繼續處理候選。"""

    def __init__(
        self,
        ctx: SmartReviewContext,
        *,
        on_open: Callable[[str], None],
        on_resume: Callable[[str], None] | None = None,
        active_run_id: Callable[[], str | None],
    ) -> None:
        """建立歷史清單；``active_run_id`` 回傳目前正在執行的批次 id。"""
        self.ctx = ctx
        self.on_open = on_open
        self.on_resume = on_resume
        self.active_run_id = active_run_id
        self._limit = _PAGE_SIZE
        self._seq = 0
        self.list_view = ft.Column([], spacing=8)
        self.status_text = ft.Text("", size=12, color=C.MUTED)
        self.refresh_btn = kit.button(
            "重新整理", "secondary", icon=ft.Icons.REFRESH, on_click=self.refresh
        )
        self.more_btn = kit.button(
            "載入更多", "secondary", size="sm", on_click=self._more
        )
        self.more_btn.visible = False
        self.run_id_field = kit.text_field(
            "run_id", hint="貼上 run_id 直接開啟", width=360, mono=True
        )
        advanced = ft.ExpansionTile(
            title=ft.Text("進階：以 run_id 開啟", size=12, color=C.MUTED),
            controls=[
                ft.Row(
                    [
                        self.run_id_field,
                        kit.button("開啟", "secondary", on_click=self._open_typed),
                    ],
                    spacing=8,
                    wrap=True,
                )
            ],
            expanded=False,
        )
        self.control = ft.Column(
            [
                ft.Row(
                    [self.refresh_btn, self.status_text],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.list_view,
                self.more_btn,
                advanced,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def refresh(self, _e=None) -> None:
        """在背景重新讀取最近的批次清單。"""
        database = self.ctx.database()
        if database is None:
            self.status_text.value = "目前沒有可用的 Mod 資料庫。"
            self.ctx.update()
            return
        path, priority = database
        limit = self._limit
        self._seq += 1
        seq = self._seq
        self.status_text.value = "正在讀取批次清單…"
        self.ctx.update()

        def work() -> list[dict[str, Any]]:
            """唯讀取得最近批次，多取一筆以判斷是否還有更多。"""
            return list_moddb_smart_review_runs_from_path(
                path, priority, limit=limit + 1
            )

        def done(runs, error: str | None) -> None:
            """顯示批次卡片或錯誤訊息。"""
            if error:
                self.status_text.value = f"批次清單載入失敗：{error}"
                self.ctx.update()
                return
            has_more = len(runs) > limit
            self._render(runs[:limit])
            self.more_btn.visible = has_more
            self.status_text.value = f"共顯示 {min(len(runs), limit)} 個批次（新到舊）"
            self.ctx.update()

        self.ctx.run_background(
            work,
            done,
            name="載入智慧校對批次清單",
            still_valid=lambda: seq == self._seq,
        )

    def _more(self, _e=None) -> None:
        """多載入一頁批次。"""
        self._limit += _PAGE_SIZE
        self.refresh()

    def _open_typed(self, _e=None) -> None:
        """開啟使用者貼上的 run_id。"""
        run_id = str(self.run_id_field.value or "").strip()
        if run_id:
            self.on_open(run_id)

    def _render(self, runs: list[dict[str, Any]]) -> None:
        """把批次資料轉成卡片；沒有批次時顯示空狀態。"""
        if not runs:
            self.list_view.controls = [
                kit.empty_state(
                    "還沒有校對批次",
                    "到「執行」分頁開始第一次智慧校對。",
                    icon=ft.Icons.HISTORY,
                )
            ]
            return
        active = self.active_run_id()
        self.list_view.controls = [self._card(run, active) for run in runs]

    def _card(self, run: dict[str, Any], active: str | None) -> ft.Control:
        """建立單一批次卡片：時間、模型、範圍、進度與開啟按鈕。"""
        run_id = str(run.get("id") or "")
        scope = run.get("scope") or {}
        label, tone = run_status_label(
            str(run.get("status") or ""), active=run_id == active
        )
        selected = int(run.get("selected_count") or 0)
        processed = int(run.get("processed_count") or 0)
        pending = max(0, selected - processed)
        scope_text = (
            f"{scope.get('version') or run.get('scope_version') or '-'} · "
            f"{scope.get('mod_id') or '全部模組'} / {scope.get('kind') or '全部類型'}"
        )
        stats = (
            f"處理 {processed:,} / {selected:,} · 有效 {int(run.get('valid_count') or 0):,}"
            f" · 無效 {int(run.get('invalid_count') or 0):,}"
            f" · 請求錯誤 {int(run.get('request_error_count') or 0):,}"
            + (f" · 未處理 {pending:,}" if pending else "")
        )
        return ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    kit.chip(label, tone, dot=True),
                                    ft.Text(
                                        str(run.get("started_at") or "-"),
                                        size=13,
                                        weight=ft.FontWeight.W_600,
                                    ),
                                    ft.Text(
                                        f"{run.get('provider')}/{run.get('model')}",
                                        size=12,
                                        color=C.MUTED,
                                    ),
                                ],
                                spacing=10,
                                wrap=True,
                            ),
                            ft.Text(
                                scope_text, size=12, color=C.MUTED, selectable=True
                            ),
                            ft.Text(stats, size=12, selectable=True),
                            ft.Text(run_id, size=11, color=C.DIM, selectable=True),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    self._card_actions(run, run_id, pending),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=11),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=10,
        )

    def _card_actions(
        self, run: dict[str, Any], run_id: str, pending: int
    ) -> ft.Control:
        """建立歷史卡片上的審查與安全續跑按鈕。"""
        actions = [
            kit.button(
                "開啟審查",
                "primary",
                size="sm",
                icon=ft.Icons.RATE_REVIEW_OUTLINED,
                on_click=lambda _e, rid=run_id: self.on_open(rid),
            )
        ]
        resumable = str(run.get("status") or "") in {
            "cancelled",
            "interrupted",
            "partial",
            "failed",
        }
        if self.on_resume is not None and resumable and pending:
            actions.append(
                kit.button(
                    "繼續未完成校對",
                    "secondary",
                    size="sm",
                    icon=ft.Icons.PLAY_CIRCLE_OUTLINE,
                    on_click=lambda _e, rid=run_id: self.on_resume(rid),
                )
            )
        return ft.Column(actions, spacing=6)
