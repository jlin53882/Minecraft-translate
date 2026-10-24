"""Smart Review 執行分頁的歷史規劃控制項與續跑入口。"""

from __future__ import annotations

from typing import Any

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

    def _invalidate_plan_preview(self) -> None:
        """使尚未回來的舊規劃結果失效。"""
        self._plan_preview_seq = getattr(self, "_plan_preview_seq", 0) + 1
        self._plan_preview_result: dict[str, int] | None = None

    def _query_changed(self, _e=None) -> None:
        """搜尋條件變更時先撤銷舊規劃，再 debounce 重新計算。"""
        self._invalidate_plan_preview()
        self._debounce.call(self.refresh_estimate)

    def _model_changed(self, _e=None) -> None:
        """模型變更時明確失效舊規劃並重新計算。"""
        self._invalidate_plan_preview()
        self._scope_changed()

    def _build_history_controls(self) -> None:
        """建立新批次的最近兩批重用與執行前規劃控制項。"""
        self._plan_preview_seq = 0
        self._plan_preview_result: dict[str, int] | None = None
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
        self._invalidate_plan_preview()
        preview_seq = self._plan_preview_seq
        database = self.ctx.database()
        version = self.version_dd.value
        if self._scope_loading or self.job.running or database is None or not version:
            return
        limit = self._read_int(self.limit_field, minimum=0)
        if limit is None:
            return
        path, priority = database
        mod_id, kind, source, query = self._scope_values()
        use_recent_history = bool(self.use_recent_history.value)
        skip_valid_history = bool(self.skip_valid_history.value)
        model = str(self.model_dd.value or "")
        self.plan_preview_text.value = "正在讀取候選與目前版本最近兩份歷史 JSON…"
        self.plan_preview_text.color = C.MUTED
        self.ctx.update()

        def still_valid() -> bool:
            return preview_seq == self._plan_preview_seq and not self.job.running

        def show_progress(message: str) -> None:
            def apply_progress() -> None:
                if not still_valid():
                    return
                self.plan_preview_text.value = message
                self.plan_preview_text.color = C.MUTED
                self.ctx.update()

            self.ctx.post(apply_progress)

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
                use_recent_history=use_recent_history,
                skip_valid_history=skip_valid_history,
                model=model,
                progress=show_progress,
            )

        def done(result: dict[str, int], error: str | None) -> None:
            if not still_valid():
                return
            if error:
                self._plan_preview_result = None
                self.plan_preview_text.value = f"無法取得預估：{error}"
                self.plan_preview_text.color = C.RED
            else:
                self._plan_preview_result = result
                self.plan_preview_text.value = (
                    f"預估範圍 {result['selected_count']:,} 筆；歷史重用 "
                    f"{result['reused_count']:,}；本次送 AI "
                    f"{result['actual_ai_count']:,}；待人工審查 "
                    f"{result['pending_review_count']:,}；讀取同版本歷史 "
                    f"{result['history_runs_consulted']} 份歷史。"
                )
                self.plan_preview_text.color = C.MUTED
                self._update_estimate_text(update=False)
            self.ctx.update()

        self.ctx.run_background(
            work,
            done,
            name="預估智慧校對歷史重用",
            still_valid=still_valid,
        )

    def resume_run(
        self,
        run_id: str,
        *,
        model: str | None = None,
        batch_size: int | None = None,
        resume_preview: dict[str, Any] | None = None,
    ) -> bool:
        """從歷史頁以原 run_id 重試尚未完成項目，回傳是否已啟動。"""
        if self.job.running:
            show_snack(self.ctx.page, "智慧校對正在執行中。", C.GOLD)
            return False
        if isinstance(resume_preview, dict):
            self._apply_resume_preview(resume_preview, model, batch_size)
        scope = (
            resume_preview.get("scope")
            if isinstance(resume_preview, dict)
            and isinstance(resume_preview.get("scope"), dict)
            else None
        )
        limit_override = (
            int(resume_preview.get("requested_limit") or 0)
            if isinstance(resume_preview, dict)
            else None
        )
        try:
            request: SmartReviewRequest = self._request(
                model_override=model,
                batch_override=batch_size,
                scope_override=scope,
                limit_override=limit_override,
            )
        except ValueError as exc:
            show_snack(self.ctx.page, str(exc), C.RED)
            return False
        request.resume_run_id = str(run_id)
        request.use_recent_history = False
        request.skip_valid_history = False
        if isinstance(resume_preview, dict):
            selected = int(resume_preview.get("selected_count") or 0)
            processed = int(resume_preview.get("processed_count") or 0)
            request.resume_selected_count = selected
            request.resume_processed_count = processed
            request.resume_pending_count = int(
                resume_preview.get("pending_count") or max(0, selected - processed)
            )
        error = self.job.start(request, parent_callback=self.ctx.on_complete)
        if error:
            show_snack(self.ctx.page, error, C.RED)
            return False
        self.log_view.clear()
        self.result_box.visible = False
        self._render_running()
        self._start_polling()
        return True

    def _apply_resume_preview(
        self,
        preview: dict[str, Any],
        model: str | None,
        batch_size: int | None,
    ) -> None:
        """將確認視窗選定的歷史 scope 與輸入值同步回執行頁。"""
        scope = preview.get("scope")
        scope = scope if isinstance(scope, dict) else {}
        version = str(preview.get("version") or scope.get("version") or "").strip()
        # 讓仍在執行的初始範圍查詢失效，避免晚到結果把歷史批次的選擇覆蓋回去。
        self._scope_seq += 1
        self._scope_loading = False

        def set_dropdown(control: ft.Dropdown, value: str, label: str) -> None:
            """補上已不在目前未審核選項中的歷史值，避免畫面留舊值。"""
            options = [(option.key, option.text) for option in control.options]
            if value not in {key for key, _text in options}:
                options.append((value, label))
                kit.set_dropdown_options(control, options)
            control.value = value

        if version:
            set_dropdown(self.version_dd, version, version)
        set_dropdown(
            self.mod_dd,
            str(scope.get("mod_id") or "__all__"),
            str(scope.get("mod_id") or "全部模組"),
        )
        set_dropdown(
            self.kind_dd,
            str(scope.get("kind") or "__all__"),
            str(scope.get("kind") or "全部類型"),
        )
        source = scope.get("effective_source")
        set_dropdown(
            self.source_dd,
            "__all__" if source is None else str(source),
            "全部來源" if source is None else str(source),
        )
        self.query_field.value = str(scope.get("query") or "")
        self.model_dd.value = str(
            model if model is not None else preview.get("model") or ""
        )
        self.limit_field.value = str(int(preview.get("requested_limit") or 0))
        self.batch_field.value = str(
            int(
                batch_size if batch_size is not None else preview.get("batch_size") or 0
            )
        )
        self.use_recent_history.value = False
        self.skip_valid_history.value = False
        self._invalidate_plan_preview()


__all__ = ["SmartReviewRunPlanningMixin"]
