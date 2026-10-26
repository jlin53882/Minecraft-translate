"""app/views/moddb/entries_panel.py：條目校對（左：清單，中：編輯區＋建議，右：記錄）。

資料與規則都在 ``translation_tool.translation_db``；這裡只負責畫面與事件：

- 手動儲存會寫入「人工」來源，並（可選）同步所有版本中原文相同的條目；儲存前先預覽影響範圍。
- 建議分兩類：同鍵值的其他版本、相同原文的其他鍵值／模組（用詞一致性檢查，不會被一併取代）。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_format_diff_service import analyze_format_diagnostics
from app.services_impl.moddb_guide_service import is_guide_kind
from app.services_impl.moddb_service import (
    EntryDetail,
    EntryFilter,
    EntryRow,
    ReviewPreviewItem,
    TranslationDB,
    current_settings,
)
from app.services_impl.moddb_smart_review_service import SmartReviewMark
from app.services_impl.moddb_source_service import (
    REVIEW_BADGE_LABELS,
    source_catalog_for,
    source_label,
    source_tone,
)
from app.ui import design, kit
from app.ui.debounce import Debouncer
from app.ui.design import C
from app.ui.page_post import has_background_scheduler
from app.ui.update_scope import isolate, update_regions
from app.views.moddb import editor_drafts, entry_selection, entry_writes
from app.views.moddb.char_inspector import scrolling_list
from app.views.moddb.editor_save_state import shadowed_versions, shadowing_source
from app.views.moddb.entry_filters import (
    ALL_KINDS,
    ALL_MODS,
    apply_filter_options,
    build_filter_controls,
    database_identity,
    entry_filter,
    on_advanced_change,
    open_batch_replace,
    open_bulk_review,
    prepare_entries_snapshot,
    refresh_entry_filter,
)
from app.views.moddb.entry_metadata import build_entry_metadata
from app.views.moddb.entry_row_tile import EntryTilePool
from app.views.moddb.entry_save_result import apply_save_result
from app.views.moddb.format_diff_review import FormatDiffReview
from app.views.moddb.format_diff_viewer import FormatDiffViewer
from app.views.moddb.formatting import (
    format_count,
    kind_label,
    whitespace_note,
)
from app.views.moddb.history_renderer import render_history
from app.views.moddb.mc_text_preview import MinecraftTextPreview
from app.views.moddb.review_scope_dialog import (
    ImpactResult,
    ReviewScopeController,
)
from app.views.moddb.smart_review_marks import (
    ALL_SMART_REVIEW,
    smart_review_mark_chip,
)
from app.views.moddb.source_filter import SourceFilter
from app.views.moddb.species_suggestion import (
    TAB_KEY as SPECIES_TAB_KEY,
)
from app.views.moddb.species_suggestion import (
    TAB_LABEL as SPECIES_TAB_LABEL,
)
from app.views.moddb.species_suggestion import SpeciesSuggestion
from app.views.moddb.suggestions import build_suggestions
from translation_tool.utils.log_unit import (
    log_debug,
)

PAGE_SIZE = 50
LOADING_OPACITY = 0.55  # 背景載入中清單的亮度
SEARCH_DEBOUNCE_SECONDS = 0.35  # 停止輸入多久後自動搜尋
EDIT_DEBOUNCE_SECONDS = 0.15  # 停止輸入多久後重畫格式提示與影響預覽
__all__ = ["EntriesPanel", "SourceFilter"]
ALL_REVIEW_STATES = "__all__"
ACTOR = "使用者"
NO_SOURCE_TEXT = "（原文未知：之後掃描同版本的 jar，會自動補上原文）"


class EntriesPanel(ft.Column):
    """條目校對頁籤。"""

    def __init__(
        self,
        page: ft.Page,
        get_db,
        on_changed=None,
        on_filter_changed=None,
        on_flagged_resolved=None,
    ):
        """``get_db()`` 回傳目前的 ``TranslationDB``（沒有資料庫時回傳 None）。"""
        super().__init__(expand=True, spacing=12)
        self._page = page
        self._get_db = get_db
        self._on_changed = on_changed
        self._on_filter_changed = on_filter_changed
        self._on_flagged_resolved = on_flagged_resolved
        self._refresh_keep_selection = True
        self._filter_only_refresh = False
        self._page_only_refresh = False
        self._refresh_keep_scroll = False
        self._refresh_fallback_index: int | None = None
        self._search_debounce = Debouncer(lambda: self._page, SEARCH_DEBOUNCE_SECONDS)
        self.version: str | None = None
        self.mod_id: str | None = None
        self.kind: str | None = None
        # 批次機翻「特殊字元不一致」的檢視：只列這些條目，並把 AI 譯文預填到譯文框供修正
        self.entry_ids: list[int] | None = None
        self.drafts: dict[int, str] = {}
        self._editor_drafts = editor_drafts.EditorDraftStore()
        self._flagged_batch = 0
        self.state = "all"
        self.query = ""
        self.query_field = "all"
        self.rows: list[EntryRow] = []
        # 目前這一頁條目在最近一次智慧校對批次的處理狀態（entry_id -> mark）
        self.smart_review_marks: dict[int, SmartReviewMark] = {}
        self.total = 0
        self._list_error: str | None = None
        self._data_loading = False
        self.selected: EntryRow | None = None
        self.detail: EntryDetail | None = None
        self.sug_tab = "key"
        self._db_identity = None
        self._render_source_catalog = None
        self._review_preview: list[ReviewPreviewItem] = []
        self._save_running = False
        self._select_ticket = 0
        self._post_refresh_message: str | None = None
        self._edit_debounce = Debouncer(lambda: self._page, EDIT_DEBOUNCE_SECONDS)
        self._loading_id: int | None = None
        self._options_dirty = False
        self.refresh_indicator = ft.Text(
            "背景更新條目資料中…", size=12, color=C.MUTED, visible=False
        )

        self._build_filters()
        self._build_list_card()
        self._build_editor_card()
        self._build_history_card()
        self.controls = [
            # 固定高度：提示出現／消失不能改變版面，否則輸入搜尋時整個畫面會上下跳動
            ft.Container(self.refresh_indicator, height=18),
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
        self.tiles = EntryTilePool(
            PAGE_SIZE,
            self.select,
            on_scroll=self._on_list_scroll,
            scroll_interval=100,
        )
        self.list_view = self.tiles.list_view
        self.list_shell = scrolling_list(self.list_view)
        self.pager = kit.Pager(0, page_size=PAGE_SIZE, on_change=self._on_page)
        self.count_badge = ft.Text("", size=12, color=C.MUTED)
        self.list_card = kit.section_card(
            "條目",
            ft.Column(
                [
                    self.list_shell,
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
            min_lines=6,
            max_lines=16,
            on_change=self._on_text_change,
        )
        self.source_chip = ft.Container()
        self.review_chip = ft.Container()
        self.smart_chip = ft.Container()
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
        self.chars = FormatDiffViewer(
            self._update_format_hints_and_refresh,
            switch_label="顯示特殊字元",
        )
        self.format_review = FormatDiffReview(
            context_label="條目校對",
            editable_control=self.tw_field,
            edit_notice=self.token_hint,
        )
        self.confirm_btn = kit.button(
            "審核（確認目前譯文）",
            "gold",
            icon=ft.Icons.VERIFIED_OUTLINED,
            size="sm",
            on_click=self._confirm,
            tooltip="確認目前譯文為人工已審核（生效來源不變）；同步時只審核目前顯示相同譯文的版本",
        )
        self.clear_review_btn = kit.button(
            "解除人工審核",
            "danger",
            icon=ft.Icons.REMOVE_DONE,
            size="sm",
            on_click=self._clear_review,
            tooltip="只解除目前條目的人工審核標記，保留譯文與來源",
        )
        self.clear_review_btn.visible = False
        self.save_btn = kit.button(
            "儲存", "primary", icon=ft.Icons.CHECK, on_click=self._save, disabled=True
        )

    def _build_editor_card(self) -> None:
        self._build_editor_inputs()
        self._build_editor_actions()
        self.sug_seg = kit.Segmented(
            [
                ("key", "同鍵值・其他版本"),
                ("text", "相同原文・其他鍵值／模組"),
                (SPECIES_TAB_KEY, SPECIES_TAB_LABEL),
            ],
            "key",
            self._on_sug_tab,
        )
        self.species_suggestion = SpeciesSuggestion(
            self._page, self._apply_suggestion, self._on_species_changed
        )
        self.sug_col = ft.Column(spacing=0)
        self.empty_editor = kit.empty_state(
            "選擇左側的條目開始校對",
            "掃描 jar 或完成翻譯後，條目會出現在清單中",
            icon=ft.Icons.EDIT_NOTE,
        )
        self.editor_body = ft.Column(
            [
                self.format_review.control,
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
                    [self.confirm_btn, self.clear_review_btn, self.save_btn],
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
        self.editor_card = isolate(
            kit.section_card(
                "編輯",
                ft.Column([self.empty_editor, self.editor_body], spacing=0),
                icon=ft.Icons.EDIT_OUTLINED,
                tone="em",
            )
        )

    def _build_history_card(self) -> None:
        self.history_col = ft.Column(
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.history_card = isolate(
            kit.section_card(
                "記錄",
                self.history_col,
                icon=ft.Icons.HISTORY,
                tone="gold",
            )
        )

    # --------------------------------------------------------------- 資料載入
    def db(self) -> TranslationDB | None:
        return self._get_db()

    def set_data_loading(self, loading: bool) -> None:
        """初次清單載入期間停用依賴範圍資料的操作。"""
        self._data_loading = loading
        self._update_data_actions()

    def _update_data_actions(self) -> None:
        disabled = self._data_loading or bool(self._list_error)
        for name in (
            "batch_replace_btn",
            "bulk_review_btn",
            "smart_review_btn",
            "species_scan_btn",
        ):
            button = getattr(self, name, None)
            if button is not None:
                button.disabled = disabled

    @staticmethod
    def _database_identity(db):
        return database_identity(db)

    def _entry_filter(self) -> EntryFilter:
        return entry_filter(self)

    def _sync_database_identity(self, identity) -> bool:
        """同步資料庫身分，切換時一次清除所有依資料庫而生的常駐狀態。"""
        if identity == self._db_identity:
            return False
        self.source_filter.reset()
        editor_drafts.reset_editor_for_database_change(self)
        self._db_identity = identity
        return True

    def _on_advanced_change(self) -> None:
        on_advanced_change(self)

    def _open_batch_replace(self, _e=None) -> None:
        open_batch_replace(self, _e)

    def _open_bulk_review(self, _e=None) -> None:
        open_bulk_review(self, _e)

    def _after_batch_replace(self, result) -> None:
        entry_writes.after_data_change(self)

    def _notify_changed(self, *, keep_scroll: bool = False) -> None:
        """通知父層資料已變更，必要時讓下一次完整刷新保留清單位置。"""
        self._refresh_keep_scroll = keep_scroll
        if self._on_changed:
            self._on_changed()

    def refresh(
        self, *, keep_selection: bool = True, keep_scroll: bool = False
    ) -> None:
        """重新載入版本／模組選項與清單（資料庫更新後呼叫）。"""
        self._refresh_keep_scroll = False
        db = self.db()
        if db is None:
            self._sync_database_identity(None)
            self.version_dd.options = []
            self.rows, self.total = [], 0
            self._list_error = None
            self._render_list()
            return
        identity = self._database_identity(db)
        self._render_source_catalog = source_catalog_for(db)
        self._sync_database_identity(identity)
        self._refresh_filter_options()
        self._load_list(keep_selection=keep_selection, keep_scroll=keep_scroll)

    def apply_refresh_snapshot(self, snapshot: dict) -> None:
        """套用 worker 載入的頁籤快照，不在 UI 執行緒查詢 SQLite；呼叫端接著 ``flush()``。"""
        snapshot, refresh_requested = prepare_entries_snapshot(self, snapshot)
        if refresh_requested:
            return
        self._select_ticket += 1  # 進行中的單筆載入已被這份新快照取代
        self._loading_id = None
        self._refresh_fallback_index = None
        self.editor_card.disabled = False
        self.list_shell.opacity = 1.0
        filter_only = snapshot.get("filter_only", False)
        if not filter_only or snapshot.get("filter_options_ready"):
            apply_filter_options(self, snapshot)
            self._options_dirty = True

        self._editor_drafts.remember(self.selected, self.tw_field.value)
        previous_selected_id = self.selected.id if self.selected else None

        self.rows = snapshot.get("rows", [])
        self.smart_review_marks = snapshot.get("smart_review_marks", {})
        self.total = snapshot.get("total", 0)
        self._list_error = snapshot.get("list_error") or snapshot.get("error")
        self.advanced_filters.error_text.value = self._list_error or ""
        self.advanced_filters.error_text.visible = bool(self._list_error)
        self.pager.set_state(self.total, snapshot.get("page", 1))
        self.detail = snapshot.get("detail")
        self.selected = self.detail.entry if self.detail else None
        self._render_list(keep_scroll=bool(snapshot.get("keep_scroll")))
        if (
            not filter_only
            or (self.selected.id if self.selected else None) != previous_selected_id
        ):
            self._show_editor(
                self.detail,
                calculate_impact=False,
                catalog=snapshot.get("source_catalog"),
            )
        else:
            # 背景篩選不重建編輯器，以免覆蓋尚未儲存的草稿；但右側狀態膠囊仍須同步。
            self._refresh_smart_review_chip()
        self.refresh_indicator.value = str(snapshot.get("error") or "")
        self.refresh_indicator.visible = bool(snapshot.get("error"))
        self.set_data_loading(False)
        self._refresh_keep_scroll = False
        if self._post_refresh_message and self.selected is not None:
            self.saved_text.value = self._post_refresh_message
        self._post_refresh_message = None

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

    def _refresh_filter_options(self) -> None:
        """使用與條目清單相同的 scope 重新載入 module/source 選項。"""
        db = self.db()
        if db is None:
            self.version_dd.options = []
            self._load_mods()
            return

        # 讓同步 fallback 與 standalone panel 測試使用和 ModDbView worker snapshot 相同的選項語意。
        # 這裡刻意採用區域 import，因為 panel_refresh 會從本模組匯入 PAGE_SIZE。
        from app.views.moddb.panel_refresh import _resolve_entries_scope

        try:
            criteria = self._entry_filter()
        except ValueError:
            # 顯示給使用者的驗證錯誤由 _load_list 負責。
            # 回報錯誤時保留上一次有效的相依選項，避免在此處再次拋出例外。
            return
        scope = _resolve_entries_scope(
            db,
            {
                "criteria": criteria,
                "version": self.version,
                "mod_id": self.mod_id,
                "kind": self.kind,
            },
            current_settings(),
        )
        apply_filter_options(
            self,
            {
                "identity": self._database_identity(db),
                "source_catalog": self._render_source_catalog,
                "effective_source_codes": scope["source_codes"],
                "filter_options_are_filtered": scope["options_are_filtered"],
                "versions": scope["versions"],
                "version": scope["version"],
                "mods": scope["mods"],
                "kinds": scope["kinds"],
                "source": scope["source"],
                "mod_id": scope["mod_id"],
                "kind": scope["kind"],
            },
        )

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
            self.smart_review_marks = {}
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
            self.smart_review_marks = db.smart_review_marks([r.id for r in self.rows])
            if not self.rows and self.total and page > 1:
                # 最後一頁的最後一筆被處理掉：退回仍有資料的最後一頁
                return self._load_list(
                    page=(self.total - 1) // PAGE_SIZE + 1,
                    keep_selection=keep_selection,
                    keep_scroll=keep_scroll,
                )
        self.pager.set_state(self.total, page)
        keep = keep_selection and self.selected is not None
        current = next(
            (r for r in self.rows if keep and r.id == self.selected.id), None
        )
        if current is None and self.rows:
            if keep_scroll and old_index is not None:
                current = self.rows[min(old_index, len(self.rows) - 1)]
            else:
                current = self.rows[0]
        self._select_now(current.id if current else None)
        self._render_list(keep_scroll=keep_scroll)
        if self._on_filter_changed is None:
            # standalone panel 與同步 fallback 頁面沒有父層 snapshot worker
            # 可以套用相依下拉選項，因此由面板自行更新。
            self._refresh_filter_options()

    def _render_list(self, *, keep_scroll: bool = False) -> None:
        self.count_badge.value = (
            f"日期條件錯誤：{self._list_error}"
            if self._list_error
            else f"{format_count(self.total)} 筆"
        )
        self._update_data_actions()
        self.advanced_filters.set_summary()
        empty = None
        if not self.rows:
            time_hint = (
                None
                if self._list_error or not self.version
                else self.advanced_filters.announce_no_data(self.db, self._entry_filter)
            )
            empty = kit.empty_state(
                "無法套用日期篩選" if self._list_error else "沒有符合的條目",
                self._list_error
                or time_hint
                or "調整上方篩選，或先到「掃描匯入」建立資料",
                icon=ft.Icons.ERROR_OUTLINE
                if self._list_error
                else ft.Icons.SEARCH_OFF,
            )
            empty.key = "entry-empty"
        if self._render_source_catalog is None and self.rows:
            self._render_source_catalog = source_catalog_for(self.db())
        self.tiles.set_rows(
            self.rows,
            selected_id=self._highlight_id(),
            marks=self.smart_review_marks,
            catalog=self._render_source_catalog,
            empty=empty,
        )
        self._scroll_list_to(self._scroll_offset if keep_scroll else 0.0)

    def _highlight_id(self) -> int | None:
        """清單中要標示為選取的條目：詳情還在載入時是即將顯示的那一筆。"""
        if self._loading_id is not None:
            return self._loading_id
        return self.selected.id if self.selected else None

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

    def select(self, entry_id: int | None) -> None:
        entry_selection.select_entry(self, entry_id)

    def _select_now(self, entry_id: int | None) -> None:
        """同步選取（單元測試、沒有背景排程的頁面，以及 ``_load_list`` 內部）。"""
        entry_selection.select_now(self, entry_id)

    def _show_editor(
        self,
        detail: EntryDetail | None,
        *,
        calculate_impact: bool = True,
        catalog=None,
        impact: ImpactResult | None = None,
    ) -> None:
        self.empty_editor.visible = detail is None
        self.editor_body.visible = detail is not None
        self.saved_text.value = ""
        if detail is None:
            self.clear_review_btn.visible = False
            self.clear_review_btn.disabled = True
            self.history_col.controls = [kit.hint_text("尚未選取條目")]
            return
        entry = detail.entry
        self.src_text.value = entry.en_us or NO_SOURCE_TEXT
        self._fill_editor_text(entry)
        if catalog is None:
            db = self.db() if calculate_impact else None
            catalog = self._render_source_catalog or source_catalog_for(db)
        self._fill_status_chips(entry, catalog)
        self._refresh_smart_review_chip()
        self.meta_col.controls = build_entry_metadata(detail)
        self.format_review.render(
            entry.en_us or NO_SOURCE_TEXT,
            self.tw_field.value or "",
            metadata=[self.source_chip, self.review_chip, self.smart_chip],
            kind=entry.kind,
        )
        if impact is not None and impact.entry_id == entry.id:
            self._update_format_hints()
            self._review_controller.apply_impact(impact)
        elif calculate_impact:
            self._update_impact()
        else:
            self._update_editor_actions_without_impact(entry)
        self._render_suggestions(catalog=catalog)
        self._render_history(catalog=catalog)

    def _fill_editor_text(self, entry: EntryRow) -> None:
        """輸入框內容：未儲存草稿 > 本次機翻的預填譯文 > 生效譯文。"""
        editor_draft = self._editor_drafts.get(entry.id)
        machine_draft = self.drafts.get(entry.id) if not entry.zh_tw else None
        if editor_draft is not None:
            self.tw_field.value = editor_draft
        elif machine_draft is not None:
            self.tw_field.value = machine_draft
            self.saved_text.value = (
                "已預填本次機翻的 AI 譯文（特殊字元與原文不一致，尚未寫入）"
            )
        else:
            self.tw_field.value = entry.zh_tw

    def _fill_status_chips(self, entry: EntryRow, catalog) -> None:
        """來源與審核狀態膠囊（智慧校對標籤另由 ``_refresh_smart_review_chip`` 處理）。"""
        self.source_chip.content = kit.chip(
            source_label(entry.source, catalog) if entry.zh_tw else "尚無譯文",
            source_tone(entry.source),
        )
        badge = (
            REVIEW_BADGE_LABELS.get(entry.review_status or "") if entry.zh_tw else None
        )
        self.review_chip.content = (
            kit.chip(badge, "em" if entry.review_status == "reviewed" else "gold")
            if badge
            else None
        )

    def _refresh_smart_review_chip(self) -> None:
        """只更新目前選中條目的智慧校對標籤，不重建編輯器內容。"""
        entry_id = self.selected.id if self.selected else None
        self.smart_chip.content = smart_review_mark_chip(
            self.smart_review_marks.get(entry_id) if entry_id is not None else None
        )

    def _update_editor_actions_without_impact(self, entry: EntryRow) -> None:
        """Set initial editor state without a cross-version preview query."""
        self._review_controller.update_actions_without_impact(entry)

    def _render_suggestions(self, *, catalog=None) -> None:
        if self.detail is None:
            return
        if self.sug_tab == SPECIES_TAB_KEY:
            self.sug_col.controls = self.species_suggestion.build(self.detail.entry)
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

    def _on_smart_review_filter(self) -> None:
        refresh_entry_filter(self)

    def _on_review_status(self) -> None:
        refresh_entry_filter(self)

    def _on_state(self, key: str) -> None:
        self.state = key
        refresh_entry_filter(self)

    def _on_search_field(self, e) -> None:
        """切換搜尋內容欄位，讓目前搜尋文字只比對指定欄位。"""
        value = str(e.control.value or "all")
        if value == self.query_field:
            return
        self.query_field = value
        refresh_entry_filter(self)

    def _on_search_input(self, e) -> None:
        """輸入時即時搜尋：停止輸入一小段時間後才查詢，避免每個字都打資料庫。"""
        if (e.control.value or "").strip() == self.query:
            self._search_debounce.cancel()
            return
        self._search_debounce.call(self._apply_search, e.control)

    def _apply_search(self, control) -> None:
        query = (control.value or "").strip()
        if query != self.query:
            self.query = query
            refresh_entry_filter(self)

    def _on_search(self, e) -> None:
        """按 Enter：立即以目前輸入搜尋（取消尚在等待的即時搜尋）。"""
        self._search_debounce.cancel()
        self.query = (e.control.value or "").strip()
        refresh_entry_filter(self)

    def _on_page(self, page: int) -> None:
        refresh_entry_filter(self, page=page, keep_selection=False, page_only=True)

    def _on_species_changed(self) -> None:
        if self.sug_tab == SPECIES_TAB_KEY:
            self._render_suggestions()
            self._flush_editor()

    def open_species_entry(self, entry_id: int) -> None:
        """從學名掃描視窗跳到條目，並直接顯示「學名」建議分頁。"""
        self.sug_tab = SPECIES_TAB_KEY
        self.sug_seg.select(SPECIES_TAB_KEY)
        self.select(entry_id)
        self._flush_editor()  # 掃描視窗關閉時的重繪發生在選取之前

    def _on_sug_tab(self, key: str) -> None:
        self.sug_tab = key
        self._render_suggestions()
        self._flush_editor()

    def _on_text_change(self, _e=None) -> None:
        """輸入框內容改變（逐字觸發）。

        只立刻更新儲存／審核按鈕；格式差異、Minecraft 預覽與影響預覽等較重的內容
        等使用者停筆一小段時間後才重畫，連續輸入時只畫最後一次。
        """
        if self._loading_id is not None:
            return  # 詳情載入中，輸入框還是上一筆的內容
        self._editor_drafts.remember(self.selected, self.tw_field.value)
        if not has_background_scheduler(self._page):
            self._update_impact()
            self._flush_editor()
            return
        self._review_controller.update_buttons()
        update_regions(self.save_btn, self.confirm_btn)
        self._edit_debounce.call(self._refresh_after_edit)

    def _on_text_replaced(self) -> None:
        """程式端換了輸入框內容（還原輸入、套用建議、複製原文）：立刻重畫，不等停筆。"""
        self._editor_drafts.remember(self.selected, self.tw_field.value)
        self._edit_debounce.cancel()
        self._refresh_after_edit()

    def _refresh_after_edit(self) -> None:
        """重畫格式提示與預覽，並在背景重算儲存／審核的影響預覽。"""
        if self.selected is None or self._loading_id is not None:
            return
        self._update_format_hints()
        self._request_impact()
        self._flush_editor()

    def _request_impact(self) -> None:
        self._review_controller.request_impact()

    def _on_sync_change(self, _e=None) -> None:
        """切換「同步其他版本」：影響預覽要跟著重算（儲存時讀的是這個開關）。"""
        if not has_background_scheduler(self._page):
            self._update_impact()
            self._flush_editor()
            return
        self._request_impact()

    def _reset_input(self) -> None:
        if self.selected is not None:
            self._editor_drafts.discard(self.selected.id)
            self.tw_field.value = self.selected.zh_tw
            self._on_text_replaced()

    def _apply_suggestion(self, text: str) -> None:
        self.tw_field.value = text
        self._on_text_replaced()

    def _step(self, delta: int) -> None:
        if self.selected is None:
            return
        ids = [r.id for r in self.rows]
        if self.selected.id not in ids:
            return
        idx = ids.index(self.selected.id) + delta
        if 0 <= idx < len(ids):
            self.select(ids[idx])

    def pending_text(self) -> str:
        """輸入框的內容（原樣；前後空白與換行是譯文的一部分）。全是空白視為空。"""
        value = self.tw_field.value or ""
        return value if value.strip() else ""

    def _update_format_hints(self) -> None:
        """換行、`§` 格式碼、`%s` 佔位符與前後空白的提醒，以及 `§` 顏色預覽。"""
        entry = self.selected
        text = self.tw_field.value or ""
        notes: list[str] = []
        diff_result = None
        if entry is not None and entry.en_us and text.strip():
            diff_result = analyze_format_diagnostics(entry.en_us, text, kind=entry.kind)
            if diff_result.legacy_messages:
                notes.append(
                    "與原文的特殊字元不一致：" + "、".join(diff_result.legacy_messages)
                )
            if diff_result.soft_newline_counts is not None:
                notes.append(diff_result.summary_lines[-1])
            if diff_result.extra_guide_issues:
                notes.append(
                    "指南標記與原文不一致：" + "、".join(diff_result.extra_guide_issues)
                )
        is_guide = entry is not None and is_guide_kind(entry.kind)
        space = whitespace_note(text)
        if space:
            notes.append(space)
        self.token_hint.value = "；".join(notes)
        self.token_hint.visible = bool(notes)
        self.mc_preview.render(text, markdown=is_guide)
        self.chars.render(
            entry.en_us if entry else "",
            text,
            diff_result,
            kind=entry.kind if entry else None,
        )
        self.format_review.render(
            entry.en_us if entry else "",
            text,
            diff_result,
            metadata=[self.source_chip, self.review_chip, self.smart_chip],
            kind=entry.kind if entry else "lang",
        )

    def _update_format_hints_and_refresh(self) -> None:
        self._update_format_hints()
        self._flush_editor()

    def _copy_source(self) -> None:
        """把原文（連同換行與前後空白）原樣放進譯文框，再手動翻譯。"""
        if self.selected is not None and self.selected.en_us:
            self.tw_field.value = self.selected.en_us
            self._on_text_replaced()

    def _update_impact(self) -> None:
        self._review_controller.update_impact()

    def _save(self, _e=None) -> None:
        entry_writes.save_entry(self)

    def _abandon_save(self) -> None:
        """儲存結果無法排回畫面時，解除儲存中狀態讓編輯區可再操作。"""
        self._save_running = False
        self.saved_text.value = ""
        self._update_impact()

    def _apply_save_result(
        self, db, entry, text, done, error, flagged_batch, *, source_info=None
    ) -> None:
        """套用儲存結果；``source_info`` 為背景已算好的來源優先序資訊，省略時就地查詢。"""
        apply_save_result(
            self, db, entry, text, done, error, flagged_batch, source_info=source_info
        )

    _shadowed_versions = staticmethod(shadowed_versions)
    _shadowing_source = staticmethod(shadowing_source)

    def prepare_reload_after_change(self, message: str | None = None) -> None:
        """寫入完成、由上層背景快照重載目前頁前：維持頁碼、捲動位置與（同位置的）選取。"""
        self._refresh_keep_selection = True
        self._refresh_keep_scroll = True
        self._refresh_fallback_index = self._selected_index()
        self._post_refresh_message = message

    def reload_page_in_place(self) -> None:
        """同步重載目前頁（沒有背景排程的頁面）。"""
        self._load_list(page=self.pager.current_page, keep_scroll=True)

    def _selected_index(self) -> int | None:
        if self.selected is None:
            return None
        return next(
            (i for i, r in enumerate(self.rows) if r.id == self.selected.id), None
        )

    def _resolve_flagged(self, entry_id: int, flagged_batch: int) -> None:
        """機翻「特殊字元不一致」清單裡的條目已手動儲存：移出清單並通知機翻頁扣掉數量。

        條目編號只在同一個資料庫、同一份清單內有意義：儲存期間換過清單就不動新清單。
        """
        if flagged_batch != self._flagged_batch or entry_id not in (
            self.entry_ids or ()
        ):
            return
        self.entry_ids = [i for i in self.entry_ids if i != entry_id]
        self.drafts = {k: v for k, v in self.drafts.items() if k != entry_id}
        self._show_flagged_banner()
        if self._on_flagged_resolved is not None:
            self._on_flagged_resolved(entry_id)

    def _confirm(self, _e=None) -> None:
        self._review_controller.confirm(_e)

    def _clear_review(self, _e=None) -> None:
        self._review_controller.clear_review(_e)

    def _revert(self, history_id: int) -> None:
        entry_writes.revert_history(self, history_id)

    def _revert_batch(self, batch_id: str) -> None:
        entry_writes.revert_batch(self, batch_id)

    def show_filter(self, state: str) -> None:
        """從其他頁籤（總覽／掃描結果）跳進來時套用狀態篩選。"""
        self._set_flagged(None, {})
        self.state = state
        self.state_seg.select(state)

    def show_flagged(
        self, entry_ids: list[int], drafts: dict[int, str], version: str
    ) -> None:
        """從批次機翻跳進來：只列「特殊字元不一致、沒寫入」的條目，並預填 AI 譯文供修正。"""
        self._sync_database_identity(self._database_identity(self.db()))
        self.version = version
        self.mod_id = None
        self.kind = None
        self.version_dd.value = version
        self.mod_dd.value = ALL_MODS
        self.kind_dd.value = ALL_KINDS
        self.query = ""
        self.search.value = ""
        self.query_field = "all"
        self.search_field_dd.value = "all"
        self.state = "none"
        self.state_seg.select("none")
        self.source_filter.reset()
        self.review_filter.value = ALL_REVIEW_STATES
        self.smart_review_filter.value = ALL_SMART_REVIEW
        self.advanced_filters.reset()
        self._set_flagged(list(entry_ids), dict(drafts))

    def clear_flagged(self) -> None:
        """清除「特殊字元不一致」篩選，回到一般清單。"""
        self._set_flagged(None, {})
        refresh_entry_filter(self)

    def _set_flagged(self, entry_ids: list[int] | None, drafts: dict[int, str]) -> None:
        self._flagged_batch += 1
        self.entry_ids = entry_ids
        self.drafts = drafts
        self._show_flagged_banner()

    def _show_flagged_banner(self) -> None:
        self.flagged_banner.visible = self.entry_ids is not None
        if self.entry_ids is not None:
            self.flagged_text.value = (
                f"只顯示本次機翻「特殊字元與原文不一致」的 {len(self.entry_ids):,} 筆"
                "（AI 譯文尚未寫入，已預填在譯文框；修正後儲存即可，或按「清除篩選」）"
            )

    def flush(self) -> None:
        """把整個頁籤的變更送到畫面。

        篩選列、清單、編輯區與記錄是各自的更新邊界（頁面層級的 update 不會走進去）；
        篩選選項沒變（例如只翻頁）時不更新篩選列。
        """
        regions = [self, self.list_view, self.editor_card, self.history_card]
        if self._options_dirty:
            regions.append(self.filter_card)
            self._options_dirty = False
        update_regions(*regions)

    def show_loading(self) -> None:
        """背景載入中：提示文字已設好，清單變暗表示目前內容即將被取代。"""
        self.list_shell.opacity = LOADING_OPACITY
        update_regions(self.refresh_indicator, self.list_shell, self.filter_card)

    def clear_loading(self) -> None:
        self.list_shell.opacity = 1.0

    def flush_all(self) -> None:
        """每個區塊都更新（來源不明的變更）。"""
        self._options_dirty = True
        self.flush()

    def _flush_list(self) -> None:
        """清單列、頁碼與筆數徽章。"""
        update_regions(self.list_view, self.pager, self.count_badge)

    def _flush_editor(self) -> None:
        """編輯區與記錄欄。"""
        update_regions(self.editor_card, self.history_card)

    def _safe_update(self) -> None:
        self.flush_all()
