"""UI for previewing, running, and manually applying smart-review candidates."""

from __future__ import annotations

import threading
from pathlib import Path

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    SMART_REVIEW_DEFAULT_SOURCE,
    SmartReviewCandidate,
    apply_moddb_smart_review_candidate_from_path,
    count_moddb_smart_review_candidates_for_run_from_path,
    count_moddb_smart_review_candidates_from_path,
    get_moddb_smart_review_run_from_path,
    list_moddb_smart_review_candidates_from_path,
    preview_moddb_smart_review_from_path,
    reject_moddb_smart_review_candidate_from_path,
    run_moddb_smart_review_from_path,
    save_moddb_smart_review_draft_from_path,
    smart_review_format_diagnostics,
    smart_review_model_options,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import kit
from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    close_page_dialog,
    dialog_dimensions,
    present_page_dialog,
)
from app.ui.snack import show_snack
from app.views.moddb.formatting import format_count, shorten


class SmartReviewDialog:
    """A bounded local AI run whose output stays in a human-review queue."""

    def __init__(
        self,
        page: ft.Page,
        get_db,
        version: str | None,
        on_complete,
        mod_id: str | None = None,
        kind: str | None = None,
        effective_source: int | None = SMART_REVIEW_DEFAULT_SOURCE,
        query: str = "",
    ):
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self._busy = False
        self._generation = 0
        self._run_id: str | None = None
        self._cancel_event = threading.Event()
        self._operation_handle = None
        self._candidate_total = 0
        self._candidate_page = 1
        self._candidate_page_size = 50
        self._candidate_path: Path | None = None
        self._candidate_priority: tuple[int, ...] = ()
        self._build_controls(version, mod_id, kind, effective_source, query)
        self._build_dialog()

    def _build_controls(
        self,
        version: str | None,
        mod_id: str | None,
        kind: str | None,
        effective_source: int | None,
        query: str,
    ) -> None:
        db = self.get_db()
        versions = db.versions() if db else []
        self.version_dd = kit.dropdown(
            label="版本", dense=True, width=180, on_select=self._on_version
        )
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        self.version_dd.value = version if version in versions else None

        selected_version = self.version_dd.value
        mods = db.mods(selected_version) if db and selected_version else []
        kinds = db.kinds(selected_version) if db and selected_version else []
        source_codes = (
            db.effective_source_codes(selected_version)
            if db and selected_version
            else ()
        )
        self.mod_dd = kit.dropdown(
            label="模組", dense=True, width=210, on_select=self._invalidate
        )
        kit.set_dropdown_options(
            self.mod_dd,
            [("__all__", "全部模組"), *((m, m) for m in mods)],
        )
        self.mod_dd.value = mod_id if mod_id in mods else "__all__"
        self.kind_dd = kit.dropdown(
            label="類型", dense=True, width=150, on_select=self._invalidate
        )
        kit.set_dropdown_options(
            self.kind_dd,
            [("__all__", "全部類型"), *((k, k) for k in kinds)],
        )
        self.kind_dd.value = kind if kind in kinds else "__all__"
        self.source_dd = kit.dropdown(
            label="目前生效來源", dense=True, width=180, on_select=self._invalidate
        )
        codes = tuple(dict.fromkeys((SMART_REVIEW_DEFAULT_SOURCE, *source_codes)))
        catalog = getattr(db, "source_catalog", None)
        labels = {
            code: catalog.label(code) if catalog is not None else str(code)
            for code in codes
        }
        kit.set_dropdown_options(
            self.source_dd,
            [("__all__", "全部來源"), *((str(c), labels[c]) for c in codes)],
        )
        self.source_dd.value = (
            "__all__"
            if effective_source is None
            else str(effective_source)
            if effective_source in codes
            else str(SMART_REVIEW_DEFAULT_SOURCE)
        )
        self.query_field = kit.text_field(
            "條目搜尋",
            value=query,
            hint="key、英文原文或目前譯文",
            width=230,
            on_change=self._invalidate,
        )

        self.model_dd = kit.dropdown(
            label="校對模型", dense=True, width=240, on_select=self._invalidate
        )
        options = smart_review_model_options()
        kit.set_dropdown_options(self.model_dd, options)
        self.model_dd.value = options[0][0] if options else None
        self.limit_field = kit.text_field(
            "上限（筆）", value="1000", width=150, on_change=self._invalidate
        )
        self.batch_field = kit.text_field(
            "每批（筆）", value="40", width=150, on_change=self._invalidate
        )
        self.preview_btn = kit.button("預覽候選", "secondary", on_click=self._preview)
        self.run_btn = kit.button(
            "開始智慧校對", "primary", icon=ft.Icons.AUTO_AWESOME, on_click=self._run
        )
        self.close_btn = ft.TextButton("關閉", on_click=self._close)
        self.cancel_btn = ft.TextButton(
            "停止後續批次", on_click=self._cancel, visible=False
        )
        self.run_id_field = kit.text_field(
            "載入 run_id", hint="重新開啟既有校對批次", width=310, mono=True
        )
        self.load_run_btn = kit.button(
            "載入歷史批次", "secondary", on_click=self._load_existing_run
        )
        self.status = ft.Text("尚未預覽", size=12, color=C.MUTED, selectable=True)
        self.summary = ft.Text("尚未載入候選。", size=14, weight=ft.FontWeight.BOLD)
        self.candidates = ft.Column(spacing=8)
        self.candidate_pager = kit.Pager(
            0, page_size=self._candidate_page_size, on_change=self._on_candidate_page
        )

    def _build_dialog(self) -> None:
        width, height = dialog_dimensions(self.page)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("AI 智慧校對（候選模式）"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "只讀取目前生效且未審核的內容與本地 reviewed context；"
                            "AI 結果先存為候選，不會自動覆蓋譯文或標記已審核。",
                            size=12,
                            color=C.MUTED,
                            selectable=True,
                        ),
                        ft.Row(
                            [
                                self.version_dd,
                                self.mod_dd,
                                self.kind_dd,
                                self.source_dd,
                                self.query_field,
                                self.model_dd,
                                self.limit_field,
                                self.batch_field,
                            ],
                            spacing=10,
                            wrap=True,
                        ),
                        ft.Row(
                            [self.preview_btn, self.run_btn, self.cancel_btn], spacing=8
                        ),
                        ft.Row(
                            [self.run_id_field, self.load_run_btn], spacing=8, wrap=True
                        ),
                        self.status,
                        self.summary,
                        self.candidates,
                        self.candidate_pager,
                    ],
                    tight=True,
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=width,
                height=height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[self.close_btn],
            on_dismiss=self._on_dismiss,
        )

    def open(self) -> None:
        try:
            present_page_dialog(self.page, self.dialog)
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟智慧校對視窗。", C.GOLD)

    def _close(self, _e=None) -> None:
        if self._busy:
            self._cancel()
        self._generation += 1
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        if self._busy:
            self._cancel()
        self._generation += 1

    def _cancel(self, _e=None) -> None:
        if not self._busy:
            return
        self._cancel_event.set()
        handle = self._operation_handle
        request_cancel = getattr(handle, "request_cancel", None)
        if callable(request_cancel):
            request_cancel()
        self.status.value = "已要求停止；目前 API 回應返回後不再送下一批。"
        self._safe_update()

    def _on_version(self, event) -> None:
        value = event.control.value
        db = self.get_db()
        mods = db.mods(value) if db and value else []
        kinds = db.kinds(value) if db and value else []
        source_codes = db.effective_source_codes(value) if db and value else ()
        kit.set_dropdown_options(
            self.mod_dd,
            [("__all__", "全部模組"), *((m, m) for m in mods)],
        )
        kit.set_dropdown_options(
            self.kind_dd,
            [("__all__", "全部類型"), *((k, k) for k in kinds)],
        )
        catalog = getattr(db, "source_catalog", None)
        codes = tuple(dict.fromkeys((SMART_REVIEW_DEFAULT_SOURCE, *source_codes)))
        kit.set_dropdown_options(
            self.source_dd,
            [
                ("__all__", "全部來源"),
                *(
                    (
                        str(code),
                        catalog.label(code) if catalog is not None else str(code),
                    )
                    for code in codes
                ),
            ],
        )
        self.mod_dd.value = "__all__"
        self.kind_dd.value = "__all__"
        self.source_dd.value = str(SMART_REVIEW_DEFAULT_SOURCE)
        self._invalidate()

    def _invalidate(self, _e=None) -> None:
        if not self._busy:
            self._run_id = None
            self._candidate_total = 0
            self._candidate_page = 1
            self.candidates.controls = []
            self.candidate_pager.set_state(0, 1)
            self.summary.value = "條件已變更，請重新預覽。"
            self._safe_update()

    def _parse_int(self, control, label: str, *, minimum: int = 1) -> int:
        try:
            value = int(str(control.value or "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} 必須是整數") from exc
        if value < minimum:
            raise ValueError(f"{label} 必須至少為 {minimum}")
        return value

    def _scope(
        self,
    ) -> tuple[
        Path,
        tuple[int, ...],
        str,
        str | None,
        str | None,
        int | None,
        str,
        int,
        int,
        str,
    ]:
        db = self.get_db()
        if db is None or not getattr(db, "path", None):
            raise ValueError("目前沒有可用的 Mod 資料庫")
        version = self.version_dd.value
        model = self.model_dd.value
        if not version:
            raise ValueError("請先選擇遊戲版本")
        if not model:
            raise ValueError("請先選擇校對模型")
        mod_id = self.mod_dd.value
        kind = self.kind_dd.value
        source = self.source_dd.value
        return (
            Path(db.path).resolve(),
            tuple(getattr(db, "priority", ())),
            str(version),
            None if mod_id in (None, "", "__all__") else str(mod_id),
            None if kind in (None, "", "__all__") else str(kind),
            None if source in (None, "", "__all__") else int(source),
            str(self.query_field.value or "").strip(),
            self._parse_int(self.limit_field, "上限"),
            self._parse_int(self.batch_field, "每批"),
            str(model),
        )

    def _preview(self, _e=None) -> None:
        if self._busy:
            return
        try:
            (
                path,
                priority,
                version,
                mod_id,
                kind,
                effective_source,
                query,
                limit,
                _batch_size,
                _model,
            ) = self._scope()
        except ValueError as exc:
            self.status.value = str(exc)
            self._safe_update()
            return
        self._set_busy(True, "正在唯讀查詢候選…")
        generation = self._generation

        def work() -> None:
            try:
                total = count_moddb_smart_review_candidates_from_path(
                    path,
                    priority,
                    version=version,
                    mod_id=mod_id,
                    kind=kind,
                    effective_source=effective_source,
                    review_status="unreviewed",
                    query=query,
                )
                rows = preview_moddb_smart_review_from_path(
                    path,
                    priority,
                    version=version,
                    mod_id=mod_id,
                    kind=kind,
                    effective_source=effective_source,
                    review_status="unreviewed",
                    query=query,
                    limit=min(limit, 3),
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(self._apply_preview, generation, None, None, str(exc))
                return
            self._post(self._apply_preview, generation, total, rows, None)

        self._launch(work, "Mod 資料庫智慧校對預覽", CommitPolicy.EPHEMERAL)

    def _apply_preview(
        self, generation: int, total: int | None, rows, error: str | None
    ) -> None:
        if generation != self._generation:
            self._set_busy(False, "預覽已過期，請重新查詢。")
            return
        self._set_busy(False, "")
        if error:
            self.summary.value = f"預覽失敗：{error}"
            self.candidates.controls = []
        else:
            self.summary.value = (
                f"目前符合候選：{format_count(total or 0)} 筆（預覽不寫入）"
            )
            self.candidates.controls = (
                [
                    ft.Text(
                        f"範例：{shorten(rows[0].en_us, 100)} → {shorten(rows[0].current_zh_tw, 100)}",
                        size=12,
                        color=C.MUTED,
                    )
                ]
                if rows
                else [kit.hint_text("目前條件沒有未審核且有譯文的條目。")]
            )
        self._safe_update()

    def _load_existing_run(self, _e=None) -> None:
        if self._busy:
            return
        run_id = str(self.run_id_field.value or "").strip()
        if not run_id:
            self.status.value = "請輸入要重新開啟的 run_id。"
            self._safe_update()
            return
        db = self.get_db()
        if db is None or not getattr(db, "path", None):
            self.status.value = "目前沒有可用的 Mod 資料庫"
            self._safe_update()
            return
        path = Path(db.path).resolve()
        priority = tuple(getattr(db, "priority", ()))
        try:
            run = get_moddb_smart_review_run_from_path(path, priority, run_id)
        except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
            self.status.value = f"載入批次失敗：{exc}"
            self._safe_update()
            return
        if run is None:
            self.status.value = f"找不到 run_id：{run_id}"
            self._safe_update()
            return
        scope = run.get("scope") or {}
        version = scope.get("version") or run.get("scope_version")
        if version:
            self.version_dd.value = version
            self._on_version(type("Event", (), {"control": self.version_dd})())
        mod_id = scope.get("mod_id")
        kind = scope.get("kind")
        source = scope.get("effective_source")
        if mod_id and any(option.key == mod_id for option in self.mod_dd.options):
            self.mod_dd.value = mod_id
        if kind and any(option.key == kind for option in self.kind_dd.options):
            self.kind_dd.value = kind
        self.source_dd.value = (
            "__all__"
            if source is None
            else str(source)
            if any(option.key == str(source) for option in self.source_dd.options)
            else str(SMART_REVIEW_DEFAULT_SOURCE)
        )
        self.query_field.value = str(scope.get("query") or "")
        if run.get("requested_limit"):
            self.limit_field.value = str(run["requested_limit"])
        if run.get("batch_size"):
            self.batch_field.value = str(run["batch_size"])
        self._run_id = run_id
        self._candidate_page = 1
        self.summary.value = (
            f"已載入 {run_id} · {run.get('status', 'unknown')} · "
            f"已處理 {int(run.get('processed_count') or 0):,} / "
            f"{int(run.get('selected_count') or 0):,} 筆"
        )
        self.status.value = "已重新開啟既有校對批次；套用前仍會進行 CAS 檢查。"
        self._load_candidates(path, priority, run_id, page=1)
        self._safe_update()

    def _run(self, _e=None) -> None:
        if self._busy:
            return
        try:
            (
                path,
                priority,
                version,
                mod_id,
                kind,
                effective_source,
                query,
                limit,
                batch_size,
                model,
            ) = self._scope()
        except ValueError as exc:
            self.status.value = str(exc)
            self._safe_update()
            return
        self._set_busy(True, "AI 校對執行中；候選會分批保存…")
        generation = self._generation

        def progress(snapshot: dict) -> None:
            self._post(
                self._apply_progress,
                generation,
                snapshot.get("processed", 0),
                snapshot.get("selected", 0),
                snapshot.get("status", "running"),
            )

        def work() -> None:
            try:
                result = run_moddb_smart_review_from_path(
                    path,
                    priority,
                    model_name=model,
                    version=version,
                    mod_id=mod_id,
                    kind=kind,
                    effective_source=effective_source,
                    review_status="unreviewed",
                    query=query,
                    limit=limit,
                    batch_size=batch_size,
                    progress=progress,
                    cancel_check=self._cancel_event.is_set,
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(self._apply_run, generation, path, priority, None, str(exc))
                return
            self._post(self._apply_run, generation, path, priority, result, None)

        self._launch(
            work,
            "Mod 資料庫智慧校對",
            CommitPolicy.PARTIAL_ALLOWED,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _apply_progress(
        self, generation: int, processed: int, selected: int, status: str
    ) -> None:
        if generation == self._generation:
            self.status.value = f"AI 校對中：{processed:,} / {selected:,} 筆 · {status}"
            self._safe_update()

    def _apply_run(self, generation, path, priority, result, error) -> None:
        if generation != self._generation:
            self._set_busy(False, "執行結果已過期。")
            return
        self._set_busy(False, "")
        if error:
            self.summary.value = f"執行失敗：{error}"
            self.candidates.controls = []
        else:
            self._run_id = result.run_id
            self.run_id_field.value = result.run_id
            status_label = {
                "completed": "完成",
                "partial": "部分完成",
                "cancelled": "已取消",
                "failed": "失敗",
            }.get(result.status, result.status)
            self.summary.value = (
                f"{status_label} {result.processed_count:,} 筆：有效 {result.valid_count:,}、"
                f"建議 {result.suggestion_count:,}、不變 {result.no_change_count:,}、"
                f"需補 context {result.needs_context_count:,}、無效 {result.invalid_count:,}"
            )
            self._load_candidates(path, priority, result.run_id)
            if self.on_complete:
                self.on_complete(result)
        self._safe_update()

    def _load_candidates(
        self,
        path: Path,
        priority: tuple[int, ...],
        run_id: str,
        *,
        page: int | None = None,
        total: int | None = None,
    ) -> None:
        self._candidate_path = path
        self._candidate_priority = priority
        if page is not None:
            self._candidate_page = max(1, int(page))
        offset = (self._candidate_page - 1) * self._candidate_page_size
        try:
            if total is None:
                total = count_moddb_smart_review_candidates_for_run_from_path(
                    path, priority, run_id
                )
            rows = list_moddb_smart_review_candidates_from_path(
                path,
                priority,
                run_id,
                limit=self._candidate_page_size,
                offset=offset,
            )
        except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
            self.candidates.controls = [ft.Text(f"候選載入失敗：{exc}", color=C.RED)]
            return
        self._candidate_total = int(total)
        self.candidate_pager.set_state(self._candidate_total, self._candidate_page)
        self.candidates.controls = [
            self._candidate_card(path, priority, row) for row in rows
        ]
        if not rows:
            self.candidates.controls = [kit.hint_text("此 run 沒有候選結果。")]

    def _on_candidate_page(self, page: int) -> None:
        if self._busy or not self._run_id or self._candidate_path is None:
            return
        self._candidate_page = max(1, int(page))
        self._load_candidates(
            self._candidate_path,
            self._candidate_priority,
            self._run_id,
            page=self._candidate_page,
            total=self._candidate_total,
        )
        self._safe_update()

    def _focus_candidate(self, ordinal: int) -> None:
        if not self._run_id or self._candidate_path is None:
            return
        if ordinal < 1 or ordinal > self._candidate_total:
            return
        self._candidate_page = (ordinal - 1) // self._candidate_page_size + 1
        self._load_candidates(
            self._candidate_path,
            self._candidate_priority,
            self._run_id,
            page=self._candidate_page,
            total=self._candidate_total,
        )
        self._safe_update()

    def _candidate_source_label(self, row: SmartReviewCandidate) -> str:
        db = self.get_db()
        return (
            db.source_catalog.label(row.effective_source)
            if db is not None
            and row.effective_source is not None
            and getattr(db, "source_catalog", None) is not None
            else ("無" if row.effective_source is None else str(row.effective_source))
        )

    def _candidate_draft_controls(self, row: SmartReviewCandidate):
        draft_control = kit.text_field(
            "人工修正草稿",
            value=row.draft_zh_tw or row.suggested_zh_tw or "",
            multiline=True,
            min_lines=2,
            max_lines=6,
            expand=True,
            read_only=row.status != "ready",
        )
        format_text = ft.Text(size=12, color=C.MUTED, selectable=True)

        def refresh_format(_e=None) -> None:
            value = str(draft_control.value or "").strip()
            if not value:
                format_text.value = "格式診斷：尚無可分析草稿"
                format_text.color = C.MUTED
                return
            try:
                diagnostics = smart_review_format_diagnostics(
                    row.en_us, value, kind=row.kind
                )
            except (TypeError, ValueError) as exc:
                format_text.value = f"格式診斷無法執行：{exc}"
                format_text.color = C.GOLD
                return
            lines = diagnostics.summary_lines
            format_text.value = (
                "格式診斷：" + "；".join(lines) if lines else "格式診斷：未發現格式問題"
            )
            format_text.color = C.RED if diagnostics.has_issues else C.EM

        draft_control.on_change = refresh_format
        refresh_format()
        return draft_control, format_text

    @staticmethod
    def _candidate_evidence_text(row: SmartReviewCandidate) -> str:
        evidence_by_id = {str(item.get("id")): item for item in row.context}
        evidence_ids = row.evidence_ids or tuple(evidence_by_id)[:6]
        evidence_lines = []
        for evidence_id in evidence_ids[:8]:
            item = evidence_by_id.get(str(evidence_id))
            if item is None:
                evidence_lines.append(f"{evidence_id}（找不到本地證據）")
                continue
            evidence_lines.append(
                f"{evidence_id} · {item.get('reference_type', 'local')} · "
                f"{item.get('en_us', '')} → {item.get('zh_tw', '')} · "
                f"{item.get('limitations', '')}"
            )
        evidence_text = (
            "\n".join(evidence_lines)
            if evidence_lines
            else "無本地術語證據；請視為需要人工確認。"
        )
        return evidence_text

    def _candidate_action_row(
        self,
        path: Path,
        priority: tuple[int, ...],
        row: SmartReviewCandidate,
        draft_control,
    ):
        apply_btn = kit.button(
            "套用這筆",
            "primary",
            size="sm",
            on_click=lambda _e, ordinal=row.ordinal: self._apply_candidate(
                path,
                priority,
                row.run_id,
                ordinal,
                draft_zh_tw=str(draft_control.value or ""),
            ),
            disabled=row.status != "ready",
        )
        save_btn = kit.button(
            "儲存草稿",
            "secondary",
            size="sm",
            on_click=lambda _e, ordinal=row.ordinal: self._save_draft(
                path, priority, row.run_id, ordinal, draft_control
            ),
            disabled=row.status != "ready",
        )
        reject_btn = kit.button(
            "跳過／保留原譯文",
            "secondary",
            size="sm",
            on_click=lambda _e, ordinal=row.ordinal: self._reject_candidate(
                path, priority, row.run_id, ordinal
            ),
            disabled=row.status != "ready",
        )
        return ft.Row(
            [
                ft.TextButton(
                    "上一筆",
                    on_click=lambda _e: self._focus_candidate(row.ordinal - 1),
                    disabled=row.ordinal <= 1,
                ),
                ft.TextButton(
                    "下一筆",
                    on_click=lambda _e: self._focus_candidate(row.ordinal + 1),
                    disabled=row.ordinal >= self._candidate_total,
                ),
                reject_btn,
                save_btn,
                apply_btn,
            ],
            spacing=8,
            wrap=True,
        )

    def _candidate_card(
        self, path: Path, priority: tuple[int, ...], row: SmartReviewCandidate
    ):
        source_label = self._candidate_source_label(row)
        draft_control, format_text = self._candidate_draft_controls(row)
        evidence_text = self._candidate_evidence_text(row)
        action_row = self._candidate_action_row(path, priority, row, draft_control)
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                f"#{row.ordinal} {row.mod_id}:{row.key}",
                                weight=ft.FontWeight.W_600,
                            ),
                            ft.Text(
                                f"{row.status} · {row.mc_version} · {row.kind}",
                                size=12,
                                color=C.MUTED,
                            ),
                        ],
                        spacing=8,
                        wrap=True,
                    ),
                    action_row,
                    ft.Text(f"原文：{row.en_us}", size=12, selectable=True),
                    ft.Text(f"目前：{row.current_zh_tw}", size=12, selectable=True),
                    ft.Text(
                        f"來源：{source_label} · 審核：{row.review_status or '未標記'}",
                        size=12,
                        color=C.MUTED,
                        selectable=True,
                    ),
                    ft.Text(
                        f"理由：{row.reason or '（舊候選沒有儲存 reason）'}",
                        size=12,
                        selectable=True,
                    ),
                    ft.Text(
                        f"專有名詞／問題類型：{', '.join(row.issue_types) or '未標記'}",
                        size=12,
                        color=C.MUTED,
                        selectable=True,
                    ),
                    ft.Text(
                        "本地 evidence：" + evidence_text, size=12, selectable=True
                    ),
                    draft_control,
                    format_text,
                ],
                spacing=5,
            ),
            padding=10,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
        )

    def _save_draft(
        self,
        path: Path,
        priority: tuple[int, ...],
        run_id: str,
        ordinal: int,
        draft_control,
    ) -> None:
        if self._busy:
            return
        draft = str(draft_control.value or "")
        self._set_busy(True, f"儲存候選 #{ordinal} 草稿…")

        def work() -> None:
            try:
                status = save_moddb_smart_review_draft_from_path(
                    path, priority, run_id, ordinal, draft
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(
                    self._apply_draft_result, path, priority, run_id, None, str(exc)
                )
                return
            self._post(self._apply_draft_result, path, priority, run_id, status, None)

        self._launch(
            work,
            "儲存智慧校對草稿",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _apply_draft_result(self, path, priority, run_id, status, error) -> None:
        self._set_busy(False, "")
        if error:
            show_snack(self.page, f"草稿儲存失敗：{error}", C.RED)
        elif status == "saved":
            show_snack(self.page, "草稿已儲存，尚未改寫正式譯文。", C.EM)
            self._load_candidates(path, priority, run_id, total=self._candidate_total)
        else:
            show_snack(self.page, f"草稿目前無法儲存：{status}", C.GOLD)
        self._safe_update()

    def _apply_candidate(
        self,
        path: Path,
        priority: tuple[int, ...],
        run_id: str,
        ordinal: int,
        *,
        draft_zh_tw: str | None = None,
    ) -> None:
        if self._busy:
            return
        self._set_busy(True, f"套用候選 #{ordinal}…")

        def work() -> None:
            try:
                status = apply_moddb_smart_review_candidate_from_path(
                    path,
                    priority,
                    run_id,
                    ordinal,
                    draft_zh_tw=draft_zh_tw,
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(
                    self._apply_candidate_result, path, priority, run_id, None, str(exc)
                )
                return
            self._post(
                self._apply_candidate_result, path, priority, run_id, status, None
            )

        self._launch(
            work,
            "套用智慧校對候選",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _apply_candidate_result(self, path, priority, run_id, status, error) -> None:
        self._set_busy(False, "")
        if error:
            show_snack(self.page, f"套用失敗：{error}", C.RED)
        elif status == "applied":
            show_snack(self.page, "已套用 1 筆人工未審核譯文。", C.EM)
            self._load_candidates(path, priority, run_id)
        elif status == "stale":
            show_snack(
                self.page, "候選已過期，資料庫內容已變更；未覆蓋目前譯文。", C.GOLD
            )
            self._load_candidates(path, priority, run_id)
        else:
            show_snack(self.page, f"此候選目前無法套用：{status}", C.GOLD)
        self._safe_update()

    def _reject_candidate(
        self, path: Path, priority: tuple[int, ...], run_id: str, ordinal: int
    ) -> None:
        if self._busy:
            return
        self._set_busy(True, f"保留候選 #{ordinal} 的原譯文…")

        def work() -> None:
            try:
                status = reject_moddb_smart_review_candidate_from_path(
                    path, priority, run_id, ordinal
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(
                    self._apply_reject_result, path, priority, run_id, None, str(exc)
                )
                return
            self._post(self._apply_reject_result, path, priority, run_id, status, None)

        self._launch(
            work,
            "保留智慧校對原譯文",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _apply_reject_result(self, path, priority, run_id, status, error) -> None:
        self._set_busy(False, "")
        if error:
            show_snack(self.page, f"保留失敗：{error}", C.RED)
        elif status == "rejected":
            show_snack(self.page, "已保留原譯文，候選不會套用。", C.EM)
            self._load_candidates(path, priority, run_id)
        else:
            show_snack(self.page, f"此候選目前無法保留：{status}", C.GOLD)
        self._safe_update()

    def _launch(
        self,
        work,
        name: str,
        commit: CommitPolicy,
        *,
        durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE,
    ) -> None:
        self._cancel_event.clear()
        try:
            launched = launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-smart-review",
                cancellation=CancellationPolicy.COOPERATIVE,
                commit=commit,
                durability=durability,
                shutdown=ShutdownPolicy.CANCEL_AND_DRAIN,
            )
        except (AttributeError, RuntimeError) as exc:
            self._set_busy(False, f"操作未啟動：{exc}")
            return
        self._operation_handle = (
            launched if hasattr(launched, "request_cancel") else None
        )
        if not launched:
            self._set_busy(False, "應用程式正在關閉，未啟動操作。")

    def _post(self, callback, *args) -> None:
        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):

            async def runner() -> None:
                callback(*args)

            run_task(runner)
        else:
            callback(*args)

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        if not busy:
            self._operation_handle = None
        for control in (
            self.version_dd,
            self.mod_dd,
            self.kind_dd,
            self.source_dd,
            self.query_field,
            self.model_dd,
            self.limit_field,
            self.batch_field,
            self.preview_btn,
            self.run_btn,
            self.load_run_btn,
        ):
            control.disabled = busy
        self.cancel_btn.visible = busy
        self.cancel_btn.disabled = False
        self.status.value = status
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.page.update()
        except RuntimeError:
            pass


__all__ = ["SmartReviewDialog"]
