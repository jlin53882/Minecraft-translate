"""智慧校對視窗的「執行」分頁：範圍設定、進度、統計與即時 log。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from pathlib import Path

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    SMART_REVIEW_DEFAULT_SOURCE,
    count_moddb_smart_review_candidates_from_path,
    smart_review_model_options,
)
from app.tasks.operation_registry import OperationPresentation
from app.ui import kit
from app.ui.debounce import Debouncer
from app.ui.design import C
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.views._log.log_view import LogView
from app.views.moddb.formatting import format_count
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_job import (
    SmartReviewJob,
    SmartReviewJobState,
    SmartReviewRequest,
    format_duration,
)
from app.views.moddb.smart_review_run_planning import SmartReviewRunPlanningMixin
from app.views.moddb.smart_review_run_status import (
    run_status_color,
    run_status_label,
)
from translation_tool.utils.config_manager import get_smart_review_settings

_POLL_SECONDS = 0.4


class SmartReviewRunPanel(SmartReviewRunPlanningMixin):
    """設定範圍、啟動背景校對，並顯示進度、統計與逐批 log。"""

    def __init__(
        self,
        ctx: SmartReviewContext,
        job: SmartReviewJob,
        *,
        version: str | None,
        mod_id: str | None,
        kind: str | None,
        effective_source: int | None,
        query: str,
        on_open_review: Callable[[str], None],
        on_hide: Callable[[], None],
    ) -> None:
        """建立面板控制項並以呼叫端帶入的條目範圍作為初始值。"""
        self.ctx = ctx
        self.job = job
        self.on_open_review = on_open_review
        self.on_hide = on_hide
        self._total: int | None = None
        self._estimate_seq = 0
        self._scope_seq = 0
        self._scope_loading = False
        self._attached = False
        self._poller = PollerHandle()
        self._debounce = Debouncer(lambda: ctx.page, 0.6)
        self._scope_debounce = Debouncer(lambda: ctx.page, 0.15)
        self._last_sync_signature: tuple[object, ...] | None = None
        self._scope_modules: tuple[str, ...] = ()
        self._scope_sources_by_mod: dict[str | None, tuple[int, ...]] = {}
        self._source_catalog = getattr(ctx.get_db(), "source_catalog", None)
        self._build_scope_controls(version, mod_id, kind, effective_source, query)
        self._build_history_controls()
        self._build_run_controls()
        self.control = ft.Column(
            [
                self._scope_card(),
                self._run_card(),
                self._result_card(),
                self._log_card(),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        self._start_scope_options_load()

    # ──────────────── 範圍控制項 ────────────────

    def _build_scope_controls(
        self,
        version: str | None,
        mod_id: str | None,
        kind: str | None,
        effective_source: int | None,
        query: str,
    ) -> None:
        """建立版本、模組、類型、來源、搜尋、模型與批次輸入。"""
        smart_review_settings = get_smart_review_settings()
        self.version_dd = kit.dropdown(
            label="遊戲版本", dense=True, width=170, on_select=self._on_version
        )
        # 先用呼叫端的範圍建立可互動的畫面；完整清單由背景查詢補上，
        # 避免點擊「智慧校對」時同步讀取大型資料庫。
        kit.set_dropdown_options(
            self.version_dd, [(version, version)] if version else []
        )
        self.version_dd.value = version
        self.mod_dd = kit.dropdown(
            label="模組", dense=True, width=210, on_select=self._on_mod
        )
        self.kind_dd = kit.dropdown(
            label="類型", dense=True, width=150, on_select=self._on_kind
        )
        self.source_dd = kit.dropdown(
            label="目前生效來源", dense=True, width=180, on_select=self._scope_changed
        )
        initial_kind = kind or "__all__"
        kit.set_dropdown_options(
            self.kind_dd,
            [("__all__", "全部類型")]
            + ([(initial_kind, initial_kind)] if initial_kind != "__all__" else []),
        )
        self.kind_dd.value = initial_kind
        initial_mod = mod_id or "__all__"
        kit.set_dropdown_options(
            self.mod_dd,
            [("__all__", "全部模組")]
            + ([(initial_mod, initial_mod)] if initial_mod != "__all__" else []),
        )
        self.mod_dd.value = initial_mod
        initial_source = (
            "__all__" if effective_source is None else str(effective_source)
        )
        self._scope_sources_by_mod = {
            None: ((int(effective_source),) if effective_source is not None else ()),
        }
        if initial_mod != "__all__":
            self._scope_sources_by_mod[initial_mod] = self._scope_sources_by_mod[None]
        kit.set_dropdown_options(
            self.source_dd,
            [("__all__", "全部來源")]
            + (
                [(initial_source, initial_source)]
                if initial_source != "__all__"
                else []
            ),
        )
        self.source_dd.value = initial_source
        self.query_field = kit.text_field(
            "條目搜尋",
            value=query,
            hint="key、英文原文或目前譯文",
            width=240,
            on_change=self._query_changed,
            on_submit=lambda _e: self.refresh_estimate(),
        )
        self.model_dd = kit.dropdown(
            label="校對模型", dense=True, width=250, on_select=self._model_changed
        )
        options = smart_review_model_options()
        kit.set_dropdown_options(self.model_dd, options)
        self.model_dd.value = options[0][0] if options else None
        self.limit_field = kit.text_field(
            "本次處理上限（筆，0=不限）",
            value="1000",
            width=190,
            on_change=lambda _e: self._scope_changed(),
        )
        self.batch_field = kit.text_field(
            "每批（筆）",
            value=str(smart_review_settings["default_batch_size"]),
            width=110,
            on_change=lambda _e: self._update_estimate_text(),
        )
        self.estimate_text = ft.Text("", size=13, color=C.MUTED, selectable=True)

    def _scope_query(self, version: str | None, kind: str | None):
        """在背景工作中讀取版本、類型與未審核候選的範圍對照。"""
        db = self.ctx.get_db()
        if db is None:
            return [], [], (), {None: ()}
        versions = list(db.versions())
        kinds = list(db.kinds(version)) if version else []
        if not version:
            return versions, kinds, (), {None: ()}
        modules, sources_by_mod = db.smart_review_scope_options(
            version=str(version),
            kind=kind,
            review_status="unreviewed",
        )
        return versions, kinds, modules, sources_by_mod

    def _start_scope_options_load(self) -> None:
        """背景載入範圍選項，避免開啟或切換條件時阻塞 UI 執行緒。"""
        self._invalidate_plan_preview()
        self._scope_seq += 1
        seq = self._scope_seq
        version = self.version_dd.value
        kind = self.kind_dd.value
        kind_value = None if kind in (None, "", "__all__") else str(kind)
        self._scope_loading = True
        self._total = None
        self._estimate_seq += 1
        self.estimate_text.value = "正在載入可用的模組與來源…"
        self.estimate_text.color = C.MUTED
        if self._attached:
            self.ctx.update()

        def work():
            """執行範圍唯讀查詢，只回傳下拉選單需要的資料。"""
            return self._scope_query(version, kind_value)

        def done(result, error: str | None) -> None:
            """只套用仍屬於最新範圍條件的背景結果。"""
            if seq != self._scope_seq:
                return
            self._scope_loading = False
            if error:
                self.estimate_text.value = f"無法載入範圍選項：{error}"
                self.estimate_text.color = C.RED
                if self._attached:
                    self.ctx.update()
                return
            versions, kinds, modules, sources_by_mod = result
            self._apply_scope_options(versions, kinds, modules, sources_by_mod)
            if self._attached:
                self.refresh_estimate()
            else:
                self._update_estimate_text(update=False)

        self.ctx.run_background(
            work,
            done,
            name="載入智慧校對範圍選項",
            presentation=OperationPresentation.MAINTENANCE,
            still_valid=lambda: seq == self._scope_seq,
        )

    def _apply_scope_options(
        self,
        versions,
        kinds,
        modules,
        sources_by_mod,
    ) -> None:
        """把背景查詢的版本、類型、模組與來源對照套用到控制項。"""
        current_version = self.version_dd.value
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        self.version_dd.value = current_version if current_version in versions else None

        current_kind = self.kind_dd.value
        kind_options = [("__all__", "全部類型"), *((k, k) for k in kinds)]
        kit.set_dropdown_options(self.kind_dd, kind_options)
        kind_keys = {key for key, _label in kind_options}
        self.kind_dd.value = current_kind if current_kind in kind_keys else "__all__"

        self._scope_modules = tuple(modules)
        self._scope_sources_by_mod = {
            key: tuple(values) for key, values in sources_by_mod.items()
        }
        current_mod = self.mod_dd.value
        mod_options = [
            ("__all__", "全部模組"),
            *((mod, mod) for mod in self._scope_modules),
        ]
        kit.set_dropdown_options(self.mod_dd, mod_options)
        mod_keys = {key for key, _label in mod_options}
        self.mod_dd.value = current_mod if current_mod in mod_keys else "__all__"
        self._refresh_sources()

    def _refresh_sources(self) -> None:
        """依目前模組從已載入的對照表重建生效來源選單。"""
        selected_mod = self.mod_dd.value
        mod_key = None if selected_mod in (None, "", "__all__") else str(selected_mod)
        codes = self._scope_sources_by_mod.get(mod_key, ())
        catalog = self._source_catalog
        source_options = [("__all__", "全部來源")] + [
            (
                str(code),
                catalog.label(code) if catalog is not None else str(code),
            )
            for code in codes
        ]
        kit.set_dropdown_options(self.source_dd, source_options)
        known_sources = {key for key, _label in source_options}
        selected_source = self.source_dd.value
        if selected_source not in known_sources:
            selected_source = (
                str(SMART_REVIEW_DEFAULT_SOURCE)
                if str(SMART_REVIEW_DEFAULT_SOURCE) in known_sources
                else "__all__"
            )
        self.source_dd.value = selected_source

    def _scope_controls(self) -> list[ft.Control]:
        """回傳啟動後需要鎖定的設定控制項。"""
        return [
            self.version_dd,
            self.mod_dd,
            self.kind_dd,
            self.source_dd,
            self.query_field,
            self.model_dd,
            self.limit_field,
            self.batch_field,
        ]

    # ──────────────── 執行與進度控制項 ────────────────

    def _build_run_controls(self) -> None:
        """建立開始、停止、進度與統計控制項。"""
        self.start_btn = kit.button(
            "開始智慧校對",
            "primary",
            icon=ft.Icons.AUTO_AWESOME,
            on_click=self._start,
        )
        self.stop_btn = kit.button(
            "停止後續批次",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            on_click=self._stop,
        )
        self.hide_btn = kit.button(
            "關閉視窗，背景繼續",
            "secondary",
            icon=ft.Icons.OPEN_IN_NEW_OFF,
            on_click=lambda _e: self.on_hide(),
        )
        self.progress_bar = kit.progress_bar(0.0, "em", height=8)
        self.progress_text = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self.status_text = ft.Text(
            "尚未開始。", size=13, color=C.MUTED, selectable=True
        )
        self.valid_stat = kit.stat_card("有效", "0", icon=ft.Icons.CHECK, tone="em")
        self.invalid_stat = kit.stat_card(
            "無效", "0", icon=ft.Icons.REPORT_PROBLEM_OUTLINED, tone="gold"
        )
        self.error_stat = kit.stat_card(
            "請求錯誤", "0", icon=ft.Icons.ERROR_OUTLINE, tone="red"
        )
        self.time_stat = kit.stat_card(
            "已用時間", "--:--", icon=ft.Icons.TIMER_OUTLINED, tone="dia"
        )
        self.progress_area = ft.Column(
            [
                self.progress_bar,
                self.progress_text,
                ft.Row(
                    [
                        ft.Container(self.valid_stat, expand=True),
                        ft.Container(self.invalid_stat, expand=True),
                        ft.Container(self.error_stat, expand=True),
                        ft.Container(self.time_stat, expand=True),
                    ],
                    spacing=10,
                ),
                kit.hint_text(
                    "關閉視窗不會中斷校對；進度會顯示在畫面上方的任務膠囊，"
                    "完成後會有通知。"
                ),
            ],
            spacing=10,
            visible=False,
        )
        self.stop_btn.visible = False
        self.hide_btn.visible = False

    def _run_card(self) -> ft.Control:
        """建立「執行」卡片。"""
        return kit.section_card(
            "執行",
            ft.Column(
                [
                    ft.Row(
                        [self.start_btn, self.stop_btn, self.hide_btn],
                        spacing=8,
                        wrap=True,
                    ),
                    self.status_text,
                    self.progress_area,
                ],
                spacing=10,
            ),
            icon=ft.Icons.PLAY_CIRCLE_OUTLINE,
            tone="gold",
        )

    def _result_card(self) -> ft.Control:
        """建立執行結果摘要卡片，完成後才顯示。"""
        self.result_title = ft.Text("", size=15, weight=ft.FontWeight.BOLD)
        self.result_detail = ft.Text("", size=13, selectable=True)
        self.result_errors = ft.Text("", size=12, color=C.GOLD, selectable=True)
        self.review_btn = kit.button(
            "前往審查候選",
            "primary",
            icon=ft.Icons.RATE_REVIEW_OUTLINED,
            on_click=self._open_review,
        )
        self.result_box = kit.section_card(
            "校對結果",
            ft.Column(
                [
                    self.result_title,
                    self.result_detail,
                    self.result_errors,
                    self.review_btn,
                ],
                spacing=8,
            ),
            icon=ft.Icons.FLAG_OUTLINED,
            tone="em",
        )
        self.result_box.visible = False
        return self.result_box

    def _log_card(self) -> ft.Control:
        """建立即時 log 卡片，可只顯示警告與錯誤。"""
        self.log_view = LogView(self.ctx.page, height=230, expand=False)
        self.only_problems = ft.Switch(
            label="只看警告／錯誤", value=False, on_change=self._toggle_levels
        )
        return kit.section_card(
            "執行紀錄",
            self.log_view,
            icon=ft.Icons.TERMINAL,
            tone="dia",
            actions=[self.only_problems],
        )

    def _toggle_levels(self, _e=None) -> None:
        """切換 log 等級篩選並依 session 重建畫面。"""
        levels = (
            ["warning", "error"]
            if self.only_problems.value
            else ["system", "info", "warning", "error"]
        )
        self.log_view.set_show_levels(levels)
        self.log_view.clear()
        self._sync_log()

    # ──────────────── 範圍與估算 ────────────────

    def _on_version(self, _e=None) -> None:
        """版本變更時重建候選模組、類型與來源選項並重新估算。"""
        self.mod_dd.value = "__all__"
        self.kind_dd.value = "__all__"
        self.source_dd.value = "__all__"
        self._start_scope_options_load()

    def _on_mod(self, _e=None) -> None:
        """模組變更時直接使用已載入的來源對照，不重查整個資料庫。"""
        self._refresh_sources()
        self._scope_changed()

    def _on_kind(self, _e=None) -> None:
        """類型變更時重新過濾有候選的模組與來源。"""
        self.mod_dd.value = "__all__"
        self.source_dd.value = "__all__"
        self._start_scope_options_load()

    def _scope_changed(self, _e=None) -> None:
        """範圍變更後立即清除舊筆數，再以短暫 debounce 背景重新估算。"""
        self._invalidate_plan_preview()
        self._total = None
        self._estimate_seq += 1
        if self._scope_loading:
            self._update_estimate_text()
            return
        self._scope_debounce.call(self.refresh_estimate)

    def _scope_values(self) -> tuple[str | None, str | None, int | None, str]:
        """讀取模組、類型、來源與搜尋條件，並把「全部」轉成 None。"""
        mod_id = self.mod_dd.value
        kind = self.kind_dd.value
        source = self.source_dd.value
        return (
            None if mod_id in (None, "", "__all__") else str(mod_id),
            None if kind in (None, "", "__all__") else str(kind),
            None if source in (None, "", "__all__") else int(source),
            str(self.query_field.value or "").strip(),
        )

    def refresh_estimate(self) -> None:
        """在背景以唯讀查詢計算符合條件的候選筆數。"""
        database = self.ctx.database()
        version = self.version_dd.value
        if self._scope_loading or self.job.running or database is None or not version:
            self._update_estimate_text()
            return
        path, priority = database
        mod_id, kind, source, query = self._scope_values()
        self._estimate_seq += 1
        seq = self._estimate_seq
        self.estimate_text.value = "正在計算符合條件的候選筆數…"
        self.ctx.update()

        def work() -> int:
            """唯讀計算符合範圍的未審核候選數。"""
            return count_moddb_smart_review_candidates_from_path(
                path,
                priority,
                version=str(version),
                mod_id=mod_id,
                kind=kind,
                effective_source=source,
                review_status="unreviewed",
                query=query,
            )

        def done(total, error: str | None) -> None:
            """顯示筆數，或在查詢失敗時說明原因。"""
            if error:
                self._total = None
                self.estimate_text.value = f"無法計算候選筆數：{error}"
            else:
                self._total = int(total or 0)
                self._update_estimate_text(update=False)
            self.ctx.update()

        self.ctx.run_background(
            work,
            done,
            name="計算智慧校對候選筆數",
            still_valid=lambda: seq == self._estimate_seq,
        )
        self._refresh_plan_preview()

    def _read_int(self, control: ft.TextField, minimum: int = 1) -> int | None:
        """讀取整數輸入；格式錯誤或小於下限時回傳 None。"""
        try:
            value = int(str(control.value or "").strip())
        except (TypeError, ValueError):
            return None
        return value if value >= minimum else None

    def _update_estimate_text(self, *, update: bool = True) -> None:
        """依目前筆數、上限與每批大小更新估算文字，不查詢資料庫。"""
        smart_review_settings = get_smart_review_settings()
        max_batch_size = smart_review_settings["max_batch_size"]
        limit = self._read_int(self.limit_field, minimum=0)
        batch = self._read_int(self.batch_field)
        if self._scope_loading:
            self.estimate_text.value = "正在載入可用的模組與來源…"
            self.estimate_text.color = C.MUTED
        elif limit is None or batch is None:
            self.estimate_text.value = (
                "上限必須是 0 或正整數；每批必須是大於 0 的整數。"
            )
            self.estimate_text.color = C.RED
        elif self._total is None:
            self.estimate_text.value = "正在等待候選筆數…"
            self.estimate_text.color = C.MUTED
        elif batch > max_batch_size:
            self.estimate_text.value = (
                f"符合範圍的未審核 AI 譯文共 {format_count(self._total)} 筆；"
                f"每批大小不可超過 {max_batch_size} 筆。"
            )
            self.estimate_text.color = C.RED
        else:
            plan = getattr(self, "_plan_preview_result", None)
            if isinstance(plan, dict):
                planned = int(plan.get("actual_ai_count") or 0)
                selected = int(plan.get("selected_count") or 0)
                scope_label = (
                    f"符合範圍 {format_count(selected)} 筆；"
                    f"歷史重用後實際送 AI {format_count(planned)} 筆"
                )
            else:
                planned = self._total if limit == 0 else min(limit, self._total)
                scope_label = (
                    f"符合範圍的未審核 AI 譯文共 {format_count(self._total)} 筆；"
                    f"目前尚未完成歷史重用預估"
                )
            batches = math.ceil(planned / batch) if planned else 0
            limit_label = "不限" if limit == 0 else format_count(limit)
            self.estimate_text.value = (
                f"{scope_label}；本次上限 {limit_label}，約 {batches} 批"
                f"（每批 {batch} 筆）。"
            )
            self.estimate_text.color = C.MUTED
        if update:
            self.ctx.update()

    # ──────────────── 啟動、停止與狀態 ────────────────

    def _request(
        self,
        *,
        model_override: str | None = None,
        batch_override: int | None = None,
    ) -> SmartReviewRequest:
        """依畫面建立校對請求；條件不完整時丟出給使用者看的錯誤。"""
        database = self.ctx.database()
        if database is None:
            raise ValueError("目前沒有可用的 Mod 資料庫")
        version = self.version_dd.value
        model = self.model_dd.value if model_override is None else model_override
        if not version:
            raise ValueError("請先選擇遊戲版本")
        if not model:
            raise ValueError("請先選擇校對模型（設定頁需啟用至少一個模型）")
        limit = self._read_int(self.limit_field, minimum=0)
        batch = (
            self._read_int(self.batch_field)
            if batch_override is None
            else int(batch_override)
        )
        if limit is None:
            raise ValueError("上限必須是 0 或正整數")
        if batch is None:
            raise ValueError("每批必須是大於 0 的整數")
        max_batch_size = get_smart_review_settings()["max_batch_size"]
        if batch > max_batch_size:
            raise ValueError(f"每批必須介於 1 與 {max_batch_size} 筆")
        mod_id, kind, source, query = self._scope_values()
        path, priority = database
        return SmartReviewRequest(
            path=path,
            priority=priority,
            model=str(model),
            version=str(version),
            mod_id=mod_id,
            kind=kind,
            effective_source=source,
            query=query,
            limit=limit,
            batch_size=batch,
            use_recent_history=bool(self.use_recent_history.value),
            skip_valid_history=bool(self.skip_valid_history.value),
        )

    def _start(self, _e=None) -> None:
        """啟動背景校對並開始輪詢進度。"""
        if self.job.running:
            show_snack(self.ctx.page, "智慧校對正在執行中。", C.GOLD)
            return
        try:
            request = self._request()
        except ValueError as exc:
            self.status_text.value = str(exc)
            self.status_text.color = C.RED
            self.ctx.update()
            return
        error = self.job.start(request, parent_callback=self.ctx.on_complete)
        if error:
            self.status_text.value = error
            self.status_text.color = C.RED
            self.ctx.update()
            return
        self.log_view.clear()
        self.result_box.visible = False
        self._last_sync_signature = None
        self._render_running()
        self._start_polling()

    def _stop(self, _e=None) -> None:
        """要求停止後續批次。"""
        self.job.cancel()
        self.stop_btn.disabled = True
        self.status_text.value = "已要求停止；目前 API 回應返回後不再送下一批。"
        self.status_text.color = C.GOLD
        self.ctx.update()

    def attach(self) -> None:
        """視窗開啟時接續任務狀態：執行中就恢復進度，已結束就顯示結果。"""
        self._attached = True
        self.job.add_finish_listener(self._on_finished)
        state = self.job.state
        if state.phase == "running":
            self._render_running()
            self._sync_log()
            self._start_polling()
        elif state.phase in {"done", "failed"}:
            self._sync_log()
            self._render_finished(state)
            if not self._scope_loading:
                self.refresh_estimate()
            else:
                self._update_estimate_text()
        else:
            if not self._scope_loading:
                self.refresh_estimate()
            else:
                self._update_estimate_text()

    def detach(self) -> None:
        """視窗關閉時停止輪詢並取消登記；背景任務本身不受影響。"""
        self._attached = False
        self._scope_debounce.cancel()
        self._debounce.cancel()
        self._scope_seq += 1
        self._estimate_seq += 1
        self._poller.stop()
        self.job.remove_finish_listener(self._on_finished)

    def _start_polling(self) -> None:
        """啟動輪詢；頁面沒有事件迴圈時略過（測試或離線環境）。"""
        self._poller.start(self.ctx.page, self._poll)

    async def _poll(self, alive: Callable[[], bool]) -> None:
        """定期把任務進度與 log 同步到畫面，直到任務結束。"""
        while alive() and self.job.running:
            self.sync()
            await asyncio.sleep(_POLL_SECONDS)

    def sync(self) -> None:
        """把目前任務狀態與 log 套用到畫面。"""
        state = self.job.state
        new_entries = self._sync_log(update=False)
        self._render_progress(state)
        signature = self._progress_signature(state)
        if new_entries or signature != self._last_sync_signature:
            self._last_sync_signature = signature
            self.ctx.update()

    def _sync_log(self, *, update: bool = True) -> None:
        """從任務的 TaskSession 同步新增的 log。"""
        session = self.job.session
        if session is None:
            return []
        entries = session.snapshot()["logs"]
        return self.log_view.sync_entries(entries, update=update)

    @staticmethod
    def _progress_signature(state: SmartReviewJobState) -> tuple[object, ...]:
        """建立目前畫面可見值的簽章，避免沒有變更時重送整頁 UI。"""
        return (
            state.phase,
            state.status,
            state.run_id,
            state.selected,
            state.processed,
            state.valid,
            state.invalid,
            state.request_errors,
            state.cancel_requested,
            format_duration(state.elapsed()),
            format_duration(state.eta_seconds()),
        )

    def _render_running(self) -> None:
        """切換到執行中的畫面：鎖定設定、顯示進度與停止按鈕。"""
        for control in self._scope_controls():
            control.disabled = True
        self.start_btn.disabled = True
        self.stop_btn.visible = True
        self.stop_btn.disabled = self.job.state.cancel_requested
        self.hide_btn.visible = True
        self.progress_area.visible = True
        self.result_box.visible = False
        self.status_text.color = C.MUTED
        self._render_progress(self.job.state)
        self._last_sync_signature = self._progress_signature(self.job.state)
        self.ctx.update()

    def _render_progress(self, state: SmartReviewJobState) -> None:
        """更新進度條、百分比、統計與預估剩餘時間。"""
        self.progress_bar.value = state.fraction if state.selected else None
        eta = format_duration(state.eta_seconds())
        self.progress_text.value = (
            f"已處理 {state.processed:,} / {state.selected:,} 筆"
            f"（{state.fraction:.0%}）· 預估剩餘 {eta}"
            if state.selected
            else "正在查詢並準備候選…"
        )
        self.valid_stat.set_value(f"{state.valid:,}")
        self.invalid_stat.set_value(f"{state.invalid:,}")
        self.error_stat.set_value(f"{state.request_errors:,}")
        self.time_stat.set_value(format_duration(state.elapsed()))
        request = state.request
        self.status_text.value = (
            f"校對中：{request.scope_label()} · 模型 {request.model}"
            if request is not None
            else "校對中…"
        )

    def _on_finished(self, state: SmartReviewJobState) -> None:
        """任務結束時停止輪詢並顯示最終結果。"""
        self._poller.stop()
        self._sync_log()
        self._render_finished(state)

    def _render_finished(self, state: SmartReviewJobState) -> None:
        """顯示結束後的摘要、無效原因與「前往審查」入口。"""
        for control in self._scope_controls():
            control.disabled = False
        self.start_btn.disabled = False
        self.stop_btn.visible = False
        self.hide_btn.visible = False
        self.progress_area.visible = True
        self._render_progress(state)
        self.progress_bar.value = state.fraction
        self.time_stat.set_value(format_duration(state.elapsed()))
        if state.phase == "failed":
            self.status_text.value = f"智慧校對失敗：{state.error}"
            self.status_text.color = C.RED
            self.result_box.visible = False
            self.ctx.update()
            return
        label, _tone = run_status_label(state.status)
        result = state.result
        self.status_text.value = f"智慧校對{label}。"
        self.status_text.color = run_status_color(state.status)
        self.result_title.value = f"{label}：已處理 {state.processed:,} 筆"
        if result is not None:
            self.result_detail.value = (
                f"可審查的有效候選 {result.valid_count:,} 筆"
                f"（建議修改 {result.suggestion_count:,}、"
                f"維持不變 {result.no_change_count:,}、"
                f"需補 context {result.needs_context_count:,}）；"
                f"無效 {result.invalid_count:,}、請求錯誤 {result.request_error_count:,}。"
            )
            if getattr(result, "stop_reason", None):
                self.result_detail.value += (
                    f" 執行停止原因：{result.stop_reason}（未送出的候選保留待恢復）。"
                )
        errors = state.breakdown.get("errors") if state.breakdown else None
        self.result_errors.value = (
            "無效原因（前幾名）：\n"
            + "\n".join(f"・{message} × {count:,}" for message, count in errors)
            if errors
            else ""
        )
        self.result_errors.visible = bool(errors)
        self.review_btn.disabled = not state.run_id
        self.result_box.visible = True
        self.ctx.update()

    def _open_review(self, _e=None) -> None:
        """切到審查分頁並載入這次的批次。"""
        run_id = self.job.state.run_id
        if run_id:
            self.on_open_review(run_id)

    def database_path(self) -> Path | None:
        """回傳目前資料庫路徑，供其他分頁判斷是否仍為同一資料庫。"""
        database = self.ctx.database()
        return database[0] if database else None
