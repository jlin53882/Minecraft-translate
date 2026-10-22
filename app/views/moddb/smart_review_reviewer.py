"""智慧校對視窗的「審查」分頁：每頁一筆的逐筆審查（套用／保留原譯文／存草稿）。

本模組是 Flet 控制項與事件的串接層（controller + view）：畫面狀態在
``SmartReviewReviewerState``，候選讀取與預載在 ``SmartReviewCandidateLoader``，證據與格式診斷的
文字在 ``SmartReviewEvidencePresenter``，寫入動作在 ``SmartReviewActionService``。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    SmartReviewCandidate,
    SmartReviewStatus,
)
from app.tasks.operation_registry import OperationPresentation
from app.ui import design, kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.formatting import format_count
from app.views.moddb.smart_review_actions import (
    DRAFT_SAVED,
    ActionOutcome,
    SmartReviewActionService,
)
from app.views.moddb.smart_review_candidate_loader import SmartReviewCandidateLoader
from app.views.moddb.smart_review_context import SmartReviewContext
from app.views.moddb.smart_review_evidence import (
    SmartReviewEvidencePresenter,
    initial_draft_text,
    is_editable_row,
    is_manual_edit_row,
    manual_edit_hint,
    not_applicable_reason,
)
from app.views.moddb.smart_review_reason import build_ai_reason_panel
from app.views.moddb.smart_review_reviewer_state import (
    DEFAULT_FILTER,
    FILTERS,
    STATUS_LABEL,
    SmartReviewReviewerState,
)

_STATUS_TONE = {
    SmartReviewStatus.READY: "em",
    SmartReviewStatus.APPLIED: "dia",
    SmartReviewStatus.REJECTED: "neutral",
    SmartReviewStatus.NEEDS_CONTEXT: "gold",
    SmartReviewStatus.NO_CHANGE: "neutral",
    SmartReviewStatus.INVALID: "red",
    SmartReviewStatus.INVALID_FORMAT: "red",
    SmartReviewStatus.STALE: "gold",
}
_DECISION_LABEL = {
    "no_change": "維持不變",
    "suggest_revision": "建議修改",
    "needs_context": "需補 context",
}
_FORMAT_TONE_COLOR = {"muted": C.MUTED, "gold": C.GOLD, "red": C.RED, "em": C.EM}


class SmartReviewReviewer:
    """逐筆審查某個校對批次：每頁只顯示一筆，翻頁就是換下一筆。"""

    def __init__(self, ctx: SmartReviewContext, *, on_changed) -> None:
        """建立審查面板；``on_changed`` 在候選被套用後通知外層重新整理。"""
        self.ctx = ctx
        self.on_changed = on_changed
        self.state = SmartReviewReviewerState()
        self.loader = SmartReviewCandidateLoader()
        self.actions = SmartReviewActionService(ctx)
        self._build_controls()
        self.body = ft.Column([], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self.control = ft.Column(
            [self._toolbar(), self.body],
            spacing=12,
            expand=True,
        )
        self._show_empty(
            "尚未載入批次", "到「執行」分頁開始校對，或到「歷史批次」開啟既有批次。"
        )

    # ──────────────── 控制項 ────────────────

    def _build_controls(self) -> None:
        """建立工具列、導覽與動作按鈕。"""
        self.filter_dd = kit.dropdown(
            label="審查篩選", dense=True, width=240, on_select=self._on_filter
        )
        kit.set_dropdown_options(self.filter_dd, list(FILTERS))
        self.filter_dd.value = DEFAULT_FILTER
        self.position_input = kit.text_field(
            value="",
            width=72,
            tooltip="輸入筆次後按 Enter 跳轉",
            on_submit=self._on_position_submit,
        )
        self.position_input.text_align = ft.TextAlign.CENTER
        self.position_suffix = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self.position_control = ft.Row(
            [
                ft.Text("目前", size=11.5, color=C.DIM),
                ft.Text("第", size=13, color=C.MUTED),
                self.position_input,
                self.position_suffix,
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.position_control = ft.Container(
            content=self.position_control,
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE2),
            border_radius=design.RADIUS_CONTROL,
        )
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
        self.progress_bar = kit.progress_bar(0.0, "em", height=7)
        self.prev_btn = kit.button(
            "上一筆",
            "secondary",
            icon=ft.Icons.ARROW_BACK,
            on_click=lambda _e: self._move(-1),
            tooltip="載入上一筆候選",
        )
        self.next_btn = kit.button(
            "下一筆",
            "secondary",
            icon=ft.Icons.ARROW_FORWARD,
            on_click=lambda _e: self._move(1),
            tooltip="載入下一筆候選",
        )
        self.reject_btn = kit.button(
            "保留原譯文", "secondary", icon=ft.Icons.RESTORE, on_click=self._reject
        )
        self.save_btn = kit.button(
            "儲存草稿", "secondary", icon=ft.Icons.SAVE_OUTLINED, on_click=self._save
        )
        self.apply_btn = kit.button(
            "套用這筆", "primary", icon=ft.Icons.CHECK, on_click=self._apply
        )
        self.hint_text = ft.Text("", size=12, color=C.GOLD, selectable=True)
        self.format_text = ft.Text("", size=12, color=C.MUTED, selectable=True)
        self.draft = kit.text_field(
            "人工修正草稿（可編輯）",
            multiline=True,
            min_lines=8,
            max_lines=14,
            on_change=self._on_draft_change,
        )

    def _status_panel(self) -> ft.Control:
        """建立批次狀態摘要卡，集中呈現統計與批次識別資訊。"""
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INSIGHTS_OUTLINED, size=17, color=C.DIA),
                            ft.Text(
                                "批次狀態",
                                size=11.5,
                                weight=ft.FontWeight.W_700,
                                color=C.MUTED,
                            ),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.summary_text,
                    self.run_text,
                ],
                spacing=5,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
        )

    def _toolbar(self) -> ft.Control:
        """建立篩選、位置、狀態摘要與進度列。"""
        navigation = ft.Row(
            [
                self.filter_dd,
                ft.Row([self.prev_btn, self.next_btn], spacing=8, wrap=True),
                self.position_control,
            ],
            spacing=10,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        content = ft.Column(
            [
                ft.Text("依狀態檢視候選，完成後再套用", size=11.5, color=C.MUTED),
                navigation,
                self._status_panel(),
                ft.Container(
                    content=self.progress_bar,
                    padding=ft.Padding.only(left=2, right=2, top=1),
                ),
            ],
            spacing=12,
        )
        return kit.section_card(
            "逐筆審查",
            content,
            icon=ft.Icons.RATE_REVIEW_OUTLINED,
            tone="em",
            badge=kit.chip("人工確認", "dia"),
            collapsible=True,
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
        )

    # ──────────────── 載入 ────────────────

    def load_run(self, run_id: str) -> None:
        """載入指定批次的資訊與狀態統計，之後顯示第一筆。"""
        database = self.ctx.database()
        if database is None:
            self._show_empty("沒有可用的資料庫", "請先在條目校對選擇資料庫。")
            return
        path, priority = database
        state = self.state
        state.run_id = run_id
        self.loader.invalidate()
        token = state.begin_request()
        self._show_loading("正在載入批次…")

        def done(result, error: str | None) -> None:
            """決定初始篩選並顯示第一筆。"""
            if error:
                self._show_empty("批次載入失敗", error)
                return
            if result.info is None:
                state.run_id = None
                self._show_empty("找不到批次", f"run_id：{run_id}")
                return
            state.run_info = result.info
            state.counts = result.counts
            state.status_filter = state.prefer_ready_filter()
            self.filter_dd.value = state.status_filter
            self._refresh_filter_labels()
            state.position = 0
            self.load_position()

        self.ctx.run_background(
            lambda: self.loader.load_run(path, priority, run_id),
            done,
            name="載入智慧校對批次",
            still_valid=lambda: state.is_current(token),
        )

    def load_position(
        self, *, refresh_total: bool = True, refresh_counts: bool = False
    ) -> None:
        """依目前篩選與位置載入單筆候選（每頁一筆）。

        ``refresh_counts`` 在套用或保留之後使用：狀態統計與候選在同一個唯讀連線讀取。
        """
        database = self.ctx.database()
        state = self.state
        if database is None or not state.run_id:
            return
        path, priority = database
        run_id = state.run_id
        status = state.status_query
        position = max(0, state.position)
        known_total = None if refresh_total else state.total
        identity = self.ctx.db_identity()
        token = state.begin_request()
        self.set_busy(True)
        self.ctx.update()

        def done(result, error: str | None) -> None:
            """顯示該筆；位置超出範圍時退回最後一筆。"""
            self.set_busy(False)
            if error:
                self._show_empty("候選載入失敗", error)
                return
            state.total = result.total
            state.position = state.clamp_position(position, result.total)
            state.row = result.row
            if result.counts is not None:
                state.counts = result.counts
                self._refresh_filter_labels()
            self.loader.remember(run_id, status, identity, position, result.window)
            self._render()
            self._preload()

        self.ctx.run_background(
            lambda: self.loader.read_position(
                path,
                priority,
                run_id,
                status=status,
                position=position,
                known_total=known_total,
                with_counts=refresh_counts,
            ),
            done,
            name="載入智慧校對候選",
            still_valid=lambda: state.is_current(token),
        )

    def _preload(self) -> None:
        """在背景預載目前位置前後各一筆，讓下一次換筆不必再讀資料庫。"""
        database, state = self.ctx.database(), self.state
        if database is None or state.row is None or not state.run_id:
            return
        run_id, status, position = state.run_id, state.status_query, state.position
        identity = self.ctx.db_identity()
        if not self.loader.missing_neighbors(
            run_id, status, identity, position, state.total
        ):
            return
        path, priority = database
        total = state.total
        token = state.begin_request()

        def done(window, error: str | None) -> None:
            """預載失敗時不提示：換筆時會改走一般載入。"""
            if not error:
                self.loader.remember(run_id, status, identity, position, window)

        self.ctx.run_background(
            lambda: self.loader.read_window(
                path,
                priority,
                run_id,
                status=status,
                position=position,
                known_total=total,
            ),
            done,
            name="預載智慧校對候選",
            presentation=OperationPresentation.MAINTENANCE,
            still_valid=lambda: state.is_current(token),
        )

    # ──────────────── 顯示 ────────────────

    def _refresh_filter_labels(self) -> None:
        """在篩選器選項上顯示各狀態的筆數。"""
        state = self.state
        counts = state.counts
        total = state.total_count
        pairs = [
            (
                key,
                f"{label}（{format_count(total if key == 'all' else counts.get(key, 0))}）",
            )
            for key, label in FILTERS
        ]
        kit.set_dropdown_options(self.filter_dd, pairs)
        parts = [
            f"{label} {format_count(counts[key])}"
            for key, label in FILTERS[:-1]
            if counts.get(key)
        ]
        self.summary_text.value = "批次狀態：" + (" · ".join(parts) or "沒有候選")
        info = state.run_info
        if info:
            self.run_text.value = (
                f"批次 {str(info.get('id', ''))[:8]} · "
                f"{info.get('provider')}/{info.get('model')} · "
                f"{info.get('scope_version') or '-'} · {info.get('started_at') or '-'}"
            )

    def _show_empty(self, title: str, subtitle: str = "") -> None:
        """顯示空狀態，並停用所有動作。"""
        self.state.row = None
        self.position_input.value = ""
        self.position_suffix.value = ""
        self.progress_bar.value = 0.0
        self.body.controls = [
            kit.empty_state(title, subtitle, icon=ft.Icons.RATE_REVIEW_OUTLINED)
        ]
        self._set_actions_enabled(False, None)
        self.ctx.update()

    def _show_loading(self, text: str) -> None:
        """顯示讀取中狀態。"""
        self.body.controls = [kit.loading_state(text)]
        self._set_actions_enabled(False, None)
        self.ctx.update()

    def _render(self) -> None:
        """顯示目前候選，或在篩選結果為空時說明下一步。"""
        state = self.state
        row = state.row
        if row is None:
            label = STATUS_LABEL.get(state.status_filter, state.status_filter)
            self._show_empty(
                "可套用的候選都處理完了"
                if state.ready_queue_finished()
                else f"「{label}」沒有候選",
                "可以切換審查篩選查看其他狀態。",
            )
            return
        self.position_input.value = str(state.position + 1)
        self.position_suffix.value = (
            f" / {state.total:,} 筆"
            f"（{STATUS_LABEL.get(state.status_filter, state.status_filter)}）"
        )
        self.progress_bar.value = (
            (state.position + 1) / state.total if state.total else 0
        )
        state.stored_draft = initial_draft_text(row)
        self.draft.value = state.stored_draft
        self.draft.read_only = not is_editable_row(row)
        self.body.controls = [self._header(row), self._columns(row)]
        self._refresh_format()
        self.ctx.update()

    def _header(self, row: SmartReviewCandidate) -> ft.Control:
        """建立候選標題：key、狀態、決策與信心。"""
        chips: list[ft.Control] = [
            kit.chip(
                STATUS_LABEL.get(row.status, row.status),
                _STATUS_TONE.get(row.status, "neutral"),
                dot=True,
            ),
            kit.chip(row.mc_version, "dia"),
            kit.chip(row.kind, "neutral"),
        ]
        if row.decision:
            chips.append(
                kit.chip(_DECISION_LABEL.get(row.decision, row.decision), "gold")
            )
        if row.confidence is not None:
            chips.append(kit.chip(f"信心 {row.confidence:.0%}", "neutral"))
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Icon(
                                    ft.Icons.TRANSLATE_OUTLINED,
                                    size=18,
                                    color=C.DIA,
                                ),
                                width=34,
                                height=34,
                                alignment=ft.Alignment.CENTER,
                                bgcolor=C.DIA_BG,
                                border_radius=10,
                            ),
                            ft.Column(
                                [
                                    ft.Text("目前條目", size=11, color=C.MUTED),
                                    ft.Text(
                                        f"{row.mod_id}:{row.key}",
                                        size=13,
                                        selectable=True,
                                        font_family=design.FONT_MONO,
                                    ),
                                ],
                                spacing=2,
                                tight=True,
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(chips, spacing=6, wrap=True),
                ],
                spacing=10,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CARD,
        )

    @staticmethod
    def _text_card(title: str, value: str, tone: str = "neutral") -> ft.Control:
        """建立「標題 + 可選取文字」的卡片。"""
        color = {"gold": C.GOLD, "dia": C.DIA, "em": C.EM}.get(tone, C.MUTED)
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=11.5, color=color),
                    ft.Text(value or "（無內容）", size=13, selectable=True),
                ],
                spacing=6,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=11),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=10,
        )

    def _source_label(self, row: SmartReviewCandidate) -> str:
        """回傳目前生效來源的顯示名稱。"""
        db = self.ctx.get_db()
        catalog = getattr(db, "source_catalog", None)
        if row.effective_source is None:
            return "無"
        return (
            catalog.label(row.effective_source)
            if catalog
            else str(row.effective_source)
        )

    def _columns(self, row: SmartReviewCandidate) -> ft.Control:
        """建立 AI 說明與左右檢視欄，讓理由不必捲到頁面下方才看得到。"""
        left = ft.Column(
            [
                self._text_card("英文原文", row.en_us, "dia"),
                self._text_card(
                    f"目前譯文（來源：{self._source_label(row)} · 審核：{row.review_status or '未標記'}）",
                    row.current_zh_tw,
                    "gold",
                ),
                self._text_card("AI 建議譯文", row.suggested_zh_tw or "", "em"),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        right = ft.Column(
            [
                self.draft,
                self.format_text,
                self.hint_text,
                ft.Row(
                    [self.reject_btn, self.save_btn, self.apply_btn],
                    spacing=8,
                    wrap=True,
                ),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        return ft.Column(
            [
                build_ai_reason_panel(row),
                ft.ResponsiveRow(
                    [
                        ft.Container(left, col={"xs": 12, "md": 6}),
                        ft.Container(right, col={"xs": 12, "md": 6}),
                    ],
                    spacing=14,
                    run_spacing=14,
                ),
            ],
            spacing=14,
        )

    # ──────────────── 草稿與格式診斷 ────────────────

    def _on_draft_change(self, _e=None) -> None:
        """草稿被編輯時重新診斷格式並更新按鈕狀態。"""
        self._refresh_format()
        self.ctx.update()

    def _refresh_format(self) -> None:
        """依目前草稿計算格式診斷，並決定能否儲存與套用。"""
        state = self.state
        row = state.row
        if row is None:
            self._set_actions_enabled(False, None)
            return
        diagnosis = SmartReviewEvidencePresenter.diagnose_draft(
            row, str(self.draft.value or "")
        )
        self.format_text.value = diagnosis.text
        self.format_text.color = _FORMAT_TONE_COLOR[diagnosis.tone]
        state.has_format_issue = diagnosis.has_issue
        self._set_actions_enabled(True, row)

    def _set_actions_enabled(
        self, has_row: bool, row: SmartReviewCandidate | None
    ) -> None:
        """依候選狀態與格式診斷設定按鈕，並說明不能操作的原因。"""
        state = self.state
        manual = has_row and is_manual_edit_row(row)
        reason = not_applicable_reason(row) if row is not None else None
        ready = has_row and row is not None and reason is None
        if manual:
            self._set_manual_edit_actions(row)
            reason = manual_edit_hint(row)
            if state.has_format_issue:
                reason = "草稿有格式問題或是空白：請先修正才能套用。"
        else:
            self.apply_btn.content = "套用這筆"
            if ready and state.has_format_issue:
                reason = "草稿有格式問題：請先修正，否則套用會被拒絕並鎖定這筆。"
            usable = ready and not state.has_format_issue and not state.busy
            self.apply_btn.disabled = not usable
            self.save_btn.disabled = not usable
            self.reject_btn.disabled = not (ready and not state.busy)
        self.prev_btn.disabled = not has_row or state.position <= 0 or state.busy
        self.next_btn.disabled = (
            not has_row or state.position >= state.total - 1 or state.busy
        )
        self.position_input.disabled = not has_row or state.busy
        self.filter_dd.disabled = state.busy or not state.run_id
        self.hint_text.value = reason or ""

    def _set_manual_edit_actions(self, row: SmartReviewCandidate) -> None:
        """人工編輯模式只開放套用；草稿有內容且格式正確才能按，存草稿與保留原譯文不適用。"""
        state = self.state
        self.apply_btn.content = (
            "重新套用" if row.status == SmartReviewStatus.APPLIED else "套用修改"
        )
        self.apply_btn.disabled = state.busy or state.has_format_issue
        self.save_btn.disabled = True
        self.reject_btn.disabled = True

    def set_busy(self, busy: bool) -> None:
        """切換忙碌狀態並同步按鈕可用性。"""
        self.state.busy = busy
        self._set_actions_enabled(self.state.row is not None, self.state.row)

    # ──────────────── 導覽 ────────────────

    def _on_filter(self, event) -> None:
        """切換審查篩選並回到第一筆。"""
        state = self.state
        if state.busy:
            return
        state.status_filter = str(event.control.value or "all")
        state.position = 0
        self.loader.invalidate()
        self._flush_then(self.load_position)

    def _move(self, delta: int) -> None:
        """移到上一筆或下一筆；到頭時說明原因。"""
        state = self.state
        if state.busy:
            return
        target = state.position + delta
        if target < 0 or target >= state.total:
            show_snack(
                self.ctx.page,
                "已經是最後一筆。" if delta > 0 else "已經是第一筆。",
                C.DIA,
                duration=2200,
            )
            return
        self._flush_then(lambda: self._go_to(target))

    def _on_position_submit(self, event) -> None:
        """解析手動輸入的 1-based 筆次，完成驗證後跳到指定候選。"""
        state = self.state
        if state.busy:
            return
        raw = str(event.control.value or "").strip()
        try:
            target = int(raw)
        except (TypeError, ValueError):
            target = 0
        if state.total <= 0 or not state.row:
            self._restore_position_input()
            return
        if target < 1 or target > state.total:
            show_snack(
                self.ctx.page,
                f"請輸入 1～{state.total:,} 的筆次。",
                C.GOLD,
                duration=2200,
            )
            self._restore_position_input()
            return
        if target - 1 == state.position:
            self._restore_position_input()
            return
        self._flush_then(lambda: self._go_to(target - 1))

    def _go_to(self, position: int) -> None:
        """換到指定位置：已預載就直接顯示，否則在背景載入。"""
        state = self.state
        cached = self.loader.take(
            state.run_id, state.status_query, self.ctx.db_identity(), position
        )
        state.position = position
        if cached is None:
            self.load_position(refresh_total=False)
            return
        state.begin_request()
        state.row = cached
        self._render()
        self._preload()

    def _restore_position_input(self) -> None:
        """把筆次輸入框還原成目前實際顯示的位置。"""
        self.position_input.value = (
            str(self.state.position + 1) if self.state.row else ""
        )
        self.ctx.update()

    def _flush_then(self, then) -> None:
        """換筆前保存草稿；格式錯誤或空白草稿會停留在目前候選。"""
        state = self.state
        draft = str(self.draft.value or "")
        if not state.draft_changed(draft):
            then()
            return
        if not draft.strip() or state.has_format_issue:
            show_snack(
                self.ctx.page,
                "草稿尚未保存：請先修正格式問題或填入內容。",
                C.GOLD,
                duration=2600,
            )
            return
        self._write_draft(
            lambda status: then() if status == DRAFT_SAVED else None,
            quiet=True,
        )

    # ──────────────── 動作 ────────────────

    def _save(self, _e=None) -> None:
        """手動儲存草稿。"""
        self._write_draft(lambda _status: None)

    def _write_draft(self, then, *, quiet: bool = False) -> None:
        """在背景儲存目前草稿，完成後呼叫 ``then(status)``。"""
        state = self.state
        row, database = state.row, self.ctx.database()
        if row is None or database is None or state.busy:
            then(None)
            return
        draft = str(self.draft.value or "")
        self.set_busy(True)

        def done(outcome: ActionOutcome) -> None:
            """依結果顯示提示；自動儲存失敗時保留目前候選。"""
            self.set_busy(False)
            if outcome.status == DRAFT_SAVED:
                state.stored_draft = draft
                self.loader.invalidate()
            if outcome.message:
                show_snack(self.ctx.page, outcome.message, outcome.color)
            self.ctx.update()
            then(outcome.status)

        self.actions.save_draft(database, row, draft, done, quiet=quiet)

    def _apply(self, _e=None) -> None:
        """套用目前草稿；格式有問題時不會送出，避免鎖死這筆候選。"""
        state = self.state
        row, database = state.row, self.ctx.database()
        if row is None or database is None or state.busy or state.has_format_issue:
            return
        self.set_busy(True)
        self.actions.apply(
            database, row, str(self.draft.value or ""), self._after_action
        )

    def _reject(self, _e=None) -> None:
        """保留原譯文並把這筆候選標為已處理。"""
        state = self.state
        row, database = state.row, self.ctx.database()
        if row is None or database is None or state.busy:
            return
        self.set_busy(True)
        self.actions.reject(database, row, self._after_action)

    def _after_action(self, outcome: ActionOutcome) -> None:
        """動作完成後提示結果，並在同一次讀取中更新統計、顯示佇列中的下一筆。"""
        self.set_busy(False)
        show_snack(self.ctx.page, outcome.message or "", outcome.color)
        if outcome.failed:
            self.ctx.update()
            return
        if outcome.reload_parent and callable(self.on_changed):
            self.on_changed()
        self.loader.invalidate()
        self.load_position(refresh_counts=True)
