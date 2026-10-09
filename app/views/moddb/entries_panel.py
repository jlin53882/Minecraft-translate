"""app/views/moddb/entries_panel.py：條目校對（左：清單，中：編輯區＋建議，右：記錄）。

資料與規則都在 ``translation_tool.translation_db``；這裡只負責畫面與事件：

- 手動儲存會寫入「人工」來源，並（可選）同步所有版本中原文相同的條目；儲存前先預覽影響範圍。
- 建議分兩類：同鍵值的其他版本、相同原文的其他鍵值／模組（用詞一致性檢查，不會被一併取代）。
"""

from __future__ import annotations

import sqlite3

import flet as ft

from app.services_impl.moddb_service import (
    EntryDetail,
    EntryFilter,
    EntryRow,
    ReviewPreviewItem,
    TranslationDB,
    current_settings,
)
from app.services_impl.moddb_source_service import (
    source_catalog_for,
    source_label,
    source_tone,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.char_inspector import CharInspector, scrolling_list
from app.views.moddb.entry_filters import (
    ALL_KINDS,
    ALL_MODS,
    apply_filter_options,
    build_filter_controls,
    database_identity,
    entry_filter,
    on_advanced_change,
    open_batch_replace,
    refresh_entry_filter,
)
from app.views.moddb.entry_metadata import build_entry_metadata
from app.views.moddb.entry_row_tile import build_entry_tile
from app.views.moddb.formatting import (
    format_count,
    kind_label,
    token_issues,
    whitespace_note,
)
from app.views.moddb.history_renderer import render_history
from app.views.moddb.mc_text_preview import MinecraftTextPreview
from app.views.moddb.review_scope_dialog import ReviewScopeController
from app.views.moddb.source_filter import SourceFilter
from app.views.moddb.suggestions import build_suggestions
from translation_tool.utils.log_unit import (
    log_debug,
    log_exception,
    log_info,
    log_warning,
)

PAGE_SIZE = 50
__all__ = ["EntriesPanel", "SourceFilter"]
ALL_REVIEW_STATES = "__all__"
ACTOR = "使用者"
NO_SOURCE_TEXT = "（原文未知：之後掃描同版本的 jar，會自動補上原文）"


class EntriesPanel(ft.Column):
    """條目校對頁籤。"""

    def __init__(self, page: ft.Page, get_db, on_changed=None, on_filter_changed=None):
        """``get_db()`` 回傳目前的 ``TranslationDB``（沒有資料庫時回傳 None）。"""
        super().__init__(expand=True, spacing=12)
        self._page = page
        self._get_db = get_db
        self._on_changed = on_changed
        self._on_filter_changed = on_filter_changed
        self._refresh_keep_selection = True
        self._filter_only_refresh = False
        self.version: str | None = None
        self.mod_id: str | None = None
        self.kind: str | None = None
        # 批次機翻「特殊字元不一致」的檢視：只列這些條目，並把 AI 譯文預填到譯文框供修正
        self.entry_ids: list[int] | None = None
        self.drafts: dict[int, str] = {}
        self.state = "all"
        self.query = ""
        self.rows: list[EntryRow] = []
        self.total = 0
        self._list_error: str | None = None
        self.selected: EntryRow | None = None
        self.detail: EntryDetail | None = None
        self.sug_tab = "key"
        self._db_identity = None
        self._render_source_catalog = None
        self._review_preview: list[ReviewPreviewItem] = []
        self._save_running = False
        self.refresh_indicator = ft.Text(
            "背景更新條目資料中…", size=12, color=C.MUTED, visible=False
        )

        self._build_filters()
        self._build_list_card()
        self._build_editor_card()
        self._build_history_card()
        self.controls = [
            self.refresh_indicator,
            self.filter_card,
            self.flagged_banner,
            ft.Row(
                [
                    ft.Column([self.list_card], expand=4),
                    ft.Column([self.editor_card], scroll=ft.ScrollMode.AUTO, expand=6),
                    ft.Column([self.history_card], scroll=ft.ScrollMode.AUTO, expand=3),
                ],
                spacing=12,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        ]
        self._review_controller = ReviewScopeController(self)

    # ------------------------------------------------------------------ 建構
    def _build_filters(self) -> None:
        build_filter_controls(self)

    def _build_list_card(self) -> None:
        # 清單高度跟著視窗伸縮（expand），捲軸常駐顯示（全域主題預設只在滑過時出現）
        self._scroll_offset = 0.0  # 清單目前的捲動位置（儲存／還原後要回到同一個位置）
        self.list_view = ft.ListView(
            spacing=0,
            expand=True,
            on_scroll=self._on_list_scroll,
            scroll_interval=100,
        )
        self.pager = kit.Pager(0, page_size=PAGE_SIZE, on_change=self._on_page)
        self.count_badge = ft.Text("", size=12, color=C.MUTED)
        self.list_card = kit.section_card(
            "條目",
            ft.Column(
                [
                    scrolling_list(self.list_view),
                    self.pager,
                ],
                spacing=0,
                expand=True,
            ),
            icon=ft.Icons.LIST_ALT,
            tone="dia",
            flush=True,
            actions=[self.count_badge],
            expand=True,
        )

    def _build_editor_inputs(self) -> None:
        self.src_text = ft.Text("", size=15, selectable=True, color=C.TEXT)
        self.tw_field = kit.text_field(
            None,
            hint="請輸入繁體中文譯文",
            multiline=True,
            min_lines=3,
            max_lines=8,
            on_change=self._on_text_change,
        )
        self.source_chip = ft.Container()
        self.token_hint = ft.Text("", size=12, color=C.GOLD, visible=False)
        self.mc_preview = MinecraftTextPreview()
        self.meta_col = ft.Column(spacing=4)
        self.impact_text = ft.Text("", size=12.5, color=C.GOLD)
        self.impact_box = ft.Container(
            self.impact_text,
            padding=10,
            border_radius=design.RADIUS_CONTROL,
            bgcolor=design.tone("gold").bg,
            border=ft.Border.all(1, design.tone("gold").line),
            visible=False,
        )
        self.saved_text = ft.Text("", size=12.5, color=C.EM)
        # 預設值取自設定 translation_db.sync_manual；頁面上的開關是這一頁的個別覆寫
        self.sync_row = kit.SwitchRow(
            "同步其他版本",
            "儲存會取代相同原文版本；審核只同步目前顯示相同譯文者（可還原）",
            current_settings().sync_manual,
            on_change=self._on_sync_change,
            divider=False,
        )

    def _build_editor_actions(self) -> None:
        self.prev_btn = kit.button(
            "上一筆",
            "secondary",
            icon=ft.Icons.CHEVRON_LEFT,
            size="sm",
            on_click=lambda _e: self._step(-1),
        )
        self.next_btn = kit.button(
            "下一筆",
            "secondary",
            icon=ft.Icons.CHEVRON_RIGHT,
            size="sm",
            on_click=lambda _e: self._step(1),
        )
        self.reset_btn = kit.button(
            "還原輸入", "ghost", size="sm", on_click=lambda _e: self._reset_input()
        )
        self.copy_src_btn = kit.button(
            "複製原文到譯文",
            "ghost",
            size="sm",
            on_click=lambda _e: self._copy_source(),
        )
        self.chars = CharInspector(self._update_format_hints_and_refresh)
        self.confirm_btn = kit.button(
            "審核（確認目前譯文）",
            "gold",
            icon=ft.Icons.VERIFIED_OUTLINED,
            size="sm",
            on_click=self._confirm,
            tooltip="確認目前譯文為人工-已審核；同步時只審核目前顯示相同譯文的版本",
        )
        self.save_btn = kit.button(
            "儲存", "primary", icon=ft.Icons.CHECK, on_click=self._save, disabled=True
        )

    def _build_editor_card(self) -> None:
        self._build_editor_inputs()
        self._build_editor_actions()
        self.sug_seg = kit.Segmented(
            [("key", "同鍵值・其他版本"), ("text", "相同原文・其他鍵值／模組")],
            "key",
            self._on_sug_tab,
        )
        self.sug_col = ft.Column(spacing=0)
        self.empty_editor = kit.empty_state(
            "選擇左側的條目開始校對",
            "掃描 jar 或完成翻譯後，條目會出現在清單中",
            icon=ft.Icons.EDIT_NOTE,
        )
        self.editor_body = ft.Column(
            [
                kit.section_label("原文 en_us"),
                ft.Container(
                    self.src_text,
                    padding=12,
                    bgcolor=C.PANEL2,
                    border_radius=design.RADIUS_CONTROL,
                    border=ft.Border.all(1, C.LINE),
                ),
                ft.Row([kit.section_label("譯文 zh_tw"), self.source_chip], spacing=8),
                self.tw_field,
                self.token_hint,
                self.chars.row,
                self.chars.box,
                self.mc_preview.control,
                self.meta_col,
                self.impact_box,
                self.saved_text,
                self.sync_row,
                ft.Row(
                    [self.prev_btn, self.next_btn, self.reset_btn, self.copy_src_btn],
                    spacing=8,
                    wrap=True,
                ),
                ft.Row(
                    [self.confirm_btn, self.save_btn],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.END,
                    wrap=True,
                ),
                ft.Divider(height=1, color=C.LINE),
                self.sug_seg,
                self.sug_col,
            ],
            spacing=10,
            visible=False,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.editor_card = kit.section_card(
            "編輯",
            ft.Column([self.empty_editor, self.editor_body], spacing=0),
            icon=ft.Icons.EDIT_OUTLINED,
            tone="em",
        )

    def _build_history_card(self) -> None:
        self.history_col = ft.Column(
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.history_card = kit.section_card(
            "記錄",
            self.history_col,
            icon=ft.Icons.HISTORY,
            tone="gold",
        )

    # --------------------------------------------------------------- 資料載入
    def db(self) -> TranslationDB | None:
        return self._get_db()

    @staticmethod
    def _database_identity(db):
        return database_identity(db)

    def _entry_filter(self) -> EntryFilter:
        return entry_filter(self)

    def _on_advanced_change(self) -> None:
        on_advanced_change(self)

    def _open_batch_replace(self, _e=None) -> None:
        open_batch_replace(self, _e)

    def _after_batch_replace(self, result) -> None:
        self._load_list(page=self.pager.current_page, keep_scroll=True)
        if self._on_changed:
            self._on_changed()
        self._safe_update()

    def refresh(self, *, keep_selection: bool = True) -> None:
        """重新載入版本／模組選項與清單（資料庫更新後呼叫）。"""
        db = self.db()
        if db is None:
            self._db_identity = None
            self.version_dd.options = []
            self.rows, self.total = [], 0
            self._list_error = None
            self._render_list()
            self._show_editor(None)
            return
        identity = self._database_identity(db)
        self._render_source_catalog = source_catalog_for(db)
        if self._db_identity is not None and identity != self._db_identity:
            # A numeric source id belongs to the old DB-local registry; do not
            # carry it into a different database.
            self.source_filter.reset()
        self._db_identity = identity
        self.source_filter.refresh()
        versions = db.versions()
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        if self.version not in versions:
            wanted = current_settings().version
            self.version = (
                wanted if wanted in versions else (versions[0] if versions else None)
            )
        self.version_dd.value = self.version
        self._load_mods()
        self._load_list(keep_selection=keep_selection)

    def apply_refresh_snapshot(self, snapshot: dict) -> None:
        """Apply a worker-loaded tab snapshot without querying SQLite on the UI thread."""
        previous_selected_id = self.selected.id if self.selected else None
        filter_only = snapshot.get("filter_only", False)
        if not filter_only:
            apply_filter_options(self, snapshot)

        self.rows = snapshot.get("rows", [])
        self.total = snapshot.get("total", 0)
        self._list_error = snapshot.get("list_error") or snapshot.get("error")
        self.advanced_filters.error_text.value = self._list_error or ""
        self.advanced_filters.error_text.visible = bool(self._list_error)
        self.pager.set_state(self.total, snapshot.get("page", 1))
        self._render_list()
        self.detail = snapshot.get("detail")
        self.selected = self.detail.entry if self.detail else None
        if (
            not filter_only
            or (self.selected.id if self.selected else None) != previous_selected_id
        ):
            self._show_editor(
                self.detail,
                calculate_impact=False,
                catalog=snapshot.get("source_catalog"),
            )
        self._render_list_selection(previous_selected_id)
        self.refresh_indicator.value = str(snapshot.get("error") or "")
        self.refresh_indicator.visible = bool(snapshot.get("error"))

    def _load_mods(self) -> None:
        db = self.db()
        mods = db.mods(self.version) if (db and self.version) else []
        kit.set_dropdown_options(
            self.mod_dd, [(ALL_MODS, ALL_MODS), *((m, m) for m in mods)]
        )
        if self.mod_id not in mods:
            self.mod_id = None
        self.mod_dd.value = self.mod_id or ALL_MODS
        self._load_kinds()

    def _load_kinds(self) -> None:
        """類型選項取自資料庫實際出現的類型（日後新增類型會自動出現）。"""
        db = self.db()
        kinds = db.kinds(self.version) if (db and self.version) else []
        kit.set_dropdown_options(
            self.kind_dd,
            [(ALL_KINDS, ALL_KINDS), *((k, kind_label(k)) for k in kinds)],
        )
        if self.kind not in kinds:
            self.kind = None
        self.kind_dd.value = self.kind or ALL_KINDS

    def _load_list(
        self,
        *,
        page: int = 1,
        keep_selection: bool = True,
        keep_scroll: bool = False,
    ) -> None:
        """重新載入清單。``keep_scroll=True``（儲存／還原後）維持捲動位置，
        原本選的條目不在清單了（例如在「未翻譯」篩選下存完就消失）就選同一個位置的下一筆。"""
        db = self.db()
        old_index = next(
            (
                i
                for i, r in enumerate(self.rows)
                if self.selected is not None and r.id == self.selected.id
            ),
            None,
        )
        if db is None or not self.version:
            self.rows, self.total = [], 0
            self._list_error = None
        else:
            self._list_error = None
            try:
                self.rows, self.total = db.list_entries(
                    criteria=self._entry_filter(),
                    limit=PAGE_SIZE,
                    offset=(page - 1) * PAGE_SIZE,
                )
            except ValueError as exc:
                self.rows, self.total = [], 0
                self._list_error = str(exc)
                self.advanced_filters.error_text.value = str(exc)
                self.advanced_filters.error_text.visible = True
            if not self.rows and self.total and page > 1:
                # 最後一頁的最後一筆被處理掉：退回仍有資料的最後一頁
                return self._load_list(
                    page=(self.total - 1) // PAGE_SIZE + 1,
                    keep_selection=keep_selection,
                    keep_scroll=keep_scroll,
                )
        self.pager.set_state(self.total, page)
        self._render_list(keep_scroll=keep_scroll)
        keep = keep_selection and self.selected is not None
        current = next(
            (r for r in self.rows if keep and r.id == self.selected.id), None
        )
        if current is None and self.rows:
            if keep_scroll and old_index is not None:
                current = self.rows[min(old_index, len(self.rows) - 1)]
            else:
                current = self.rows[0]
        self.select(current.id if current else None)

    def _render_list(self, *, keep_scroll: bool = False) -> None:
        self.count_badge.value = (
            f"日期條件錯誤：{self._list_error}"
            if self._list_error
            else f"{format_count(self.total)} 筆"
        )
        self.batch_replace_btn.disabled = bool(self._list_error)
        self.advanced_filters.set_summary()
        tiles = [self._row_tile(r) for r in self.rows]
        if not tiles:
            empty = kit.empty_state(
                "無法套用日期篩選" if self._list_error else "沒有符合的條目",
                self._list_error or "調整上方篩選，或先到「掃描匯入」建立資料",
                icon=ft.Icons.ERROR_OUTLINE
                if self._list_error
                else ft.Icons.SEARCH_OFF,
            )
            empty.key = "entry-empty"
            tiles = [empty]
        self.list_view.controls = tiles
        self._scroll_list_to(self._scroll_offset if keep_scroll else 0.0)

    def _on_list_scroll(self, e) -> None:
        self._scroll_offset = float(getattr(e, "pixels", 0) or 0)

    def _scroll_list_to(self, offset: float) -> None:
        """換篩選／換頁後回到清單最上方（offset=0；否則沿用上一份清單的捲動位置，第一筆會被標題蓋住）；
        儲存／還原後回到原本的位置，連續校對時不必重新捲動。"""
        self._scroll_offset = offset
        run_task = getattr(self._page, "run_task", None)
        if not callable(run_task):
            return

        async def scroll() -> None:
            try:
                await self.list_view.scroll_to(offset=offset, duration=0)
            except Exception as exc:  # noqa: BLE001 - 尚未掛上頁面時不影響清單
                log_debug(f"清單捲動略過：{exc}")

        try:
            run_task(scroll)
        except Exception as exc:  # noqa: BLE001 - 排程失敗不影響清單
            log_debug(f"清單捲動排程失敗：{exc}")

    def _row_tile(self, row: EntryRow) -> ft.Control:
        return build_entry_tile(self, row, catalog=self._render_source_catalog)

    # ------------------------------------------------------------------ 選取
    def select(self, entry_id: int | None) -> None:
        previous_selected_id = self.selected.id if self.selected else None
        db = self.db()
        detail = db.entry_detail(entry_id) if (db and entry_id) else None
        self.detail = detail
        self.selected = detail.entry if detail else None
        self._show_editor(detail)
        self._render_list_selection(previous_selected_id)

    def _render_list_selection(self, previous_selected_id: int | None = None) -> None:
        """Rebuild selected rows because mounted Flet controls are immutable."""
        selected_id = self.selected.id if self.selected else None
        changed_ids = {
            entry_id
            for entry_id in (previous_selected_id, selected_id)
            if entry_id is not None
        }
        if not changed_ids:
            return
        rows_by_id = {row.id: row for row in self.rows if row.id in changed_ids}
        if not rows_by_id:
            return
        self.list_view.controls = [
            self._row_tile(rows_by_id[entry_id])
            if (entry_id := getattr(tile, "data", None)) in rows_by_id
            else tile
            for tile in self.list_view.controls
        ]

    def _show_editor(
        self,
        detail: EntryDetail | None,
        *,
        calculate_impact: bool = True,
        catalog=None,
    ) -> None:
        self.empty_editor.visible = detail is None
        self.editor_body.visible = detail is not None
        self.saved_text.value = ""
        if detail is None:
            self.history_col.controls = [kit.hint_text("尚未選取條目")]
            return
        entry = detail.entry
        self.src_text.value = entry.en_us or NO_SOURCE_TEXT
        draft = self.drafts.get(entry.id) if not entry.zh_tw else None
        self.tw_field.value = draft or entry.zh_tw
        if draft:
            self.saved_text.value = (
                "已預填本次機翻的 AI 譯文（特殊字元與原文不一致，尚未寫入）"
            )
        if catalog is None:
            db = self.db() if calculate_impact else None
            catalog = source_catalog_for(db)
        self.source_chip.content = kit.chip(
            source_label(entry.source, catalog, entry.review_status)
            if entry.zh_tw
            else "尚無譯文",
            source_tone(entry.source),
        )
        self.meta_col.controls = build_entry_metadata(detail)
        if calculate_impact:
            self._update_impact()
        else:
            self._update_editor_actions_without_impact(entry)
        self._render_suggestions(catalog=catalog)
        self._render_history(catalog=catalog)

    def _update_editor_actions_without_impact(self, entry: EntryRow) -> None:
        """Set initial editor state without a cross-version preview query."""
        self._update_format_hints()
        changed = bool(entry and self.pending_text() != entry.zh_tw)
        ready = bool(
            entry and entry.zh_tw and entry.review_status != "reviewed" and not changed
        )
        self.save_btn.disabled = self._save_running or not changed
        self.confirm_btn.visible = ready
        self.confirm_btn.disabled = not ready
        self._review_preview = []
        self.impact_box.visible = False

    def _render_suggestions(self, *, catalog=None) -> None:
        if self.detail is None:
            return
        self.sug_col.controls = build_suggestions(
            self.detail,
            self.sug_tab,
            self._apply_suggestion,
            catalog=catalog or source_catalog_for(self.db()),
        )

    def _render_history(self, *, catalog=None) -> None:
        render_history(self, catalog=catalog)

    # ------------------------------------------------------------------ 事件
    def _on_version(self, e) -> None:
        self.version = e.control.value
        self.mod_id = None
        if self._on_filter_changed is None:
            self._load_mods()
        refresh_entry_filter(self, full_refresh=True)

    def _on_mod(self, e) -> None:
        value = e.control.value
        self.mod_id = None if value in (None, "", ALL_MODS) else value
        refresh_entry_filter(self)

    def _on_kind(self, e) -> None:
        value = e.control.value
        self.kind = None if value in (None, "", ALL_KINDS) else value
        refresh_entry_filter(self)

    def _on_source(self) -> None:
        refresh_entry_filter(self)

    def _on_review_status(self) -> None:
        refresh_entry_filter(self)

    def _on_state(self, key: str) -> None:
        self.state = key
        refresh_entry_filter(self)

    def _on_search(self, e) -> None:
        self.query = (e.control.value or "").strip()
        refresh_entry_filter(self)

    def _on_page(self, page: int) -> None:
        refresh_entry_filter(self, page=page, keep_selection=False, background=False)

    def _on_sug_tab(self, key: str) -> None:
        self.sug_tab = key
        self._render_suggestions()
        self._safe_update()

    def _on_text_change(self, _e=None) -> None:
        self._update_impact()
        self._safe_update()

    def _on_sync_change(self, _e=None) -> None:
        """切換「同步其他版本」：影響預覽要跟著重算（儲存時讀的是這個開關）。"""
        self._update_impact()
        self._safe_update()

    def _reset_input(self) -> None:
        if self.selected is not None:
            self.tw_field.value = self.selected.zh_tw
            self._update_impact()
            self._safe_update()

    def _apply_suggestion(self, text: str) -> None:
        self.tw_field.value = text
        self._update_impact()
        self._safe_update()

    def _step(self, delta: int) -> None:
        if self.selected is None:
            return
        ids = [r.id for r in self.rows]
        if self.selected.id not in ids:
            return
        idx = ids.index(self.selected.id) + delta
        if 0 <= idx < len(ids):
            self.select(ids[idx])
            self._safe_update()

    def pending_text(self) -> str:
        """輸入框的內容（原樣；前後空白與換行是譯文的一部分）。全是空白視為空。"""
        value = self.tw_field.value or ""
        return value if value.strip() else ""

    def _update_format_hints(self) -> None:
        """換行、`§` 格式碼、`%s` 佔位符與前後空白的提醒，以及 `§` 顏色預覽。"""
        entry = self.selected
        text = self.tw_field.value or ""
        notes: list[str] = []
        if entry is not None and entry.en_us and text.strip():
            issues = token_issues(entry.en_us, text)
            if issues:
                notes.append("與原文的特殊字元不一致：" + "、".join(issues))
        space = whitespace_note(text)
        if space:
            notes.append(space)
        self.token_hint.value = "；".join(notes)
        self.token_hint.visible = bool(notes)
        self.mc_preview.render(text)
        self.chars.render(entry.en_us if entry else "", text)

    def _update_format_hints_and_refresh(self) -> None:
        self._update_format_hints()
        self._safe_update()

    def _copy_source(self) -> None:
        """把原文（連同換行與前後空白）原樣放進譯文框，再手動翻譯。"""
        if self.selected is not None and self.selected.en_us:
            self.tw_field.value = self.selected.en_us
            self._update_impact()
            self._safe_update()

    def _update_impact(self) -> None:
        self._review_controller.update_impact()

    def _save(self, _e=None) -> None:
        entry, db = self.selected, self.db()
        text = self.pending_text()
        if entry is None or db is None or not text or self._save_running:
            return
        propagate = bool(self.sync_row.value)
        self._save_running = True
        self.save_btn.disabled = True
        self.saved_text.value = "正在儲存並同步其他版本…" if propagate else "正在儲存…"
        self._safe_update()

        def save_in_background() -> None:
            done = None
            error = None
            try:
                done = db.save_manual(entry.id, text, actor=ACTOR, propagate=propagate)
            except ValueError as exc:
                error = ("validation", exc)
            except sqlite3.Error as exc:
                error = ("database", exc)
            except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
                error = ("unexpected", exc)
                log_exception(
                    f"Mod 資料庫手動儲存發生未預期錯誤："
                    f"{entry.mc_version} {entry.mod_id} {entry.key}"
                )

            run_task = getattr(self._page, "run_task", None)
            try:
                if callable(run_task):
                    run_task(
                        self._apply_save_result_async, db, entry, text, done, error
                    )
                else:
                    self._apply_save_result(db, entry, text, done, error)
            except Exception as exc:  # noqa: BLE001 - a disposed page may reject the result callback
                log_warning(f"Mod 資料庫儲存結果無法排回畫面：{exc!r}")

        try:
            launched = launch_page_operation(
                self._page,
                save_in_background,
                name="Mod 資料庫手動儲存",
                owner="moddb-manual-save",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                commit=CommitPolicy.ATOMIC,
                durability=DurabilityPolicy.USER_ACTION,
                shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
            )
        except Exception as exc:  # noqa: BLE001 - restore the editor if the worker cannot launch
            self._save_running = False
            log_exception(
                f"Mod 資料庫手動儲存無法啟動："
                f"{entry.mc_version} {entry.mod_id} {entry.key}"
            )
            self.saved_text.value = ""
            show_snack(self._page, f"儲存無法啟動：{exc}", C.RED)
            self._update_impact()
            self._safe_update()
            return
        if not launched:
            self._save_running = False
            self.saved_text.value = ""
            show_snack(self._page, "應用程式正在關閉，未啟動儲存。", C.GOLD)
            self._update_impact()
            self._safe_update()

    async def _apply_save_result_async(self, db, entry, text, done, error) -> None:
        self._apply_save_result(db, entry, text, done, error)

    def _apply_save_result(self, db, entry, text, done, error) -> None:
        self._save_running = False
        if error is not None:
            kind, exc = error
            if kind == "validation":
                log_warning(
                    f"Mod 資料庫儲存被拒絕：{entry.mc_version} {entry.mod_id} "
                    f"{entry.key}（{exc!r}）"
                )
                show_snack(self._page, str(exc), C.RED)
            elif kind == "database":
                log_exception(
                    f"Mod 資料庫儲存失敗：{entry.mc_version} {entry.mod_id} {entry.key}"
                )
                show_snack(
                    self._page,
                    f"儲存失敗（資料庫錯誤：{exc}）。可能是資料庫被其他程式鎖住，請稍後再試；詳情見後台 log",
                    C.RED,
                )
            else:
                show_snack(self._page, f"儲存失敗：{exc}；詳情見後台 log", C.RED)
            if self.selected is not None and self.selected.id == entry.id:
                self.saved_text.value = ""
            self._update_impact()
            self._safe_update()
            return

        if done is None:
            return
        others = [i.mc_version for i in done if not i.is_self]
        log_info(
            f"Mod 資料庫手動儲存：{entry.mc_version} {entry.mod_id} {entry.key}"
            f"（同步 {len(others)} 個版本：{'、'.join(others) or '無'}）"
        )
        message = (
            f"已儲存，並同步 {len(others)} 個版本：{'、'.join(others)}"
            if others
            else "已儲存"
        )
        show_snack(self._page, message, C.EM, text_color=C.ON_EM)
        if self.db() is db:
            same_entry = self.selected is not None and self.selected.id == entry.id
            unchanged_editor = same_entry and self.pending_text() == text
            if unchanged_editor:
                self._load_list(page=self.pager.current_page, keep_scroll=True)
                if self.selected is not None and self.selected.id == entry.id:
                    self.saved_text.value = message
            elif same_entry:
                self.saved_text.value = f"{message}；目前輸入的變更尚未儲存。"
            if self._on_changed:
                self._on_changed()
        self._update_impact()
        self._safe_update()

    def _confirm(self, _e=None) -> None:
        self._review_controller.confirm(_e)

    def _revert(self, history_id: int) -> None:
        db = self.db()
        if db is None:
            return
        try:
            count = db.revert(history_id)
        except sqlite3.Error as exc:
            log_exception(f"Mod 資料庫還原失敗：history_id={history_id}")
            show_snack(
                self._page,
                f"還原失敗（資料庫錯誤：{exc}）；詳情見後台 log",
                C.RED,
            )
            return
        log_info(f"Mod 資料庫還原：history_id={history_id}，還原 {count} 筆")
        self._load_list(page=self.pager.current_page, keep_scroll=True)
        show_snack(
            self._page,
            f"已還原 {count} 筆" if count else "沒有可還原的內容（之後已被再次修改）",
            C.EM if count else C.GOLD,
        )
        if self._on_changed:
            self._on_changed()
        self._safe_update()

    def _revert_batch(self, batch_id: str) -> None:
        db = self.db()
        if db is None:
            return
        try:
            result = db.revert_batch_replace(batch_id)
        except sqlite3.Error as exc:
            log_exception(f"Mod 資料庫批次取代還原失敗：batch={batch_id}")
            show_snack(self._page, f"整批還原失敗（資料庫錯誤：{exc}）", C.RED)
            return
        self._load_list(page=self.pager.current_page, keep_scroll=True)
        show_snack(
            self._page,
            f"已還原 {result.reverted:,} 筆；因後續修改略過 {result.skipped:,} 筆。",
            C.EM if result.reverted else C.GOLD,
        )
        if self._on_changed:
            self._on_changed()
        self._safe_update()

    def show_filter(self, state: str) -> None:
        """從其他頁籤（總覽／掃描結果）跳進來時套用狀態篩選。"""
        self._set_flagged(None, {})
        self.state = state
        self.state_seg.select(state)

    def show_flagged(
        self, entry_ids: list[int], drafts: dict[int, str], version: str
    ) -> None:
        """從批次機翻跳進來：只列「特殊字元不一致、沒寫入」的條目，並預填 AI 譯文供修正。"""
        self.version = version
        self.mod_id = None
        self.kind = None
        self.query = ""
        self.search.value = ""
        self.state = "none"
        self.state_seg.select("none")
        self.source_filter.reset()
        self._set_flagged(list(entry_ids), dict(drafts))

    def clear_flagged(self) -> None:
        """清除「特殊字元不一致」篩選，回到一般清單。"""
        self._set_flagged(None, {})
        refresh_entry_filter(self)

    def _set_flagged(self, entry_ids: list[int] | None, drafts: dict[int, str]) -> None:
        self.entry_ids = entry_ids
        self.drafts = drafts
        self.flagged_banner.visible = entry_ids is not None
        if entry_ids is not None:
            self.flagged_text.value = (
                f"只顯示本次機翻「特殊字元與原文不一致」的 {len(entry_ids):,} 筆"
                "（AI 譯文尚未寫入，已預填在譯文框；修正後儲存即可，或按「清除篩選」）"
            )

    def _safe_update(self) -> None:
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響資料操作
            log_debug(f"EntriesPanel update 略過：{exc}")
