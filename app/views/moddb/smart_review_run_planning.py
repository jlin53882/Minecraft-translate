"""Smart Review 執行分頁的歷史規劃控制項與續跑入口。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    preview_moddb_smart_review_plan_from_path,
)
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.smart_review_job import SmartReviewRequest
from translation_tool.utils.config_manager import get_smart_review_settings


@dataclass(frozen=True)
class SmartReviewPlanPreviewRequest:
    """執行頁送給規劃控制器的完整、不可變預估輸入。"""

    path: Path
    priority: tuple[int, ...]
    version: str
    mod_id: str | None
    kind: str | None
    effective_source: int | None
    query: str
    limit: int
    batch_size: int
    use_recent_history: bool
    skip_valid_history: bool
    model: str | None


@dataclass(frozen=True)
class ResumeSelection:
    """續跑確認視窗解析後的不可變輸入。"""

    run_id: str
    version: str
    mod_id: str | None
    kind: str | None
    effective_source: int | None
    query: str
    requested_limit: int
    model: str
    batch_size: int
    selected_count: int
    processed_count: int
    pending_count: int


def _optional_scope_value(value: Any) -> str | None:
    """把歷史 scope 的空值與 UI 的「全部」正規化成 None。"""
    if value in (None, "", "__all__"):
        return None
    return str(value).strip() or None


def _required_nonnegative_int(preview: Mapping[str, Any], key: str) -> int:
    """讀取續跑預覽中的非負整數欄位。"""
    value = preview.get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"續跑狀態缺少有效的 {key}。") from exc
    if parsed < 0:
        raise ValueError(f"續跑狀態的 {key} 不可小於 0。")
    return parsed


def _parse_resume_scope(
    run_id: str, preview: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any], str]:
    """驗證續跑識別與 scope，回傳純化後的共同欄位。"""
    requested_run_id = str(run_id or "").strip()
    if not requested_run_id:
        raise ValueError("續跑批次識別碼不可為空白。")
    if not isinstance(preview, Mapping):
        raise TypeError("續跑狀態格式無效。")
    preview_run_id = str(preview.get("run_id") or "").strip()
    if not preview_run_id:
        raise ValueError("續跑狀態缺少批次識別碼。")
    if preview_run_id != requested_run_id:
        raise ValueError("續跑狀態已過期，批次識別碼不一致。")

    scope = preview.get("scope")
    if not isinstance(scope, Mapping):
        raise TypeError("續跑狀態缺少有效的校對範圍。")
    scope_version = str(scope.get("version") or "").strip()
    version = str(preview.get("version") or scope_version).strip()
    if not version:
        raise ValueError("續跑狀態缺少遊戲版本。")
    if scope_version and scope_version != version:
        raise ValueError("續跑狀態的遊戲版本不一致。")
    return requested_run_id, scope, version


def _parse_resume_source(scope: Mapping[str, Any]) -> int | None:
    """驗證歷史 scope 的生效來源，保留合法的 source 0。"""
    try:
        source_value = scope.get("effective_source")
        effective_source = (
            None if source_value in (None, "", "__all__") else int(source_value)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("續跑狀態的生效來源無效。") from exc
    if effective_source is not None and effective_source < 0:
        raise ValueError("續跑狀態的生效來源不可小於 0。")
    return effective_source


def _parse_resume_counts(preview: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """讀取並驗證續跑數量欄位。"""
    requested_limit = _required_nonnegative_int(preview, "requested_limit")
    batch_size = _required_nonnegative_int(preview, "batch_size")
    if batch_size < 1:
        raise ValueError("續跑狀態的每批大小必須大於 0。")
    selected_count = _required_nonnegative_int(preview, "selected_count")
    processed_count = _required_nonnegative_int(preview, "processed_count")
    pending_count = _required_nonnegative_int(preview, "pending_count")
    if processed_count > selected_count or pending_count > selected_count:
        raise ValueError("續跑狀態的處理數量超過原批次範圍。")
    return (
        requested_limit,
        batch_size,
        selected_count,
        processed_count,
        pending_count,
    )


def parse_resume_selection(run_id: str, preview: Mapping[str, Any]) -> ResumeSelection:
    """純解析並驗證續跑預覽，不修改任何 UI 或資料庫狀態。"""
    requested_run_id, scope, version = _parse_resume_scope(run_id, preview)

    model = str(preview.get("model") or "").strip()
    if not model:
        raise ValueError("續跑狀態缺少校對模型。")
    effective_source = _parse_resume_source(scope)
    (
        requested_limit,
        batch_size,
        selected_count,
        processed_count,
        pending_count,
    ) = _parse_resume_counts(preview)

    return ResumeSelection(
        run_id=requested_run_id,
        version=version,
        mod_id=_optional_scope_value(scope.get("mod_id")),
        kind=_optional_scope_value(scope.get("kind")),
        effective_source=effective_source,
        query=str(scope.get("query") or "").strip(),
        requested_limit=requested_limit,
        model=model,
        batch_size=batch_size,
        selected_count=selected_count,
        processed_count=processed_count,
        pending_count=pending_count,
    )


def build_smart_review_request(
    *,
    path: Path,
    priority: tuple[int, ...],
    model: Any,
    version: Any,
    mod_id: Any,
    kind: Any,
    effective_source: Any,
    query: Any,
    limit: Any,
    batch_size: Any,
    use_recent_history: bool,
    skip_valid_history: bool,
    max_batch_size: int,
) -> SmartReviewRequest:
    """以單一規則建立新批次與續跑共用的 request。"""
    normalized_version = str(version or "").strip()
    if not normalized_version:
        raise ValueError("請先選擇遊戲版本")
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise ValueError("請先選擇校對模型（設定頁需啟用至少一個模型）")
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("上限必須是 0 或正整數") from exc
    if normalized_limit < 0:
        raise ValueError("上限必須是 0 或正整數")
    try:
        normalized_batch = int(batch_size)
    except (TypeError, ValueError) as exc:
        raise ValueError("每批必須是大於 0 的整數") from exc
    if normalized_batch < 1:
        raise ValueError("每批必須是大於 0 的整數")
    if normalized_batch > int(max_batch_size):
        raise ValueError(f"每批必須介於 1 與 {int(max_batch_size)} 筆")
    try:
        normalized_source = (
            None if effective_source in (None, "", "__all__") else int(effective_source)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("目前生效來源無效") from exc
    if normalized_source is not None and normalized_source < 0:
        raise ValueError("目前生效來源不可小於 0")

    return SmartReviewRequest(
        path=Path(path),
        priority=tuple(priority),
        model=normalized_model,
        version=normalized_version,
        mod_id=_optional_scope_value(mod_id),
        kind=_optional_scope_value(kind),
        effective_source=normalized_source,
        query=str(query or "").strip(),
        limit=normalized_limit,
        batch_size=normalized_batch,
        use_recent_history=bool(use_recent_history),
        skip_valid_history=bool(skip_valid_history),
    )


def build_resume_request(
    selection: ResumeSelection,
    *,
    path: Path,
    priority: tuple[int, ...],
    model_override: Any = None,
    batch_size_override: Any = None,
    max_batch_size: int,
) -> SmartReviewRequest:
    """純建立續跑 request；不改寫 selection、控制項或 job 狀態。"""
    request = build_smart_review_request(
        path=path,
        priority=priority,
        model=selection.model if model_override is None else model_override,
        version=selection.version,
        mod_id=selection.mod_id,
        kind=selection.kind,
        effective_source=selection.effective_source,
        query=selection.query,
        limit=selection.requested_limit,
        batch_size=(
            selection.batch_size if batch_size_override is None else batch_size_override
        ),
        use_recent_history=False,
        skip_valid_history=False,
        max_batch_size=max_batch_size,
    )
    return replace(
        request,
        resume_run_id=selection.run_id,
        resume_selected_count=selection.selected_count,
        resume_processed_count=selection.processed_count,
        resume_pending_count=selection.pending_count,
    )


class SmartReviewPlanningController:
    """協調智慧校對預估，不直接依賴 Flet 控制項或執行面板狀態。

    面板只負責把控制項值組成 :class:`SmartReviewPlanPreviewRequest`，並透過
    callback 投影結果；控制器本身只管理 request ticket、背景工作與 service
    呼叫，避免舊預估在新範圍套用後覆蓋畫面。
    """

    _REQUEST_DOMAIN = "smart_review_plan_preview"

    def __init__(self, ctx) -> None:
        """保存共用 UI context；不保存面板控制項或 job 狀態。"""
        self.ctx = ctx

    def invalidate(self) -> None:
        """讓目前預估失效，下一次 refresh 會取得新的 request ticket。"""
        self.ctx.invalidate_request(self._REQUEST_DOMAIN)

    def refresh(
        self,
        request: SmartReviewPlanPreviewRequest,
        *,
        still_valid,
        on_progress,
        on_done,
    ) -> bool:
        """以明確 request 執行背景預估，完成後透過 callback 回傳結果。"""
        ticket = self.ctx.begin_request(self._REQUEST_DOMAIN)

        def current() -> bool:
            return self.ctx.is_current(ticket) and still_valid()

        def report_progress(message: str) -> None:
            if not current():
                return

            def deliver() -> None:
                if current():
                    on_progress(message)

            self.ctx.post(deliver)

        def work() -> dict[str, Any]:
            return preview_moddb_smart_review_plan_from_path(
                request.path,
                request.priority,
                version=request.version,
                mod_id=request.mod_id,
                kind=request.kind,
                effective_source=request.effective_source,
                review_status="unreviewed",
                query=request.query,
                limit=request.limit,
                batch_size=request.batch_size,
                use_recent_history=request.use_recent_history,
                skip_valid_history=request.skip_valid_history,
                model=request.model,
                progress=report_progress,
            )

        def done(result: dict[str, Any] | None, error: str | None) -> None:
            if current():
                on_done(result, error)

        return self.ctx.run_background(
            work,
            done,
            name="預估智慧校對歷史重用",
            still_valid=current,
        )


class SmartReviewRunPlanningMixin:
    """保留 Flet 控制項的 view adapter；背景規劃交給明確 controller。"""

    _SCOPE_OPTIONS_REQUEST_DOMAIN = "smart_review_scope_options"
    _CANDIDATE_COUNT_REQUEST_DOMAIN = "smart_review_candidate_count"

    def _invalidate_scope_queries(
        self, *, candidate_scope_changed: bool, reason: str
    ) -> None:
        """依範圍變更契約使候選查詢與預估結果失效。

        模型、上限與歷史開關只會使 plan preview 失效；版本、模組、類型、來源
        與搜尋會同時使候選計數失效。每一種 callback 都透過這個入口，避免只
        失效其中一條背景查詢而留下互相矛盾的數字。
        """
        self._planning_controller.invalidate()
        self._plan_preview_result: dict[str, Any] | None = None
        if candidate_scope_changed:
            self.ctx.invalidate_request(self._SCOPE_OPTIONS_REQUEST_DOMAIN)
            self.ctx.invalidate_request(self._CANDIDATE_COUNT_REQUEST_DOMAIN)
            self._total = None
        self._debounce.cancel()
        if hasattr(self, "plan_preview_text"):
            self.plan_preview_text.value = f"條件已變更（{reason}），等待重新計算…"
            self.plan_preview_text.color = C.MUTED
        if hasattr(self, "plan_progress_bar"):
            self.plan_progress_bar.visible = False
        if hasattr(self, "estimate_text"):
            self._update_estimate_text(update=False)

    def _invalidate_plan_preview(self) -> None:
        """保留舊呼叫端名稱，僅使 plan preview 失效。"""
        self._invalidate_scope_queries(
            candidate_scope_changed=False,
            reason="校對規劃",
        )

    def _query_changed(self, _e=None) -> None:
        """搜尋條件變更時先撤銷舊規劃，再 debounce 重新計算。"""
        self._invalidate_scope_queries(
            candidate_scope_changed=True,
            reason="搜尋條件",
        )
        self._debounce.call(self.refresh_estimate)

    def _model_changed(self, _e=None) -> None:
        """模型變更時明確失效舊規劃並重新計算。"""
        self._invalidate_scope_queries(
            candidate_scope_changed=False,
            reason="校對模型",
        )
        self._debounce.call(self._refresh_plan_preview)

    def _limit_changed(self, _e=None) -> None:
        """處理上限只影響 plan preview，不重查候選總數。"""
        self._invalidate_scope_queries(
            candidate_scope_changed=False,
            reason="本次處理上限",
        )
        self._debounce.call(self._refresh_plan_preview)

    def _build_history_controls(self) -> None:
        """建立新批次的最近兩批重用與執行前規劃控制項。"""
        self._plan_preview_result: dict[str, Any] | None = None
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
        self.plan_progress_bar = kit.progress_bar(None, "em", height=6)
        self.plan_progress_bar.visible = False

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
                    self.plan_progress_bar,
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
        self._invalidate_scope_queries(
            candidate_scope_changed=False,
            reason="校對規劃",
        )
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
        self.plan_progress_bar.value = None
        self.plan_progress_bar.visible = True
        self.ctx.update()

        def still_valid() -> bool:
            return (
                not self.ctx.closed and not self.job.running and not self._scope_loading
            )

        batch = self._read_int(self.batch_field)
        if batch is None:
            self._update_estimate_text()
            return
        request = SmartReviewPlanPreviewRequest(
            path=path,
            priority=priority,
            version=str(version),
            mod_id=mod_id,
            kind=kind,
            effective_source=source,
            query=query,
            limit=limit,
            batch_size=batch,
            use_recent_history=use_recent_history,
            skip_valid_history=skip_valid_history,
            model=model,
        )

        def show_progress(message: str) -> None:
            self.plan_progress_bar.visible = True
            self.plan_progress_bar.value = None
            self.plan_preview_text.value = message
            self.plan_preview_text.color = C.MUTED
            self.ctx.update()

        def done(result: dict[str, Any] | None, error: str | None) -> None:
            if not still_valid():
                return
            if error:
                self._plan_preview_result = None
                self.plan_progress_bar.visible = False
                self.plan_preview_text.value = f"無法取得預估：{error}"
                self.plan_preview_text.color = C.RED
            else:
                self._plan_preview_result = result
                self.plan_progress_bar.visible = False
                self.plan_preview_text.value = (
                    f"快速預估範圍 {result['selected_count']:,} 筆；歷史重用 "
                    f"{result['reused_count']:,}；估算送 AI "
                    f"{result['estimated_to_process_count']:,}；待人工審查 "
                    f"{result['pending_review_count']:,}；讀取同版本歷史 "
                    f"{result['history_runs_consulted']} 份歷史；開始執行前會重新計算精確計畫。"
                )
                self.plan_preview_text.color = C.MUTED
                self._update_estimate_text(update=False)
            self.ctx.update()

        self._planning_controller.refresh(
            request,
            still_valid=still_valid,
            on_progress=show_progress,
            on_done=done,
        )

    @staticmethod
    def _control_options(control: ft.Dropdown) -> list[tuple[str, str]]:
        """保存 dropdown 選項，供續跑失敗時還原。"""
        return [(str(option.key), str(option.text)) for option in control.options]

    def _capture_resume_panel_state(self) -> dict[str, Any]:
        """保存續跑兩階段套用前的面板投影。"""
        dropdowns = {
            name: {
                "options": self._control_options(getattr(self, name)),
                "value": getattr(self, name).value,
            }
            for name in ("version_dd", "mod_dd", "kind_dd", "source_dd")
        }
        return {
            "dropdowns": dropdowns,
            "query": self.query_field.value,
            "model": self.model_dd.value,
            "limit": self.limit_field.value,
            "batch": self.batch_field.value,
            "use_recent_history": self.use_recent_history.value,
            "skip_valid_history": self.skip_valid_history.value,
            "scope_loading": self._scope_loading,
            "total": self._total,
            "plan": deepcopy(self._plan_preview_result),
            "scope_modules": tuple(self._scope_modules),
            "scope_sources": deepcopy(self._scope_sources_by_mod),
            "estimate": (self.estimate_text.value, self.estimate_text.color),
            "plan_text": (
                self.plan_preview_text.value,
                self.plan_preview_text.color,
            ),
            "plan_progress_visible": self.plan_progress_bar.visible,
            "plan_progress_value": self.plan_progress_bar.value,
        }

    def _restore_resume_panel_state(self, snapshot: dict[str, Any]) -> None:
        """恢復續跑啟動失敗前的完整條件投影。"""
        self._invalidate_scope_queries(
            candidate_scope_changed=True,
            reason="續跑啟動失敗",
        )
        for name, state in snapshot["dropdowns"].items():
            control = getattr(self, name)
            kit.set_dropdown_options(control, state["options"])
            control.value = state["value"]
        self.query_field.value = snapshot["query"]
        self.model_dd.value = snapshot["model"]
        self.limit_field.value = snapshot["limit"]
        self.batch_field.value = snapshot["batch"]
        self.use_recent_history.value = snapshot["use_recent_history"]
        self.skip_valid_history.value = snapshot["skip_valid_history"]
        self._scope_modules = tuple(snapshot["scope_modules"])
        self._scope_sources_by_mod = deepcopy(snapshot["scope_sources"])
        if snapshot["scope_loading"]:
            self._start_scope_options_load()
            return
        self._scope_loading = False
        self._total = snapshot["total"]
        self._plan_preview_result = deepcopy(snapshot["plan"])
        self.estimate_text.value, self.estimate_text.color = snapshot["estimate"]
        self.plan_preview_text.value, self.plan_preview_text.color = snapshot[
            "plan_text"
        ]
        self.plan_progress_bar.visible = snapshot["plan_progress_visible"]
        self.plan_progress_bar.value = snapshot["plan_progress_value"]
        self.ctx.update()

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
        if not isinstance(resume_preview, dict):
            show_snack(self.ctx.page, "缺少可驗證的續跑狀態。", C.RED)
            return False
        database = self.ctx.database()
        if database is None:
            show_snack(self.ctx.page, "目前沒有可用的 Mod 資料庫。", C.RED)
            return False
        path, priority = database
        generation = self.ctx.generation
        database_identity = self.ctx.db_identity(path, include_generation=False)
        preview_identity = resume_preview.get("_database_identity")
        if preview_identity and preview_identity != database_identity:
            show_snack(self.ctx.page, "續跑狀態已過期，資料庫已切換。", C.RED)
            return False
        try:
            selection = parse_resume_selection(run_id, resume_preview)
            request = build_resume_request(
                selection,
                path=path,
                priority=priority,
                model_override=model,
                batch_size_override=batch_size,
                max_batch_size=int(get_smart_review_settings()["max_batch_size"]),
            )
        except (TypeError, ValueError) as exc:
            show_snack(self.ctx.page, str(exc), C.RED)
            return False
        if not self.ctx.is_current(generation, database_identity):
            show_snack(self.ctx.page, "續跑狀態已過期，資料庫或視窗狀態已變更。", C.RED)
            return False

        snapshot = self._capture_resume_panel_state()
        self._apply_resume_selection(selection, request)
        error = self._launch_request(request)
        if error:
            self._restore_resume_panel_state(snapshot)
            show_snack(self.ctx.page, error, C.RED)
            return False
        return True

    def _apply_resume_selection(self, selection, request) -> None:
        """只把已驗證的續跑輸入投影到執行頁控制項。"""
        self._invalidate_scope_queries(
            candidate_scope_changed=True,
            reason="續跑歷史範圍",
        )
        self._scope_loading = False

        def set_dropdown(control: ft.Dropdown, value: str, label: str) -> None:
            """補上已不在目前未審核選項中的歷史值，避免畫面留舊值。"""
            options = [(option.key, option.text) for option in control.options]
            if value not in {key for key, _text in options}:
                options.append((value, label))
                kit.set_dropdown_options(control, options)
            control.value = value

        set_dropdown(self.version_dd, selection.version, selection.version)
        set_dropdown(
            self.mod_dd,
            str(selection.mod_id or "__all__"),
            str(selection.mod_id or "全部模組"),
        )
        set_dropdown(
            self.kind_dd,
            str(selection.kind or "__all__"),
            str(selection.kind or "全部類型"),
        )
        set_dropdown(
            self.source_dd,
            "__all__"
            if selection.effective_source is None
            else str(selection.effective_source),
            "全部來源"
            if selection.effective_source is None
            else str(selection.effective_source),
        )
        self.query_field.value = selection.query
        self.model_dd.value = request.model
        self.limit_field.value = str(selection.requested_limit)
        self.batch_field.value = str(request.batch_size)
        self.use_recent_history.value = False
        self.skip_valid_history.value = False
        self._update_estimate_text(update=False)


__all__ = [
    "ResumeSelection",
    "SmartReviewPlanPreviewRequest",
    "SmartReviewPlanningController",
    "SmartReviewRunPlanningMixin",
    "build_resume_request",
    "build_smart_review_request",
    "parse_resume_selection",
]
