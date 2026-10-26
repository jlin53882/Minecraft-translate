"""智慧校對審查的批量模式：分頁勾選候選，預覽後背景套用、保留或覆蓋。"""

from __future__ import annotations

import threading

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    SmartReviewBatchResult,
    SmartReviewCandidate,
    SmartReviewStatus,
    apply_moddb_smart_review_batch_from_path,
    moddb_smart_review_read_session,
)
from app.tasks.operation_registry import CommitPolicy, DurabilityPolicy
from app.ui import design, kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.formatting import format_count, shorten
from app.views.moddb.smart_review_batch_loader import (
    load_batch_page,
    should_load_summary,
)
from app.views.moddb.smart_review_batch_outcome import (
    SUCCESS_OUTCOMES,
    summarize_outcomes,
)
from app.views.moddb.smart_review_batch_progress import SmartReviewBatchProgress
from app.views.moddb.smart_review_candidate_loader import LoadedRun
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_evidence import (
    SmartReviewEvidencePresenter,
    initial_draft_text,
)
from app.views.moddb.smart_review_reason import (
    build_batch_text_line,
    issue_text,
    reason_text,
)
from app.views.moddb.smart_review_reviewer_state import (
    ALL_FILTER,
    FILTERS,
    STATUS_LABEL,
)

PAGE_SIZE = 25
_BATCHABLE_STATUS = SmartReviewStatus.READY


class SmartReviewBatchReviewer:
    """提供有界分頁、跨頁選取與部分成功結果摘要的批量審查面板。"""

    def __init__(self, ctx: SmartReviewContext, *, on_changed) -> None:
        """建立批量審查控制項；批次由 ``load_run`` 指定，不在初始化時讀資料庫。"""
        self.ctx = ctx
        self.on_changed = on_changed
        self.run_id: str | None = None
        self.run_info: dict = {}
        self.counts: dict[str, int] = {}
        self._summary_loaded = False
        self.status_filter = str(_BATCHABLE_STATUS)
        self.page = 1
        self.total = 0
        self.rows: list[SmartReviewCandidate] = []
        self._row_cache: dict[int, SmartReviewCandidate] = {}
        self._draft_validity_cache: dict[tuple[str, str, str], bool] = {}
        self._selected: set[int] = set()
        self._request_seq = 0
        self._operation_seq = 0
        self._loading = False
        self._busy = False
        self._cancel_event = threading.Event()
        self._last_result: SmartReviewBatchResult | None = None
        self._build_controls()
        self.body = ft.Column([], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self.control = ft.Column(
            [self._toolbar(), self.body, self.pager],
            spacing=10,
            expand=True,
        )
        self._show_empty("尚未載入批次", "請從歷史批次或執行結果開啟智慧校對批次。")

    # ──────────────── 控制項 ────────────────

    def _build_controls(self) -> None:
        """建立篩選、選取、批量動作、進度與結果控制項。"""
        self.filter_dd = kit.dropdown(
            label="候選篩選", dense=True, width=230, on_select=self._on_filter
        )
        kit.set_dropdown_options(self.filter_dd, list(FILTERS))
        self.filter_dd.value = self.status_filter
        self.refresh_btn = kit.button(
            "重新整理",
            "secondary",
            icon=ft.Icons.REFRESH,
            on_click=lambda _e: self.refresh(),
            tooltip="重新讀取目前批次與狀態",
        )
        self.select_page_btn = kit.button(
            "全選本頁",
            "ghost",
            size="sm",
            icon=ft.Icons.CHECK_BOX_OUTLINED,
            on_click=lambda _e: self._select_page(),
        )
        self.select_all_btn = kit.button(
            "全選全部可套用",
            "ghost",
            size="sm",
            icon=ft.Icons.SELECT_ALL,
            on_click=lambda _e: self._select_all_safe(),
        )
        self.select_override_all_btn = kit.button(
            "全選全部可覆蓋",
            "ghost",
            size="sm",
            icon=ft.Icons.WARNING_AMBER,
            on_click=lambda _e: self._select_all_override(),
            tooltip="載入所有已過期且格式可用的候選，準備明確覆蓋",
        )
        self.clear_btn = kit.button(
            "清除選取",
            "ghost",
            size="sm",
            icon=ft.Icons.CHECK_BOX_OUTLINE_BLANK,
            on_click=lambda _e: self._clear_selection(),
        )
        self.selection_text = ft.Text("尚未選取候選", size=12, color=C.MUTED)
        self.summary_text = ft.Text(
            "", size=12.5, color=C.TEXT, weight=ft.FontWeight.W_600, selectable=True
        )
        self.run_text = ft.Text(
            "",
            size=11.5,
            color=C.DIM,
            selectable=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.status_text = ft.Text("", size=12, color=C.MUTED, selectable=True)
        self.progress_bar = kit.progress_bar(0.0, "em", height=7)
        self.progress_bar.visible = False
        self.apply_btn = kit.button(
            "批量套用",
            "primary",
            icon=ft.Icons.CHECK,
            on_click=lambda _e: self._confirm_action("apply"),
        )
        self.reject_btn = kit.button(
            "批量保留原譯文",
            "secondary",
            icon=ft.Icons.RESTORE,
            on_click=lambda _e: self._confirm_action("reject"),
        )
        self.override_btn = kit.button(
            "批量覆蓋已過期",
            "danger",
            icon=ft.Icons.WARNING_AMBER,
            on_click=lambda _e: self._confirm_action("override"),
            tooltip="先更新 JSON 目前譯文快照，再覆蓋資料庫目前譯文",
        )
        self.cancel_btn = kit.button(
            "停止後續項目",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            on_click=lambda _e: self._cancel(),
        )
        self.cancel_btn.visible = False
        self.result_text = ft.Text("", size=12.5, color=C.MUTED, selectable=True)
        self.result_box = ft.Container(
            content=self.result_text,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
            visible=False,
        )
        self.pager = kit.Pager(0, page_size=PAGE_SIZE, on_change=self._on_page)
        self.pager.visible = False

    def _toolbar(self) -> ft.Control:
        """建立批量模式的篩選、跨頁選取與動作工具列。"""
        return kit.section_card(
            "批量審查",
            ft.Column(
                [
                    kit.hint_text(
                        "先勾選候選再確認批量動作；一般套用不會覆蓋過期資料，需明確使用「批量覆蓋已過期」。"
                    ),
                    ft.Row(
                        [self.filter_dd, self.refresh_btn],
                        spacing=8,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [
                            self.select_page_btn,
                            self.select_all_btn,
                            self.select_override_all_btn,
                            self.clear_btn,
                            self.selection_text,
                        ],
                        spacing=6,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=ft.Column(
                            [self.summary_text, self.run_text], spacing=4, tight=True
                        ),
                        padding=ft.Padding.symmetric(horizontal=12, vertical=9),
                        bgcolor=C.PANEL2,
                        border=ft.Border.all(1, C.LINE),
                        border_radius=design.RADIUS_CONTROL,
                    ),
                    ft.Row(
                        [
                            self.apply_btn,
                            self.reject_btn,
                            self.override_btn,
                            self.cancel_btn,
                        ],
                        spacing=8,
                        wrap=True,
                    ),
                    self.status_text,
                    self.progress_bar,
                    self.result_box,
                ],
                spacing=9,
            ),
            icon=ft.Icons.LAYERS_OUTLINED,
            tone="em",
            badge=kit.chip("分頁選取", "dia"),
            collapsible=True,
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
        )

    # ──────────────── 載入與分頁 ────────────────

    def load_run(self, run_id: str, *, loaded_run: LoadedRun | None = None) -> None:
        """切換批次、重設選取並從第一頁載入候選。

        ``loaded_run`` 由對話框共用逐筆面板已取得的批次摘要，確保兩種
        審查模式的篩選計數來自同一個讀取世代。
        """
        self.run_id = run_id
        self.run_info = {}
        self.counts = {}
        self.status_filter = str(_BATCHABLE_STATUS)
        self.filter_dd.value = self.status_filter
        self.page = 1
        self.total = 0
        self.rows = []
        self._row_cache.clear()
        self._draft_validity_cache.clear()
        self._selected.clear()
        self._last_result = None
        self.result_box.visible = False
        self._request_seq += 1
        self._show_loading("正在載入批量候選…")
        if loaded_run is not None:
            if loaded_run.info is None:
                self._show_empty("找不到批次", f"run_id：{run_id}", keep_toolbar=True)
                return
            self.run_info = loaded_run.info
            self._apply_page_summary({"status": loaded_run.counts})
            self._choose_initial_filter(self.counts)
            self._load_page(
                include_run=False,
                include_summary=False,
                known_total=self._summary_total(),
                stable_identity=True,
            )
            return
        self._load_page(include_run=True)

    def refresh(self) -> None:
        """同步重新讀取目前頁；刷新會清除選取，避免使用舊快照批量寫入。"""
        if self._busy or not self.run_id:
            return
        self._selected.clear()
        self._row_cache.clear()
        self._draft_validity_cache.clear()
        self._load_page(include_run=True, background=False)

    def _load_page(
        self,
        *,
        include_run: bool = False,
        include_summary: bool | None = None,
        known_total: int | None = None,
        background: bool = True,
        stable_identity: bool = False,
    ) -> None:
        """在單一唯讀連線中取得統計、總數與目前頁候選。

        初始批次載入可在背景執行；使用者換頁、篩選與重新整理傳入
        ``background=False``，避免可見清單出現晚到的背景結果。
        共用批次摘要的初始頁面可使用 ``stable_identity=True``，讓執行中的
        工作更新資料庫世代時仍能顯示同一份審查快照。
        """
        database = self.ctx.database()
        if database is None or not self.run_id:
            self._show_empty("沒有可用的資料庫", "請先在條目校對選擇資料庫。")
            return
        path, priority = database
        run_id = self.run_id
        status = self._status_query()
        page = max(1, self.page)
        if include_summary is None:
            include_summary = should_load_summary(include_run, self._summary_loaded)
        token = self._request_seq = self._request_seq + 1
        self._begin_page_load(page)

        def work():
            return load_batch_page(
                path,
                priority,
                run_id,
                status,
                page,
                PAGE_SIZE,
                moddb_smart_review_read_session,
                include_run=include_run,
                include_summary=include_summary,
                known_total=known_total,
            )

        def done(result, error: str | None) -> None:
            if token != self._request_seq:
                return
            self._finish_page_load(
                result,
                error,
                include_run=include_run,
                run_id=run_id,
                status=status,
                page=page,
                background=background,
            )

        def still_valid() -> bool:
            return token == self._request_seq

        if background:
            self.ctx.run_background(
                work,
                done,
                name="載入智慧校對批量候選",
                stable_identity=stable_identity,
                still_valid=still_valid,
            )
        else:
            self.ctx.run_inline(work, done, still_valid=still_valid)

    def _begin_page_load(self, page: int) -> None:
        """Disable visible controls while a page request is in flight."""
        self._loading = True
        self._set_controls_enabled(False)
        self.status_text.value = (
            f"正在切換第 {page} 頁…" if self.rows else "正在讀取候選分頁…"
        )
        self.ctx.update()

    def _finish_page_load(
        self,
        result,
        error: str | None,
        *,
        include_run: bool,
        run_id: str,
        status: str | None,
        page: int,
        background: bool,
    ) -> None:
        """Apply one page result, retrying only when the requested page became stale."""
        self._loading = False
        if error:
            self._set_controls_enabled(True)
            self._show_empty("候選載入失敗", error, keep_toolbar=True)
            return
        info, breakdown, total, rows = result
        if include_run and info is None:
            self._set_controls_enabled(True)
            self._show_empty("找不到批次", f"run_id：{run_id}", keep_toolbar=True)
            return
        self._apply_page_summary(breakdown)
        if info is not None:
            self.run_info = info or {}
            self._choose_initial_filter(self.counts)
            if self._status_query() != status:
                self.page = 1
                self._load_page(background=background)
                return
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if total and page > total_pages:
            self.page = total_pages
            self._load_page(background=background)
            return
        self.total = int(total or 0)
        self.rows = list(rows or [])
        self._row_cache.update({row.ordinal: row for row in self.rows})
        self.page = min(page, total_pages)
        self._refresh_filter_labels()
        self._set_controls_enabled(True)
        self._render_page()

    def _apply_page_summary(self, breakdown: dict | None) -> None:
        """Refresh cached status counts when the query returned a breakdown."""
        if breakdown is None:
            return
        status_counts = breakdown.get("status", {}) or {}
        self.counts = {
            str(key): int(value or 0) for key, value in status_counts.items()
        }
        self._summary_loaded = True

    def _summary_total(self) -> int:
        """回傳目前共用摘要對應篩選的總數。"""
        status = self._status_query()
        if status is None:
            return sum(max(0, int(count or 0)) for count in self.counts.values())
        return max(0, int(self.counts.get(status, 0) or 0))

    def _choose_initial_filter(self, counts: dict) -> None:
        """有可套用候選時預設看 ready，否則顯示全部狀態以解釋空批次。"""
        if self.status_filter != str(_BATCHABLE_STATUS):
            return
        if int(counts.get(str(_BATCHABLE_STATUS), 0) or 0) == 0:
            self.status_filter = ALL_FILTER
            self.filter_dd.value = ALL_FILTER

    def _status_query(self) -> str | None:
        return None if self.status_filter == ALL_FILTER else self.status_filter

    def _on_filter(self, event) -> None:
        """切換狀態篩選並清除跨篩選選取。"""
        if self._busy:
            return
        self.status_filter = str(event.control.value or ALL_FILTER)
        self.page = 1
        self._selected.clear()
        self._row_cache.clear()
        self._draft_validity_cache.clear()
        self._load_page(background=False)

    def _on_page(self, page: int) -> None:
        """換頁；選取保留在記憶體中，讓使用者可以跨頁勾選。"""
        if self._busy or self._loading or not self.run_id:
            return
        self.page = max(1, int(page))
        self._load_page(background=False)

    # ──────────────── 顯示 ────────────────

    def _refresh_filter_labels(self) -> None:
        """在候選篩選器上顯示各狀態的筆數。"""
        all_total = sum(max(0, int(count or 0)) for count in self.counts.values())
        pairs = [
            (
                key,
                f"{label}（{format_count(all_total if key == ALL_FILTER else self.counts.get(str(key), 0))}）",
            )
            for key, label in FILTERS
        ]
        kit.set_dropdown_options(self.filter_dd, pairs)
        self.filter_dd.value = self.status_filter

    def _render_page(self) -> None:
        """重畫目前頁、分頁器、選取摘要與批量按鈕。"""
        self.status_text.value = ""
        self._refresh_summary()
        self.pager.set_state(self.total, self.page)
        self.pager.visible = self.total > PAGE_SIZE
        if not self.rows:
            label = STATUS_LABEL.get(self.status_filter, self.status_filter)
            title = (
                "沒有可批量處理的候選" if self.total == 0 else f"「{label}」沒有候選"
            )
            self._show_empty(
                title, "可切換候選篩選，或重新整理批次狀態。", keep_toolbar=True
            )
            return
        controls = [self._candidate_card(row) for row in self.rows]
        self.body.controls = kit.rekey(controls, "smart-review-batch-row")
        self._update_action_buttons()
        self.ctx.update()

    def _refresh_summary(self) -> None:
        """更新批次識別、狀態統計與目前選取摘要。"""
        info = self.run_info
        if info:
            self.run_text.value = (
                f"批次 {str(info.get('id', ''))[:8]} · "
                f"{info.get('provider')}/{info.get('model')} · "
                f"{info.get('scope_version') or '-'} · {info.get('started_at') or '-'}"
            )
        parts = [
            f"{STATUS_LABEL.get(str(status), str(status))} {format_count(count)}"
            for status, count in self.counts.items()
            if count and str(status) in STATUS_LABEL
        ]
        self.summary_text.value = f"目前篩選共 {format_count(self.total)} 筆" + (
            "；" + " · ".join(parts) if parts else ""
        )
        selected = self._selected_rows()
        safe = [row for row in selected if self._can_apply(row)]
        override = [row for row in selected if self._can_override(row)]
        format_issues = len(
            [
                row
                for row in selected
                if self._can_select(row)
                and not self._can_apply(row)
                and not self._can_override(row)
            ]
        )
        text = f"已選 {format_count(len(selected))} 筆"
        if selected:
            text += f"；可套用 {format_count(len(safe))} 筆"
            if override:
                text += f"；可覆蓋已過期 {format_count(len(override))} 筆"
        if format_issues:
            text += f"；格式問題 {format_count(format_issues)} 筆會略過"
        self.selection_text.value = text

    def _candidate_card(self, row: SmartReviewCandidate) -> ft.Control:
        """建立一筆候選卡片；長文以三行摘要呈現，避免表格在小視窗爆版。"""
        can_select = self._can_select(row)
        can_apply = self._can_apply(row)
        can_override = self._can_override(row)
        selected = row.ordinal in self._selected
        status = STATUS_LABEL.get(str(row.status), str(row.status))
        tone = "em" if can_apply else "gold" if can_select else "neutral"
        chips: list[ft.Control] = [kit.chip(status, tone, dot=True)]
        if row.confidence is not None:
            chips.append(kit.chip(f"信心 {row.confidence:.0%}", "neutral"))
        if row.status == SmartReviewStatus.STALE and can_override:
            chips.append(kit.chip("可覆蓋・會更新 JSON 快照", "gold"))
        elif can_select and not can_apply:
            chips.append(
                kit.chip(
                    "格式問題・覆蓋會略過"
                    if row.status == SmartReviewStatus.STALE
                    else "格式問題・套用會略過",
                    "gold",
                )
            )
        if not can_select:
            chips.append(kit.chip(self._selection_reason(row), "neutral"))

        def toggle(event, ordinal=row.ordinal) -> None:
            if event.control.value:
                self._selected.add(ordinal)
            else:
                self._selected.discard(ordinal)
            self._refresh_summary()
            self._update_action_buttons()
            self.ctx.update()

        checkbox = ft.Checkbox(
            value=selected,
            on_change=toggle,
            disabled=self._busy or not can_select,
        )
        header = ft.Column(
            [
                ft.Text(
                    f"{row.mod_id}:{shorten(row.key, 76)}",
                    size=12.5,
                    weight=ft.FontWeight.BOLD,
                    selectable=True,
                    font_family=design.FONT_MONO,
                ),
                ft.Row(chips, spacing=5, wrap=True),
            ],
            spacing=5,
            tight=True,
        )
        details = ft.Column(
            [
                header,
                build_batch_text_line("原文", row.en_us),
                build_batch_text_line("目前", row.current_zh_tw),
                build_batch_text_line("建議", initial_draft_text(row), main=True),
                build_batch_text_line("AI 理由", reason_text(row)),
                build_batch_text_line("問題", issue_text(row)),
            ],
            spacing=5,
            tight=True,
            expand=True,
        )
        return ft.Container(
            content=ft.Row(
                [checkbox, details],
                spacing=7,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=ft.Padding.symmetric(horizontal=9, vertical=8),
            bgcolor=C.PANEL2 if not selected else C.PANEL,
            border=ft.Border.all(1, C.EM if selected else C.LINE),
            border_radius=design.RADIUS_CONTROL,
        )

    def show_run_loading(self) -> None:
        """對話框正在讀取共用批次摘要：先顯示讀取中，並讓先前批次晚到的結果作廢。"""
        self._request_seq += 1
        self._show_loading("正在載入批次…")

    def _show_loading(self, text: str) -> None:
        """顯示讀取中狀態並暫停批量動作。"""
        self.body.controls = [kit.loading_state(text)]
        self._set_controls_enabled(False)
        self.ctx.update()

    def _show_empty(
        self, title: str, subtitle: str, *, keep_toolbar: bool = False
    ) -> None:
        """顯示空狀態；保留工具列讓使用者仍可切換篩選或重新整理。"""
        self.body.controls = [
            kit.empty_state(title, subtitle, icon=ft.Icons.LAYERS_OUTLINED)
        ]
        if not keep_toolbar:
            self._set_controls_enabled(False)
        self._update_action_buttons()
        self.ctx.update()

    # ──────────────── 選取 ────────────────

    def _selected_rows(self) -> list[SmartReviewCandidate]:
        """依穩定 ordinal 取回跨頁選取的候選。"""
        return [
            self._row_cache[ordinal]
            for ordinal in sorted(self._selected)
            if ordinal in self._row_cache
        ]

    def _select_page(self) -> None:
        """選取目前頁所有可進入批量流程的 ready／stale 候選。"""
        if self._busy:
            return
        self._selected.update(row.ordinal for row in self.rows if self._can_select(row))
        self._refresh_summary()
        self._update_action_buttons()
        self._render_page()

    def _select_all_safe(self) -> None:
        """背景載入目前篩選的完整候選，只選取格式檢查通過的 ready 項目。"""
        if self._busy or self._loading or not self.run_id:
            return
        database = self.ctx.database()
        if database is None:
            return
        path, priority = database
        run_id = self.run_id
        status = self._status_query()
        token = self._request_seq = self._request_seq + 1
        self._loading = True
        self._set_controls_enabled(False)
        self.status_text.value = "正在載入全部候選並篩選可套用項目…"
        self.ctx.update()

        def work():
            with moddb_smart_review_read_session(path, priority) as session:
                _total, rows = session.candidate_page(
                    run_id, status=status, limit=None, offset=0
                )
            return list(rows or [])

        def done(rows, error: str | None) -> None:
            if token != self._request_seq:
                return
            self._loading = False
            self._set_controls_enabled(True)
            if error:
                show_snack(self.ctx.page, f"全批次載入失敗：{error}", C.RED)
                self._render_page()
                return
            self._row_cache.update({row.ordinal: row for row in rows})
            self._selected.update(row.ordinal for row in rows if self._can_apply(row))
            self._refresh_summary()
            self._update_action_buttons()
            self._render_page()
            show_snack(
                self.ctx.page,
                f"已選取全部可套用候選，共 {format_count(len(self._selected))} 筆。",
                C.EM,
            )

        self.ctx.run_background(
            work,
            done,
            name="選取全部智慧校對候選",
            still_valid=lambda: token == self._request_seq,
        )

    def _select_all_override(self) -> None:
        """背景載入所有 stale 候選，只選取可安全覆蓋的項目。"""
        if self._busy or self._loading or not self.run_id:
            return
        database = self.ctx.database()
        if database is None:
            return
        path, priority = database
        run_id = self.run_id
        # 把畫面切到 stale 篩選並清掉其他篩選下的選取，避免使用者看不到
        # 即將覆蓋的候選，或誤把 ready 候選一起帶入危險動作。
        self.status_filter = str(SmartReviewStatus.STALE)
        self.filter_dd.value = self.status_filter
        self.page = 1
        self._selected.clear()
        self._row_cache.clear()
        self._draft_validity_cache.clear()
        token = self._request_seq = self._request_seq + 1
        self._loading = True
        self._set_controls_enabled(False)
        self.status_text.value = "正在載入全部已過期候選並篩選可覆蓋項目…"
        self.ctx.update()

        def work():
            with moddb_smart_review_read_session(path, priority) as session:
                _total, rows = session.candidate_page(
                    run_id,
                    status=SmartReviewStatus.STALE,
                    limit=None,
                    offset=0,
                )
            return list(rows or [])

        def done(rows, error: str | None) -> None:
            if token != self._request_seq:
                return
            self._loading = False
            self._set_controls_enabled(True)
            if error:
                show_snack(self.ctx.page, f"過期候選載入失敗：{error}", C.RED)
                self._render_page()
                return
            self._row_cache.update({row.ordinal: row for row in rows})
            self._selected.update(
                row.ordinal for row in rows if self._can_override(row)
            )
            self._refresh_summary()
            self._update_action_buttons()
            self._render_page()
            show_snack(
                self.ctx.page,
                f"已選取全部可覆蓋候選，共 {format_count(sum(self._can_override(row) for row in rows))} 筆。",
                C.GOLD,
            )

        self.ctx.run_background(
            work,
            done,
            name="選取全部已過期智慧校對候選",
            still_valid=lambda: token == self._request_seq,
        )

    def _clear_selection(self) -> None:
        """清除所有頁面的選取。"""
        if self._busy:
            return
        self._selected.clear()
        self._refresh_summary()
        self._update_action_buttons()
        self._render_page()

    def _can_select(self, row: SmartReviewCandidate) -> bool:
        """可進入批量選取的 ready 或 stale 候選。"""
        return self._can_ready_select(row) or self._can_stale_select(row)

    @staticmethod
    def _can_ready_select(row: SmartReviewCandidate) -> bool:
        """ready 候選只要有 AI 建議即可選取，格式問題交給動作按鈕略過。"""
        return row.status == _BATCHABLE_STATUS and bool(initial_draft_text(row).strip())

    @staticmethod
    def _can_stale_select(row: SmartReviewCandidate) -> bool:
        """stale 候選只允許有草稿且未標記人工審核保護的項目。"""
        return (
            row.status == SmartReviewStatus.STALE
            and row.validation_error != "manual_reviewed_requires_manual_edit"
            and bool(initial_draft_text(row).strip())
        )

    def _can_apply(self, row: SmartReviewCandidate) -> bool:
        """批量套用再加一道純本地格式診斷，避免把已知問題送進寫入流程。"""
        if not self._can_ready_select(row):
            return False
        return self._draft_has_valid_format(row)

    def _can_override(self, row: SmartReviewCandidate) -> bool:
        """stale 候選需通過本地格式診斷，才可進入明確覆蓋動作。"""
        if not self._can_stale_select(row):
            return False
        return self._draft_has_valid_format(row)

    def _draft_has_valid_format(self, row: SmartReviewCandidate) -> bool:
        """快取同一原文／草稿的純本地格式診斷，避免重畫時重複解析。"""
        draft = initial_draft_text(row)
        cache_key = (row.kind, row.en_us, draft)
        if cache_key in self._draft_validity_cache:
            return self._draft_validity_cache[cache_key]
        try:
            valid = not SmartReviewEvidencePresenter.diagnose_draft(
                row, draft
            ).has_issue
        except (TypeError, ValueError):
            valid = False
        self._draft_validity_cache[cache_key] = valid
        return valid

    @staticmethod
    def _selection_reason(row: SmartReviewCandidate) -> str:
        """回傳停用勾選框的簡短原因。"""
        if row.status == SmartReviewStatus.STALE:
            if row.validation_error == "manual_reviewed_requires_manual_edit":
                return "已有人工審核，不批量覆蓋"
            if not initial_draft_text(row).strip():
                return "沒有可覆蓋草稿"
        if row.status != _BATCHABLE_STATUS:
            return STATUS_LABEL.get(str(row.status), "非可套用狀態")
        return "沒有建議譯文"

    # ──────────────── 批量動作 ────────────────

    def _update_action_buttons(self) -> None:
        """依選取與格式檢查結果更新批量按鈕文字和可用狀態。"""
        selected = self._selected_rows()
        apply_rows = [row for row in selected if self._can_apply(row)]
        reject_rows = [row for row in selected if self._can_ready_select(row)]
        override_rows = [row for row in selected if self._can_override(row)]
        self.apply_btn.content = f"批量套用（{len(apply_rows):,}）"
        self.reject_btn.content = f"批量保留原譯文（{len(reject_rows):,}）"
        self.override_btn.content = f"批量覆蓋已過期（{len(override_rows):,}）"
        enabled = not self._busy and not self._loading
        self.apply_btn.disabled = not enabled or not apply_rows
        self.reject_btn.disabled = not enabled or not reject_rows
        self.override_btn.disabled = not enabled or not override_rows
        self.select_page_btn.disabled = not enabled or not any(
            self._can_select(row) for row in self.rows
        )
        self.select_all_btn.disabled = not enabled or not self.total
        self.select_override_all_btn.disabled = not enabled or not self.counts.get(
            str(SmartReviewStatus.STALE), 0
        )
        self.clear_btn.disabled = not enabled or not self._selected
        self.refresh_btn.disabled = self._busy or self._loading or not self.run_id
        self.filter_dd.disabled = self._busy or self._loading or not self.run_id

    def _confirm_action(self, action: str) -> None:
        """顯示批量寫入前的明確確認，避免誤按一次就處理整批。"""
        selected = self._selected_rows()
        if action == "apply":
            candidates = [row for row in selected if self._can_apply(row)]
        elif action == "reject":
            candidates = [row for row in selected if self._can_ready_select(row)]
        elif action == "override":
            candidates = [row for row in selected if self._can_override(row)]
        else:
            raise ValueError(f"未知的智慧校對批量動作：{action}")
        if not candidates:
            show_snack(self.ctx.page, "目前沒有可執行的選取項目。", C.GOLD)
            return
        if not callable(getattr(self.ctx.page, "show_dialog", None)):
            self._start_batch(action, candidates)
            return
        label = {
            "apply": "套用 AI 建議",
            "reject": "保留原譯文",
            "override": "覆蓋已過期候選",
        }[action]
        skipped = len(selected) - len(candidates)
        detail = f"即將{label} {len(candidates):,} 筆候選。"
        if skipped:
            detail += f"另有 {skipped:,} 筆不符合此動作，會略過。"
        warning = (
            "會先把 JSON 的 current_zh_tw 快照更新為資料庫目前譯文，再以候選草稿／AI 建議覆蓋；"
            "資料庫在對齊後再次變更，或目前譯文已人工審核的項目，會安全略過。"
            if action == "override"
            else "執行前仍會重新檢查資料庫快照；已被其他操作修改的候選不會被覆蓋。"
        )
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER, color=C.GOLD),
                    ft.Text(f"確認批量{label}"),
                ],
                spacing=8,
            ),
            content=ft.Column(
                [
                    ft.Text(detail, selectable=True),
                    ft.Text(
                        warning,
                        size=12,
                        color=C.MUTED,
                        selectable=True,
                    ),
                ],
                tight=True,
                spacing=10,
            ),
            actions=[
                ft.TextButton(
                    "返回檢查",
                    on_click=lambda _e: self._dismiss_confirmation(),
                ),
                kit.button(
                    f"確認{label}",
                    "danger" if action == "override" else "primary",
                    icon=ft.Icons.WARNING_AMBER
                    if action == "override"
                    else ft.Icons.CHECK,
                    on_click=lambda _e: self._confirm_and_start(action, candidates),
                ),
            ],
        )
        self._confirmation = dialog
        self.ctx.page.show_dialog(dialog)

    def _confirm_and_start(
        self, action: str, candidates: list[SmartReviewCandidate]
    ) -> None:
        """關閉確認視窗並啟動背景批量操作。"""
        self._dismiss_confirmation()
        self._start_batch(action, candidates)

    def _dismiss_confirmation(self) -> None:
        """關閉批量確認視窗。"""
        self._confirmation = None
        pop_dialog = getattr(self.ctx.page, "pop_dialog", None)
        if callable(pop_dialog):
            pop_dialog()

    def _start_batch(self, action: str, candidates: list[SmartReviewCandidate]) -> None:
        """以部分成功策略執行批量候選，並在 UI 執行緒更新進度。"""
        database = self.ctx.database()
        if database is None or self._busy:
            return
        path, priority = database
        self._busy = True
        self._cancel_event.clear()
        self._operation_seq += 1
        operation = self._operation_seq
        self.progress_bar.visible = True
        self.progress_bar.value = 0.0
        self.body.disabled = True
        self.cancel_btn.visible = True
        self.cancel_btn.disabled = False
        action_label = {
            "apply": "套用",
            "reject": "保留",
            "override": "覆蓋已過期",
        }.get(action, action)
        self.status_text.value = f"準備{action_label} {len(candidates):,} 筆…"
        self._update_action_buttons()
        self.ctx.update()

        progress = SmartReviewBatchProgress(self.ctx, operation, self._apply_progress)

        def work() -> SmartReviewBatchResult:
            return apply_moddb_smart_review_batch_from_path(
                path,
                priority,
                candidates,
                action=action,
                progress=progress,
                cancel_check=self._cancel_event.is_set,
            )

        def done(result: SmartReviewBatchResult | None, error: str | None) -> None:
            if operation != self._operation_seq:
                return
            self._busy = False
            self.progress_bar.visible = False
            self.body.disabled = False
            self.cancel_btn.visible = False
            if error:
                self.status_text.value = f"批量操作失敗：{error}"
                self.status_text.color = C.RED
                self._update_action_buttons()
                self.ctx.update()
                return
            self._last_result = result
            self._selected.clear()
            self._row_cache.clear()
            self._draft_validity_cache.clear()
            self._render_result(result)
            if summarize_outcomes(result).succeeded and callable(self.on_changed):
                self.on_changed()
            self._load_page(include_run=True)

        self.ctx.run_background(
            work,
            done,
            name="執行智慧校對批量審查",
            commit=CommitPolicy.PARTIAL_ALLOWED,
            durability=DurabilityPolicy.USER_ACTION,
            stable_identity=True,
            still_valid=lambda: operation == self._operation_seq,
        )

    def _apply_progress(self, operation: int, snapshot: dict) -> None:
        """把 worker 的單筆進度安全套用到畫面。"""
        if operation != self._operation_seq or not self._busy:
            return
        processed = int(snapshot.get("processed", 0) or 0)
        total = max(1, int(snapshot.get("total", 0) or 0))
        item = snapshot.get("item")
        status = getattr(item, "status", "")
        self.progress_bar.value = min(1.0, processed / total)
        self.status_text.value = f"已處理 {processed:,} / {total:,} 筆"
        if status not in SUCCESS_OUTCOMES:
            self.status_text.value += (
                f"；本筆結果：{STATUS_LABEL.get(str(status), str(status))}"
            )
        self.ctx.update()

    def _cancel(self) -> None:
        """要求在目前項目完成後停止後續候選。"""
        if not self._busy:
            return
        self._cancel_event.set()
        self.cancel_btn.disabled = True
        self.status_text.value = "已要求停止；目前項目完成後不再處理下一筆。"
        self.status_text.color = C.GOLD
        self.ctx.update()

    def _render_result(self, result: SmartReviewBatchResult) -> None:
        """顯示成功、略過、格式問題、錯誤與取消的批量結果。"""
        summary = summarize_outcomes(result)
        applied, rejected = summary.applied, summary.rejected
        stale, errors = summary.skipped, summary.error
        parts = [f"完成 {summary.total:,} / {result.requested:,} 筆"]
        if applied:
            parts.append(f"已套用 {applied:,}")
        if rejected:
            parts.append(f"已保留原譯文 {rejected:,}")
        if stale:
            parts.append(f"略過 {stale:,}")
        if errors:
            parts.append(f"失敗 {errors:,}")
        if result.cancelled:
            parts.append("已停止後續項目")
        self.result_text.value = "批量結果：" + "；".join(parts) + "。"
        self.result_text.color = C.GOLD if result.cancelled or stale or errors else C.EM
        self.result_box.visible = True
        self.status_text.value = "批量操作完成。"
        self.status_text.color = C.MUTED

    # ──────────────── 狀態 ────────────────

    def _set_controls_enabled(self, enabled: bool) -> None:
        """在唯讀載入期間鎖定篩選與批量控制，但保留結果畫面。"""
        self.body.disabled = not enabled
        if not enabled:
            self.filter_dd.disabled = True
            self.refresh_btn.disabled = True
            self.select_page_btn.disabled = True
            self.select_all_btn.disabled = True
            self.select_override_all_btn.disabled = True
            self.clear_btn.disabled = True
            self.apply_btn.disabled = True
            self.reject_btn.disabled = True
            self.override_btn.disabled = True
            return
        self._update_action_buttons()
