"""Smart Review 執行分頁的歷史規劃控制項與續跑入口。"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    preview_moddb_smart_review_plan_from_path,
)
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.smart_review_job import SmartReviewRequest


class SmartReviewRunPlanningMixin:
    """把歷史規劃 UI 從執行面板的範圍控制項中隔離。"""

    def _build_history_controls(self) -> None:
        """建立新批次的最近兩批重用與執行前規劃控制項。"""
        self.use_recent_history = ft.Switch(
            label="讀取最近 2 次智慧校對狀態",
            value=True,
            on_change=lambda _e: self._refresh_plan_preview(),
        )
        self.skip_valid_history = ft.Switch(
            label="跳過已套用／AI 不用修改（僅快照有效）",
            value=True,
            on_change=lambda _e: self._refresh_plan_preview(),
        )
        self.preview_plan_btn = kit.button(
            "重新整理預估",
            "secondary",
            size="sm",
            icon=ft.Icons.REFRESH,
            on_click=lambda _e: self._refresh_plan_preview(),
        )
        self.plan_preview_text = ft.Text(
            "尚未取得歷史規劃預估。", size=12, color=C.MUTED, selectable=True
        )

    def _scope_card(self) -> ft.Control:
        """建立含歷史規劃控制項的「校對範圍與模型」卡片。"""
        return kit.section_card(
            "校對範圍與模型",
            ft.Column(
                [
                    kit.hint_text(
                        "只讀取「目前生效且未審核」的 AI 譯文與本地已審核 context；"
                        "AI 結果先存成候選，不會自動覆蓋譯文。"
                    ),
                    ft.Row(
                        [
                            self.version_dd,
                            self.mod_dd,
                            self.kind_dd,
                            self.source_dd,
                            self.query_field,
                        ],
                        spacing=10,
                        wrap=True,
                    ),
                    ft.Row(
                        [self.model_dd, self.limit_field, self.batch_field],
                        spacing=10,
                        wrap=True,
                    ),
                    ft.Row(
                        [
                            self.use_recent_history,
                            self.skip_valid_history,
                            self.preview_plan_btn,
                        ],
                        spacing=10,
                        wrap=True,
                    ),
                    self.plan_preview_text,
                    self.estimate_text,
                ],
                spacing=10,
            ),
            icon=ft.Icons.TUNE,
            tone="em",
        )

    def _refresh_plan_preview(self) -> None:
        """以同一個 planner 讀取歷史 KPI；預覽不會呼叫 provider。"""
        database = self.ctx.database()
        version = self.version_dd.value
        if self._scope_loading or self.job.running or database is None or not version:
            return
        limit = self._read_int(self.limit_field, minimum=0)
        if limit is None:
            return
        path, priority = database
        mod_id, kind, source, query = self._scope_values()
        self.plan_preview_text.value = "正在以最近兩份歷史計算預估…"
        self.plan_preview_text.color = C.MUTED
        self.ctx.update()

        def work() -> dict[str, int]:
            return preview_moddb_smart_review_plan_from_path(
                path,
                priority,
                version=str(version),
                mod_id=mod_id,
                kind=kind,
                effective_source=source,
                review_status="unreviewed",
                query=query,
                limit=limit,
                use_recent_history=bool(self.use_recent_history.value),
                skip_valid_history=bool(self.skip_valid_history.value),
            )

        def done(result: dict[str, int], error: str | None) -> None:
            if error:
                self.plan_preview_text.value = f"無法取得預估：{error}"
                self.plan_preview_text.color = C.RED
            else:
                self.plan_preview_text.value = (
                    f"符合範圍 {result['selected_count']:,} 筆；歷史重用 "
                    f"{result['reused_count']:,}；本次實際提交 AI "
                    f"{result['actual_ai_count']:,}；待人工審查 "
                    f"{result['pending_review_count']:,}；讀取 "
                    f"{result['history_runs_consulted']} 份歷史。"
                )
                self.plan_preview_text.color = C.MUTED
            self.ctx.update()

        self.ctx.run_background(
            work,
            done,
            name="預估智慧校對歷史重用",
            still_valid=lambda: not self.job.running,
        )

    def resume_run(self, run_id: str) -> None:
        """從歷史頁以原 run_id 重試尚未完成項目。"""
        if self.job.running:
            show_snack(self.ctx.page, "智慧校對正在執行中。", C.GOLD)
            return
        try:
            request: SmartReviewRequest = self._request()
        except ValueError as exc:
            show_snack(self.ctx.page, str(exc), C.RED)
            return
        request.resume_run_id = str(run_id)
        request.use_recent_history = False
        request.skip_valid_history = False
        error = self.job.start(request, parent_callback=self.ctx.on_complete)
        if error:
            show_snack(self.ctx.page, error, C.RED)
            return
        self.log_view.clear()
        self.result_box.visible = False
        self._render_running()
        self._start_polling()


__all__ = ["SmartReviewRunPlanningMixin"]
