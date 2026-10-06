"""app/views/moddb/entries_panel.py：條目校對（左：清單，中：編輯區＋建議，右：記錄）。

資料與規則都在 ``translation_tool.translation_db``；這裡只負責畫面與事件：

- 手動儲存會寫入「人工」來源，並（可選）同步所有版本中原文相同的條目；儲存前先預覽影響範圍。
- 建議分兩類：同鍵值的其他版本、相同原文的其他鍵值／模組（用詞一致性檢查，不會被一併取代）。
"""

from __future__ import annotations

import sqlite3

import flet as ft

from app.services_impl.moddb_service import (
    SRC_MANUAL,
    EntryDetail,
    EntryRow,
    TranslationDB,
    current_settings,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.mc_text import mc_text_spans
from app.ui.snack import show_snack
from app.views.moddb.char_inspector import CharInspector, scrolling_list
from app.views.moddb.formatting import (
    KIND_LABELS,
    STATE_LABELS,
    STATE_TONES,
    format_count,
    impact_text,
    shorten,
    source_label,
    source_tone,
    token_issues,
    whitespace_note,
)
from app.views.moddb.source_filter import SourceFilter
from app.views.moddb.suggestions import build_suggestions
from translation_tool.utils.log_unit import (
    log_debug,
    log_exception,
    log_info,
    log_warning,
)

PAGE_SIZE = 50
ALL_MODS = "全部模組"
ACTOR = "使用者"
NO_SOURCE_TEXT = "（原文未知：之後掃描同版本的 jar，會自動補上原文）"


class EntriesPanel(ft.Column):
    """條目校對頁籤。"""

    def __init__(self, page: ft.Page, get_db, on_changed=None):
        """``get_db()`` 回傳目前的 ``TranslationDB``（沒有資料庫時回傳 None）。"""
        super().__init__(expand=True, spacing=12)
        self._page = page
        self._get_db = get_db
        self._on_changed = on_changed
        self.version: str | None = None
        self.mod_id: str | None = None
        self.state = "all"
        self.query = ""
        self.rows: list[EntryRow] = []
        self.total = 0
        self.selected: EntryRow | None = None
        self.detail: EntryDetail | None = None
        self.sug_tab = "key"

        self._build_filters()
        self._build_list_card()
        self._build_editor_card()
        self._build_history_card()
        self.controls = [
            self.filter_card,
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

    # ------------------------------------------------------------------ 建構
    def _build_filters(self) -> None:
        self.version_dd = kit.dropdown(
            label="遊戲版本", dense=True, width=220, on_select=self._on_version
        )
        self.mod_dd = kit.dropdown(
            label="模組", dense=True, width=220, on_select=self._on_mod
        )
        self.source_filter = SourceFilter(self._on_source)
        self.search = kit.text_field(
            "搜尋", hint="原文、譯文或鍵值", expand=True, on_submit=self._on_search
        )
        self.state_seg = kit.Segmented(
            [(k, v) for k, v in STATE_LABELS.items()], "all", self._on_state
        )
        self.filter_card = kit.section_card(
            None,
            ft.Row(
                [
                    self.version_dd,
                    self.mod_dd,
                    self.source_filter.dropdown,
                    self.search,
                    self.state_seg,
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_list_card(self) -> None:
        # 清單高度跟著視窗伸縮（expand），捲軸常駐顯示（全域主題預設只在滑過時出現）
        self.list_view = ft.ListView(spacing=0, expand=True)
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
        self.mc_preview = ft.Text("", size=14, selectable=True, visible=False)
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
            "原文相同的版本一併取代（可還原）",
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
            tooltip="把目前的譯文確認為人工校對：優先於其他來源，並同步原文相同的版本",
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
                self.mc_preview,
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
        self.history_col = ft.Column(spacing=10)
        self.history_card = kit.section_card(
            "記錄",
            self.history_col,
            icon=ft.Icons.HISTORY,
            tone="gold",
        )

    # --------------------------------------------------------------- 資料載入
    def db(self) -> TranslationDB | None:
        return self._get_db()

    def refresh(self, *, keep_selection: bool = True) -> None:
        """重新載入版本／模組選項與清單（資料庫更新後呼叫）。"""
        db = self.db()
        if db is None:
            self.version_dd.options = []
            self.rows, self.total = [], 0
            self._render_list()
            self._show_editor(None)
            return
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

    def _load_mods(self) -> None:
        db = self.db()
        mods = db.mods(self.version) if (db and self.version) else []
        kit.set_dropdown_options(
            self.mod_dd, [(ALL_MODS, ALL_MODS), *((m, m) for m in mods)]
        )
        if self.mod_id not in mods:
            self.mod_id = None
        self.mod_dd.value = self.mod_id or ALL_MODS

    def _load_list(self, *, page: int = 1, keep_selection: bool = True) -> None:
        db = self.db()
        if db is None or not self.version:
            self.rows, self.total = [], 0
        else:
            self.rows, self.total = db.list_entries(
                self.version,
                mod_id=self.mod_id,
                state=self.state,
                query=self.query,
                source=self.source_filter.code,
                limit=PAGE_SIZE,
                offset=(page - 1) * PAGE_SIZE,
            )
        self.pager.set_state(self.total, page)
        self._render_list()
        keep = keep_selection and self.selected is not None
        current = next(
            (r for r in self.rows if keep and r.id == self.selected.id), None
        )
        if current is None and self.rows:
            current = self.rows[0]
        self.select(current.id if current else None)

    def _render_list(self) -> None:
        self.count_badge.value = f"{format_count(self.total)} 筆"
        tiles = [self._row_tile(r) for r in self.rows] or [
            kit.empty_state(
                "沒有符合的條目",
                "調整上方篩選，或先到「掃描匯入」建立資料",
                icon=ft.Icons.SEARCH_OFF,
            )
        ]
        # 換篩選／換頁後清單內容大幅改變：全新 key 避免 Flet 配對舊項目而殘留上一份清單
        self.list_view.controls = kit.rekey(tiles, "entry")

    def _row_tile(self, row: EntryRow) -> ft.Control:
        tone = design.tone(STATE_TONES[row.state])
        return ft.Container(
            data=row.id,
            on_click=lambda _e, i=row.id: self.select(i),
            ink=True,
            padding=ft.Padding.symmetric(horizontal=14, vertical=9),
            bgcolor=C.EM_BG if self.selected and self.selected.id == row.id else None,
            border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
            content=ft.Row(
                [
                    ft.Container(
                        width=8,
                        height=8,
                        border_radius=4,
                        bgcolor=tone.fg,
                        margin=ft.Margin.only(top=5),
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                shorten(row.en_us, 48) if row.en_us else "（原文未知）",
                                size=13,
                                weight=ft.FontWeight.W_500,
                                color=C.TEXT if row.en_us else C.DIM,
                            ),
                            ft.Text(
                                shorten(row.zh_tw, 48) if row.zh_tw else "（未翻譯）",
                                size=12,
                                color=C.MUTED,
                            ),
                            kit.mono_text(shorten(row.key, 46), size=10.5, color=C.DIM),
                        ],
                        spacing=1,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        )

    # ------------------------------------------------------------------ 選取
    def select(self, entry_id: int | None) -> None:
        db = self.db()
        detail = db.entry_detail(entry_id) if (db and entry_id) else None
        self.detail = detail
        self.selected = detail.entry if detail else None
        self._show_editor(detail)
        self._render_list_selection()

    def _render_list_selection(self) -> None:
        for tile in self.list_view.controls:
            if isinstance(tile, ft.Container) and tile.data is not None:
                tile.bgcolor = (
                    C.EM_BG if self.selected and tile.data == self.selected.id else None
                )

    def _show_editor(self, detail: EntryDetail | None) -> None:
        self.empty_editor.visible = detail is None
        self.editor_body.visible = detail is not None
        self.saved_text.value = ""
        if detail is None:
            self.history_col.controls = [kit.hint_text("尚未選取條目")]
            return
        entry = detail.entry
        self.src_text.value = entry.en_us or NO_SOURCE_TEXT
        self.tw_field.value = entry.zh_tw
        self.source_chip.content = kit.chip(
            source_label(entry.source) if entry.zh_tw else "尚無譯文",
            source_tone(entry.source),
        )
        self.meta_col.controls = [
            ft.Row(
                [kit.section_label("鍵值"), kit.mono_text(entry.key, size=12)],
                spacing=10,
                wrap=True,
            ),
            ft.Row(
                [
                    kit.section_label("模組"),
                    ft.Text(
                        f"{entry.mod_id}（{KIND_LABELS.get(entry.kind, entry.kind)}）",
                        size=12.5,
                        color=C.TEXT,
                    ),
                ],
                spacing=10,
            ),
            ft.Row(
                [
                    kit.section_label("出現於"),
                    *(
                        kit.chip(v, "em" if v == entry.mc_version else "neutral")
                        for v in detail.versions
                    ),
                ],
                spacing=6,
                wrap=True,
            ),
            *(
                ft.Column(
                    [
                        kit.chip("掃描到原文已變動（尚未採用）", "gold"),
                        kit.mono_text(f"新原文：{c.new_en}", size=12),
                    ],
                    spacing=4,
                )
                for c in detail.src_changes[:1]
            ),
        ]
        self._update_impact()
        self._render_suggestions()
        self._render_history()

    def _render_suggestions(self) -> None:
        if self.detail is None:
            return
        self.sug_col.controls = build_suggestions(
            self.detail, self.sug_tab, self._apply_suggestion
        )

    def _render_history(self) -> None:
        detail = self.detail
        if detail is None:
            return
        out: list[ft.Control] = []
        for h in detail.history:
            action = "手動更新" if h.action == "manual" else "還原"
            body = ft.Text(
                (
                    f"{shorten(h.old_zh_tw, 20)} → {shorten(h.new_zh_tw, 20)}"
                    if h.old_zh_tw
                    else shorten(h.new_zh_tw, 40)
                ),
                size=12.5,
                color=C.TEXT,
            )
            controls: list[ft.Control] = [
                ft.Row(
                    [
                        ft.Text(
                            f"{action}・{h.actor or '—'}", size=11.5, color=C.MUTED
                        ),
                        ft.Text(h.at[:16], size=11, color=C.DIM),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                body,
            ]
            if h.note:
                controls.append(ft.Text(h.note, size=11, color=C.DIM))
            if h.action == "manual":
                controls.append(
                    kit.button(
                        "還原這次更新",
                        "ghost",
                        size="sm",
                        on_click=lambda _e, i=h.id: self._revert(i),
                    )
                )
            out.append(ft.Column(controls, spacing=3))
        if detail.translations:
            out.append(kit.section_label("各來源譯文"))
            for t in detail.translations:
                out.append(
                    ft.Row(
                        [
                            kit.chip(source_label(t.source), source_tone(t.source)),
                            ft.Text(
                                shorten(t.zh_tw, 28), size=12, color=C.TEXT, expand=True
                            ),
                        ],
                        spacing=6,
                    )
                )
        self.history_col.controls = out or [kit.hint_text("還沒有異動記錄")]

    # ------------------------------------------------------------------ 事件
    def _on_version(self, e) -> None:
        self.version = e.control.value
        self.mod_id = None
        self._load_mods()
        self._load_list()
        self._safe_update()

    def _on_mod(self, e) -> None:
        value = e.control.value
        self.mod_id = None if value in (None, "", ALL_MODS) else value
        self._load_list()
        self._safe_update()

    def _on_source(self) -> None:
        self._load_list()
        self._safe_update()

    def _on_state(self, key: str) -> None:
        self.state = key
        self._load_list()
        self._safe_update()

    def _on_search(self, e) -> None:
        self.query = (e.control.value or "").strip()
        self._load_list()
        self._safe_update()

    def _on_page(self, page: int) -> None:
        self._load_list(page=page, keep_selection=False)
        self._safe_update()

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
        has_codes = "§" in text
        self.mc_preview.visible = has_codes
        self.mc_preview.value = ""
        self.mc_preview.spans = mc_text_spans(text, C.TEXT, 14) if has_codes else []
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
        self._update_format_hints()
        entry, db = self.selected, self.db()
        text = self.pending_text()
        changed = bool(entry and db and text and text != entry.zh_tw)
        self.save_btn.disabled = not changed
        # 沒改動、但目前譯文不是人工來源：可以「審核」，把它確認為人工校對（同樣會同步相同原文的版本）
        self.confirm_btn.visible = bool(
            entry and entry.zh_tw and entry.source != SRC_MANUAL and not changed
        )
        if not changed:
            self.impact_box.visible = False
            return
        impacts = db.preview_manual(entry.id, text, propagate=self.sync_row.value)
        self.impact_text.value = impact_text(entry.mc_version, text, impacts)
        self.impact_box.visible = True

    def _save(self, _e=None) -> None:
        entry, db = self.selected, self.db()
        text = self.pending_text()
        if entry is None or db is None or not text:
            return
        try:
            done = db.save_manual(
                entry.id, text, actor=ACTOR, propagate=self.sync_row.value
            )
        except ValueError as exc:
            log_warning(
                f"Mod 資料庫儲存被拒絕：{entry.mc_version} {entry.mod_id} {entry.key}（{exc!r}）"
            )
            show_snack(self._page, str(exc), C.RED)
            return
        except sqlite3.Error as exc:
            log_exception(
                f"Mod 資料庫儲存失敗：{entry.mc_version} {entry.mod_id} {entry.key}"
            )
            show_snack(
                self._page,
                f"儲存失敗（資料庫錯誤：{exc}）。可能是資料庫被其他程式鎖住，請稍後再試；詳情見後台 log",
                C.RED,
            )
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
        self._load_list(page=self.pager.current_page)
        self.saved_text.value = message
        show_snack(self._page, message, C.EM, text_color=C.ON_EM)
        if self._on_changed:
            self._on_changed()
        self._safe_update()

    def _confirm(self, _e=None) -> None:
        """審核：不改文字，直接把目前譯文寫成人工來源。"""
        if self.selected is not None and self.selected.zh_tw:
            self.tw_field.value = self.selected.zh_tw
            self._save()

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
        self._load_list(page=self.pager.current_page)
        show_snack(
            self._page,
            f"已還原 {count} 筆" if count else "沒有可還原的內容（之後已被再次修改）",
            C.EM if count else C.GOLD,
        )
        if self._on_changed:
            self._on_changed()
        self._safe_update()

    def show_filter(self, state: str) -> None:
        """從其他頁籤（總覽／掃描結果）跳進來時套用狀態篩選。"""
        self.state = state
        self.state_seg.select(state)

    def _safe_update(self) -> None:
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響資料操作
            log_debug(f"EntriesPanel update 略過：{exc}")
