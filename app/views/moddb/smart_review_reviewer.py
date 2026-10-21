"""智慧校對視窗的「審查」分頁：每頁一筆的逐筆審查（套用／保留原譯文／存草稿）。"""

from __future__ import annotations

from typing import Any

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    SMART_REVIEW_MANUAL_EDIT_STATUSES,
    SmartReviewCandidate,
    apply_moddb_smart_review_candidate_from_path,
    apply_moddb_smart_review_manual_edit_from_path,
    get_moddb_smart_review_run_breakdown_from_path,
    get_moddb_smart_review_run_from_path,
    load_moddb_smart_review_candidate_page_from_path,
    realign_moddb_smart_review_candidate_from_path,
    reject_moddb_smart_review_candidate_from_path,
    save_moddb_smart_review_draft_from_path,
    smart_review_format_diagnostics,
)
from app.tasks.operation_registry import CommitPolicy, DurabilityPolicy
from app.ui import design, kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.formatting import format_count
from app.views.moddb.smart_review_context import SmartReviewContext

DEFAULT_FILTER = "ready"
# 篩選器：(狀態值, 顯示名稱)；``all`` 表示不篩選。
FILTERS: tuple[tuple[str, str], ...] = (
    ("ready", "可套用"),
    ("applied", "已套用"),
    ("rejected", "已保留原譯文"),
    ("needs_context", "需補 context"),
    ("no_change", "AI 認為不用改"),
    ("invalid", "無效（AI 回應不合格）"),
    ("invalid_format", "格式不符"),
    ("stale", "已過期"),
    ("all", "全部"),
)
STATUS_LABEL = dict(FILTERS)
_STATUS_TONE = {
    "ready": "em",
    "applied": "dia",
    "rejected": "neutral",
    "needs_context": "gold",
    "no_change": "neutral",
    "invalid": "red",
    "invalid_format": "red",
    "stale": "gold",
}
_DECISION_LABEL = {
    "no_change": "維持不變",
    "suggest_revision": "建議修改",
    "needs_context": "需補 context",
}
_CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低"}
# 為什麼某筆不能套用：讓灰掉的按鈕有明確的說明。
_NOT_APPLICABLE = {
    "applied": "這筆已經套用；要再修改請編輯草稿後重新套用。",
    "rejected": "這筆已選擇保留原譯文。",
    "needs_context": "AI 表示需要更多上下文才能判斷，沒有提供建議譯文。",
    "no_change": "AI 認為目前譯文不用修改，沒有建議譯文。",
    "invalid": "AI 的回應沒有通過驗證，不能套用。",
    "invalid_format": "草稿的格式未通過檢查，這筆已鎖定；請重新校對。",
    "stale": "資料庫內容在校對後已變更，這筆已過期；請重新校對。",
}
_APPLY_MESSAGES = {
    "applied": ("已套用 1 筆人工（未審核）譯文；原 AI 譯文保留。", C.EM),
    "stale": ("資料庫內容已變更，這筆候選已過期；未覆蓋目前譯文。", C.GOLD),
    "manual_reviewed": (
        "目前已有人工審核過的譯文，不會被覆蓋；請到條目校對編輯。",
        C.GOLD,
    ),
    "invalid_format": ("草稿格式未通過檢查，未套用。", C.GOLD),
    "invalid_draft": ("草稿不可為空白。", C.GOLD),
    "not_ready": ("這筆目前不是可套用的狀態。", C.GOLD),
    "missing": ("找不到這筆候選。", C.GOLD),
}
# 人工編輯套用（任何非 ready 的可編輯狀態）可能回傳的結果。
_MANUAL_EDIT_MESSAGES = {
    **_APPLY_MESSAGES,
    "applied": ("已套用人工修改（未審核）；可從條目歷史還原。", C.EM),
    "changed": (
        "資料庫內容在載入後已變更，未覆蓋；已重新載入這筆，請確認目前譯文後再套用。",
        C.GOLD,
    ),
    "unchanged": ("草稿與目前譯文相同，沒有需要套用的變更。", C.GOLD),
    "not_editable": ("這筆目前不能人工編輯。", C.GOLD),
    "unsupported": ("舊版批次不支援人工編輯套用；請重新校對。", C.GOLD),
}
_MANUAL_EDIT_HINTS = {
    "applied": "這筆已套用。修改草稿後按「重新套用」會覆蓋目前人工譯文（可從條目歷史還原）。",
    "rejected": "這筆先前選擇保留原譯文；仍可編輯草稿後套用。",
    "needs_context": "AI 沒有提供建議譯文；草稿預設為目前譯文，可直接修改後套用。",
    "no_change": "AI 認為不用修改；草稿預設為目前譯文，仍可修改後套用。",
    "invalid": "AI 的回應沒有通過驗證；草稿預設為目前譯文，可直接修改後套用。",
    "invalid_format": "先前的草稿格式未通過檢查；請修正草稿後套用。",
    "stale": "資料庫內容在校對後已變更；已對齊目前譯文，可直接修改後套用。",
}


def is_manual_edit_row(row: SmartReviewCandidate | None) -> bool:
    """這筆候選是否走人工編輯套用（非 ready 但可編輯的狀態）。"""
    return row is not None and row.status in SMART_REVIEW_MANUAL_EDIT_STATUSES


def manual_edit_hint(row: SmartReviewCandidate) -> str:
    """說明人工編輯這筆候選的方式；未儲存的修改換筆後不會保留。"""
    hint = _MANUAL_EDIT_HINTS.get(row.status, "")
    if row.status in {"invalid", "invalid_format"} and row.validation_error:
        hint += f"原因：{row.validation_error}。"
    return f"{hint}未套用的修改換筆後不會保留。" if hint else ""


def initial_draft_text(row: SmartReviewCandidate) -> str:
    """決定草稿框的起始內容：已套用以目前資料庫值為準，其他依序取草稿、建議、現況。"""
    if row.status == "applied":
        return row.current_zh_tw or ""
    if row.status in SMART_REVIEW_MANUAL_EDIT_STATUSES:
        return row.draft_zh_tw or row.suggested_zh_tw or row.current_zh_tw or ""
    return row.draft_zh_tw or row.suggested_zh_tw or ""


def candidate_evidence_text(row: SmartReviewCandidate) -> str:
    """只顯示 AI 實際引用的證據，並相容解析舊版 entry-only id。"""
    context = list(row.context)
    evidence_by_id = {str(item.get("id")): item for item in context}
    legacy_by_id: dict[str, list[dict]] = {}
    for item in context:
        legacy_id = item.get("legacy_id")
        if legacy_id:
            legacy_by_id.setdefault(str(legacy_id), []).append(item)

    def reference_label(item: dict) -> str:
        """將 context 來源轉成審查者看得懂的可信度分層。"""
        reference_type = str(item.get("reference_type", "local"))
        match_type = str(item.get("match_type", ""))
        if reference_type == "official_locale":
            if match_type in {"exact_key", "exact_text", "official_phrase"}:
                return "官方完整翻譯"
            return "官方術語"
        if reference_type == "reviewed_lang_glossary":
            return "已審核 lang 參考"
        if reference_type in {"same_key_reviewed", "same_text_reviewed"}:
            return "已審核資料庫參考"
        if reference_type == "module_locale":
            return "未審核模組翻譯（低信心，非官方標準）"
        return "本地參考"

    def format_item(display_id: str, item: dict) -> str:
        """格式化單筆證據及其版本、來源與品質限制。"""
        return (
            f"{display_id} · {reference_label(item)} · "
            f"來源={item.get('source_label', item.get('source', ''))} · "
            f"版本={item.get('mc_version', '-')} · "
            f"信心={_CONFIDENCE_LABEL.get(str(item.get('confidence', 'low')), '未知')} · "
            f"{item.get('en_us', '')} → {item.get('zh_tw', '')} · "
            f"{item.get('limitations', '')}"
        )

    evidence_ids = tuple(row.evidence_ids)
    if not evidence_ids:
        lines = ["AI 未引用任何本地證據。"]
        if context:
            lines.append("可參考資料（非本次 AI 證據）：")
            groups = (
                (
                    "官方完整翻譯參考",
                    [
                        item
                        for item in context
                        if item.get("reference_type") == "official_locale"
                        and item.get("match_type")
                        in {"exact_key", "exact_text", "official_phrase"}
                    ],
                ),
                (
                    "官方術語參考",
                    [
                        item
                        for item in context
                        if item.get("reference_type") == "official_locale"
                        and item.get("match_type")
                        not in {"exact_key", "exact_text", "official_phrase"}
                    ],
                ),
                (
                    "已審核參考",
                    [
                        item
                        for item in context
                        if item.get("reference_type")
                        in {
                            "reviewed_lang_glossary",
                            "same_key_reviewed",
                            "same_text_reviewed",
                        }
                    ],
                ),
                (
                    "模組語系參考",
                    [
                        item
                        for item in context
                        if item.get("reference_type") == "module_locale"
                    ],
                ),
                (
                    "其他本地參考",
                    [
                        item
                        for item in context
                        if item.get("reference_type")
                        not in {
                            "official_locale",
                            "reviewed_lang_glossary",
                            "same_key_reviewed",
                            "same_text_reviewed",
                            "module_locale",
                        }
                    ],
                ),
            )
            remaining = 6
            for label, grouped in groups:
                if not grouped or remaining <= 0:
                    continue
                lines.append(f"{label}：")
                selected = grouped[:remaining]
                lines.extend(
                    format_item(str(item.get("id")), item) for item in selected
                )
                remaining -= len(selected)
        else:
            lines.append("完全沒有可供參考的本地 context；請人工確認。")
        return "\n".join(lines)
    lines = []
    for evidence_id in evidence_ids[:8]:
        requested_id = str(evidence_id)
        item = evidence_by_id.get(requested_id)
        if item is not None:
            lines.append(format_item(requested_id, item))
            continue
        matches = legacy_by_id.get(requested_id, [])
        if len(matches) == 1:
            lines.append(
                format_item(f"{requested_id} → {matches[0].get('id')}", matches[0])
            )
        elif matches:
            lines.append(f"{requested_id}（舊版 id 對應多個來源，無法唯一解析）")
        else:
            lines.append(f"{requested_id}（找不到本地證據）")
    return "\n".join(lines)


def not_applicable_reason(row: SmartReviewCandidate) -> str | None:
    """回傳這筆候選不能套用的原因；可套用時回傳 None。"""
    if row.status == "ready":
        return None
    reason = _NOT_APPLICABLE.get(row.status, f"目前狀態「{row.status}」不能套用。")
    if row.status in {"invalid", "invalid_format"} and row.validation_error:
        reason += f"原因：{row.validation_error}"
    return reason


class SmartReviewReviewer:
    """逐筆審查某個校對批次：每頁只顯示一筆，翻頁就是換下一筆。"""

    def __init__(self, ctx: SmartReviewContext, *, on_changed) -> None:
        """建立審查面板；``on_changed`` 在候選被套用後通知外層重新整理。"""
        self.ctx = ctx
        self.on_changed = on_changed
        self.run_id: str | None = None
        self.run_info: dict[str, Any] = {}
        self.counts: dict[str, int] = {}
        self.status_filter = DEFAULT_FILTER
        self.position = 0
        self.total = 0
        self.row: SmartReviewCandidate | None = None
        self.busy = False
        self._seq = 0
        self._stored_draft = ""
        self._has_format_issue = False
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
        self.run_id = run_id
        self._seq += 1
        seq = self._seq
        self._show_loading("正在載入批次…")

        def work() -> tuple[dict | None, dict]:
            """唯讀取得批次資訊與狀態統計。"""
            info = get_moddb_smart_review_run_from_path(path, priority, run_id)
            breakdown = get_moddb_smart_review_run_breakdown_from_path(
                path, priority, run_id
            )
            return info, breakdown

        def done(result, error: str | None) -> None:
            """決定初始篩選並顯示第一筆。"""
            if error:
                self._show_empty("批次載入失敗", error)
                return
            info, breakdown = result
            if info is None:
                self.run_id = None
                self._show_empty("找不到批次", f"run_id：{run_id}")
                return
            self.run_info = info
            self.counts = dict(breakdown.get("status", {}))
            ready = self.counts.get("ready", 0)
            self.status_filter = "ready" if ready else "all"
            self.filter_dd.value = self.status_filter
            self._refresh_filter_labels()
            self.position = 0
            self.load_position()

        self.ctx.run_background(
            work, done, name="載入智慧校對批次", still_valid=lambda: seq == self._seq
        )

    def reload_counts(self, then=None) -> None:
        """重新讀取目前批次的狀態統計（套用或保留之後使用）。"""
        database = self.ctx.database()
        if database is None or not self.run_id:
            return
        path, priority = database
        run_id = self.run_id

        def work() -> dict:
            """唯讀取得狀態統計。"""
            return get_moddb_smart_review_run_breakdown_from_path(
                path, priority, run_id
            )

        def done(result, error: str | None) -> None:
            """更新篩選器上的筆數並繼續。"""
            if not error:
                self.counts = dict(result.get("status", {}))
                self._refresh_filter_labels()
            if then is not None:
                then()

        self.ctx.run_background(work, done, name="更新智慧校對統計")

    def load_position(self, *, refresh_total: bool = True) -> None:
        """依目前篩選與位置載入單筆候選（每頁一筆）。"""
        database = self.ctx.database()
        if database is None or not self.run_id:
            return
        path, priority = database
        run_id = self.run_id
        status = None if self.status_filter == "all" else self.status_filter
        position = max(0, self.position)
        known_total = None if refresh_total else self.total
        self._seq += 1
        seq = self._seq
        self.set_busy(True)
        self.ctx.update()

        def read_page(total_hint: int | None):
            """在一次唯讀操作中取得總數與目前位置的那一筆。"""
            return load_moddb_smart_review_candidate_page_from_path(
                path,
                priority,
                run_id,
                status=status,
                limit=1,
                offset=position,
                known_total=total_hint,
            )

        def work() -> tuple[int, list[SmartReviewCandidate]]:
            """載入該筆；可人工編輯的候選先對齊目前資料庫，讓畫面顯示現況。"""
            total, rows = read_page(known_total)
            if is_manual_edit_row(rows[0] if rows else None) and (
                realign_moddb_smart_review_candidate_from_path(
                    path, priority, run_id, rows[0].ordinal
                )
                == "realigned"
            ):
                total, rows = read_page(total)
            return total, rows

        def done(result, error: str | None) -> None:
            """顯示該筆；位置超出範圍時退回最後一筆。"""
            self.set_busy(False)
            if error:
                self._show_empty("候選載入失敗", error)
                return
            total, rows = result
            self.total = total
            self.position = min(position, total - 1) if total else 0
            self.row = rows[0] if rows else None
            self._render()

        self.ctx.run_background(
            work,
            done,
            name="載入智慧校對候選",
            still_valid=lambda: seq == self._seq,
        )

    # ──────────────── 顯示 ────────────────

    def _refresh_filter_labels(self) -> None:
        """在篩選器選項上顯示各狀態的筆數。"""
        total = sum(self.counts.values())
        pairs = [
            (
                key,
                f"{label}（{format_count(total if key == 'all' else self.counts.get(key, 0))}）",
            )
            for key, label in FILTERS
        ]
        kit.set_dropdown_options(self.filter_dd, pairs)
        parts = [
            f"{label} {format_count(self.counts[key])}"
            for key, label in FILTERS[:-1]
            if self.counts.get(key)
        ]
        self.summary_text.value = "批次狀態：" + (" · ".join(parts) or "沒有候選")
        info = self.run_info
        if info:
            self.run_text.value = (
                f"批次 {str(info.get('id', ''))[:8]} · "
                f"{info.get('provider')}/{info.get('model')} · "
                f"{info.get('scope_version') or '-'} · {info.get('started_at') or '-'}"
            )

    def _show_empty(self, title: str, subtitle: str = "") -> None:
        """顯示空狀態，並停用所有動作。"""
        self.row = None
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
        row = self.row
        if row is None:
            label = STATUS_LABEL.get(self.status_filter, self.status_filter)
            done_all = (
                self.status_filter == "ready"
                and self.counts.get("applied", 0) + self.counts.get("rejected", 0) > 0
            )
            self._show_empty(
                "可套用的候選都處理完了" if done_all else f"「{label}」沒有候選",
                "可以切換審查篩選查看其他狀態。",
            )
            return
        self.position_input.value = str(self.position + 1)
        self.position_suffix.value = (
            f" / {self.total:,} 筆"
            f"（{STATUS_LABEL.get(self.status_filter, self.status_filter)}）"
        )
        self.progress_bar.value = (self.position + 1) / self.total if self.total else 0
        self._stored_draft = initial_draft_text(row)
        self.draft.value = self._stored_draft
        self.draft.read_only = not (row.status == "ready" or is_manual_edit_row(row))
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
        """建立左右兩欄：左邊是原文與 AI 判斷，右邊是可編輯草稿與動作。"""
        reason = row.reason or "（這批資料沒有儲存 reason）"
        issues = "、".join(row.issue_types) or "未標記"
        evidence = ft.ExpansionTile(
            title=ft.Text("本地證據", size=12, color=C.MUTED),
            controls=[
                ft.Container(
                    ft.Text(candidate_evidence_text(row), size=12, selectable=True),
                    padding=ft.Padding.only(left=14, right=14, bottom=10),
                )
            ],
            expanded=False,
            tile_padding=ft.Padding.symmetric(horizontal=10),
        )
        left = ft.Column(
            [
                self._text_card("英文原文", row.en_us, "dia"),
                self._text_card(
                    f"目前譯文（來源：{self._source_label(row)} · 審核：{row.review_status or '未標記'}）",
                    row.current_zh_tw,
                    "gold",
                ),
                self._text_card("AI 建議譯文", row.suggested_zh_tw or "", "em"),
                self._text_card("AI 理由", f"{reason}\n問題類型：{issues}"),
                evidence,
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
        return ft.ResponsiveRow(
            [
                ft.Container(left, col={"xs": 12, "md": 6}),
                ft.Container(right, col={"xs": 12, "md": 6}),
            ],
            spacing=14,
            run_spacing=14,
        )

    # ──────────────── 草稿與格式診斷 ────────────────

    def _on_draft_change(self, _e=None) -> None:
        """草稿被編輯時重新診斷格式並更新按鈕狀態。"""
        self._refresh_format()
        self.ctx.update()

    def _refresh_format(self) -> None:
        """依目前草稿計算格式診斷，並決定能否儲存與套用。"""
        row = self.row
        value = str(self.draft.value or "")
        has_issue = False
        if row is None:
            self._set_actions_enabled(False, None)
            return
        if not value.strip():
            self.format_text.value = "格式診斷：尚無可分析草稿"
            self.format_text.color = C.MUTED
            has_issue = row.status == "ready" or is_manual_edit_row(row)
        else:
            try:
                diagnostics = smart_review_format_diagnostics(
                    row.en_us, value, kind=row.kind
                )
            except (TypeError, ValueError) as exc:
                self.format_text.value = f"格式診斷無法執行：{exc}"
                self.format_text.color = C.GOLD
            else:
                lines = diagnostics.summary_lines
                self.format_text.value = (
                    "格式診斷：" + "；".join(lines)
                    if lines
                    else "格式診斷：未發現格式問題"
                )
                self.format_text.color = C.RED if diagnostics.has_issues else C.EM
                has_issue = diagnostics.has_issues
        self._has_format_issue = has_issue
        self._set_actions_enabled(True, row)

    def _set_actions_enabled(
        self, has_row: bool, row: SmartReviewCandidate | None
    ) -> None:
        """依候選狀態與格式診斷設定按鈕，並說明不能操作的原因。"""
        manual = has_row and is_manual_edit_row(row)
        reason = not_applicable_reason(row) if row is not None else None
        ready = has_row and row is not None and reason is None
        if manual:
            self._set_manual_edit_actions(row)
            reason = manual_edit_hint(row)
            if self._has_format_issue:
                reason = "草稿有格式問題或是空白：請先修正才能套用。"
        else:
            self.apply_btn.content = "套用這筆"
            if ready and self._has_format_issue:
                reason = "草稿有格式問題：請先修正，否則套用會被拒絕並鎖定這筆。"
            usable = ready and not self._has_format_issue and not self.busy
            self.apply_btn.disabled = not usable
            self.save_btn.disabled = not usable
            self.reject_btn.disabled = not (ready and not self.busy)
        self.prev_btn.disabled = not has_row or self.position <= 0 or self.busy
        self.next_btn.disabled = (
            not has_row or self.position >= self.total - 1 or self.busy
        )
        self.position_input.disabled = not has_row or self.busy
        self.filter_dd.disabled = self.busy or not self.run_id
        self.hint_text.value = reason or ""

    def _set_manual_edit_actions(self, row: SmartReviewCandidate) -> None:
        """人工編輯模式只開放套用；草稿有內容且格式正確才能按，存草稿與保留原譯文不適用。"""
        self.apply_btn.content = "重新套用" if row.status == "applied" else "套用修改"
        self.apply_btn.disabled = self.busy or self._has_format_issue
        self.save_btn.disabled = True
        self.reject_btn.disabled = True

    def set_busy(self, busy: bool) -> None:
        """切換忙碌狀態並同步按鈕可用性。"""
        self.busy = busy
        self._set_actions_enabled(self.row is not None, self.row)

    # ──────────────── 導覽 ────────────────

    def _on_filter(self, event) -> None:
        """切換審查篩選並回到第一筆。"""
        if self.busy:
            return
        self.status_filter = str(event.control.value or "all")
        self.position = 0
        self._flush_then(self.load_position)

    def _move(self, delta: int) -> None:
        """移到上一筆或下一筆；到頭時說明原因。"""
        if self.busy:
            return
        target = self.position + delta
        if target < 0 or target >= self.total:
            show_snack(
                self.ctx.page,
                "已經是最後一筆。" if delta > 0 else "已經是第一筆。",
                C.DIA,
                duration=2200,
            )
            return

        def go() -> None:
            """在草稿處理完後載入目標位置。"""
            self.position = target
            self.load_position(refresh_total=False)

        self._flush_then(go)

    def _on_position_submit(self, event) -> None:
        """解析手動輸入的 1-based 筆次，完成驗證後跳到指定候選。"""
        if self.busy:
            return
        raw = str(event.control.value or "").strip()
        try:
            target = int(raw)
        except (TypeError, ValueError):
            target = 0
        if self.total <= 0 or not self.row:
            self._restore_position_input()
            return
        if target < 1 or target > self.total:
            show_snack(
                self.ctx.page,
                f"請輸入 1～{self.total:,} 的筆次。",
                C.GOLD,
                duration=2200,
            )
            self._restore_position_input()
            return
        if target - 1 == self.position:
            self._restore_position_input()
            return

        def go() -> None:
            """在草稿處理完後載入手動指定的位置。"""
            self.position = target - 1
            self.load_position(refresh_total=False)

        self._flush_then(go)

    def _restore_position_input(self) -> None:
        """把筆次輸入框還原成目前實際顯示的位置。"""
        self.position_input.value = str(self.position + 1) if self.row else ""
        self.ctx.update()

    def _dirty(self) -> bool:
        """草稿是否與已儲存內容不同且可儲存。"""
        return (
            self._draft_changed()
            and bool(str(self.draft.value or "").strip())
            and not self._has_format_issue
        )

    def _draft_changed(self) -> bool:
        """判斷目前可編輯候選是否有尚未保存的文字變更。"""
        row = self.row
        return (
            row is not None
            and row.status == "ready"
            and str(self.draft.value or "") != self._stored_draft
        )

    def _flush_then(self, then) -> None:
        """換筆前保存草稿；格式錯誤或空白草稿會停留在目前候選。"""
        if not self._draft_changed():
            then()
            return
        if not str(self.draft.value or "").strip() or self._has_format_issue:
            show_snack(
                self.ctx.page,
                "草稿尚未保存：請先修正格式問題或填入內容。",
                C.GOLD,
                duration=2600,
            )
            return
        self._write_draft(
            lambda status: then() if status == "saved" else None,
            quiet=True,
        )

    # ──────────────── 動作 ────────────────

    def _save(self, _e=None) -> None:
        """手動儲存草稿。"""
        self._write_draft(lambda _status: None)

    def _write_draft(self, then, *, quiet: bool = False) -> None:
        """在背景儲存目前草稿，完成後呼叫 ``then(status)``。"""
        row, database = self.row, self.ctx.database()
        if row is None or database is None or self.busy:
            then(None)
            return
        path, priority = database
        draft = str(self.draft.value or "")
        self.set_busy(True)

        def work() -> str:
            """儲存草稿並回傳 service 狀態。"""
            return save_moddb_smart_review_draft_from_path(
                path, priority, row.run_id, row.ordinal, draft
            )

        def done(status, error: str | None) -> None:
            """依結果顯示提示；自動儲存失敗時保留目前候選。"""
            self.set_busy(False)
            if error:
                show_snack(self.ctx.page, f"草稿儲存失敗：{error}", C.RED)
            elif status == "saved":
                self._stored_draft = draft
                if not quiet:
                    show_snack(self.ctx.page, "草稿已儲存，尚未改寫正式譯文。", C.EM)
            else:
                show_snack(self.ctx.page, f"草稿目前無法儲存：{status}", C.GOLD)
            self.ctx.update()
            then(status)

        self.ctx.run_background(
            work,
            done,
            name="儲存智慧校對草稿",
            commit=CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
            stable_identity=True,
        )

    def _apply(self, _e=None) -> None:
        """套用目前草稿；格式有問題時不會送出，避免鎖死這筆候選。"""
        row, database = self.row, self.ctx.database()
        if row is None or database is None or self.busy or self._has_format_issue:
            return
        path, priority = database
        draft = str(self.draft.value or "")
        manual = is_manual_edit_row(row)
        messages = _MANUAL_EDIT_MESSAGES if manual else _APPLY_MESSAGES
        self.set_busy(True)

        def work() -> str:
            """以 CAS 條件套用候選（ready 走 AI 建議流程，其餘走人工編輯）。"""
            if manual:
                return apply_moddb_smart_review_manual_edit_from_path(
                    path, priority, row.run_id, row.ordinal, draft
                )
            return apply_moddb_smart_review_candidate_from_path(
                path, priority, row.run_id, row.ordinal, draft_zh_tw=draft
            )

        self.ctx.run_background(
            work,
            lambda status, error: self._after_action(
                status, error, "套用失敗", True, messages=messages
            ),
            name="套用智慧校對候選",
            commit=CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
            stable_identity=True,
        )

    def _reject(self, _e=None) -> None:
        """保留原譯文並把這筆候選標為已處理。"""
        row, database = self.row, self.ctx.database()
        if row is None or database is None or self.busy:
            return
        path, priority = database
        self.set_busy(True)

        def work() -> str:
            """標記候選為保留原譯文並回傳 service 狀態。"""
            return reject_moddb_smart_review_candidate_from_path(
                path, priority, row.run_id, row.ordinal
            )

        self.ctx.run_background(
            work,
            lambda status, error: self._after_action(status, error, "保留失敗", False),
            name="保留智慧校對原譯文",
            commit=CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
            stable_identity=True,
        )

    def _after_action(
        self,
        status,
        error: str | None,
        failure: str,
        notify_parent: bool,
        *,
        messages: dict = _APPLY_MESSAGES,
    ) -> None:
        """動作完成後提示結果，更新統計並顯示佇列中的下一筆。"""
        self.set_busy(False)
        if error:
            show_snack(self.ctx.page, f"{failure}：{error}", C.RED)
            self.ctx.update()
            return
        if status == "rejected":
            show_snack(self.ctx.page, "已保留原譯文，這筆不會套用。", C.EM)
        else:
            message, color = messages.get(
                status, (f"這筆目前無法處理：{status}", C.GOLD)
            )
            show_snack(self.ctx.page, message, color)
        if status == "applied" and notify_parent and callable(self.on_changed):
            self.on_changed()
        self.set_busy(True)
        self.ctx.update()
        self.reload_counts(then=self.load_position)
