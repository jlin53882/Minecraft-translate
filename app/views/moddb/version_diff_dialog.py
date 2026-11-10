"""ModDB「版本差異審核」對話框：比較目標版本與其他版本已審核的相同內容。

內容相同＝類型、模組、鍵值、原文都相同。參考版本預設取每個條目最近一次被審核的版本，
也可以固定成某個版本。「差異」頁籤列出參考譯文與目標不同者；「衝突」頁籤列出各版本
已審核譯文彼此不同者，只列出、由使用者逐筆選擇。套用與批次套用都可整批還原。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import (
    REFERENCE_LATEST,
    ApplyItem,
    CrossVersionRow,
    QueryCancelled,
    VersionRef,
    pack_label,
)
from app.services_impl.moddb_source_service import source_catalog_for, source_label
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    OperationPresentation,
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
from app.ui.page_post import post_to_ui
from app.ui.snack import show_snack
from app.ui.update_scope import isolate, update_regions
from app.views.moddb.formatting import format_count, kind_label
from app.views.moddb.version_diff_text import changed_ranges, text_spans
from translation_tool.utils.log_unit import log_exception

PAGE_SIZE = 20
ACTOR = "使用者"
ALL_MODS = "__all__"
TAB_DIFF = "diff"
TAB_CONFLICT = "conflict"
TAB_SOURCE = "source"
LATEST_LABEL = "各條目最近審核的版本"
LATEST_KEY = "__latest__"
PACK_SEP = "	"  # 參考選項的值：「遊戲版本	整合包資料夾」


def split_rows(
    rows: list[CrossVersionRow],
) -> tuple[list[CrossVersionRow], list[CrossVersionRow]]:
    """把差異清單分成（可直接套用的差異, 各版本不一致的衝突）；原文不同的另列。"""
    same_source = [row for row in rows if not row.source_changed]
    diffs = [row for row in same_source if not row.conflict]
    return diffs, [row for row in same_source if row.conflict]


def source_changed_rows(rows: list[CrossVersionRow]) -> list[CrossVersionRow]:
    """參考的原文與目標不同（不只差大小寫或空白）的條目：只能逐筆判斷後套用。"""
    return [row for row in rows if row.source_changed]


def selectable(row: CrossVersionRow, include_reviewed: bool) -> bool:
    """可勾選批次套用：衝突與原文不同要逐筆判斷；目標已審核者需先勾「包含目標已審核」。"""
    return (
        not row.conflict
        and not row.source_changed
        and (include_reviewed or not row.target_reviewed)
    )


def selected_items(
    rows: list[CrossVersionRow], selected: set[int], include_reviewed: bool
) -> list[ApplyItem]:
    """批次套用的內容：已勾選且目前仍可勾選的差異，參考譯文取各條目的預設參考。"""
    return [
        ApplyItem.from_row(row, row.reference)
        for row in rows
        if row.entry_id in selected and selectable(row, include_reviewed)
    ]


def _time_text(ref: VersionRef) -> str:
    return ref.reviewed_at[:16] if ref.reviewed_at else "審核時間不明"


def split_reference(value: str | None) -> tuple[str, str]:
    """參考下拉選單的值 → （遊戲版本, 整合包資料夾）；各條目最近審核者為空字串。"""
    if value in (None, "", LATEST_KEY):
        return REFERENCE_LATEST, ""
    version, _, pack = str(value).partition(PACK_SEP)
    return version, pack


def target_options(
    versions: list[str], packs: dict[str, list[str]]
) -> list[tuple[str, str]]:
    """目標下拉選單：遊戲版本，以及其中的整合包版本（只比較該整合包資料夾的條目）。"""
    options: list[tuple[str, str]] = []
    for version in versions:
        options.append((version, version))
        options += [
            (f"{version}{PACK_SEP}{pack}", f"{version} · {pack}")
            for pack in packs.get(version, [])
        ]
    return options


def reference_options(
    versions: list[str], packs: dict[str, list[str]], target: str | None
) -> list[tuple[str, str]]:
    """參考下拉選單：各條目最近審核者、固定遊戲版本、固定遊戲版本中的某個整合包版本。

    同一個遊戲版本可以有多個整合包版本（例如 8.1～8.5 都是 1.21.1），所以整合包版本
    另列，包含目標所在的遊戲版本（參考同遊戲版本的其他整合包版本）。
    """
    options = [(LATEST_KEY, LATEST_LABEL)]
    for version in versions:
        if version != target:
            options.append((version, f"固定 {version}"))
        options += [
            (f"{version}{PACK_SEP}{pack}", f"固定 {version} · {pack}")
            for pack in packs.get(version, [])
        ]
    return options


def _ref_name(kind: str, ref: VersionRef) -> str:
    """參考的名稱：遊戲版本；KubeJS／FTB 另標整合包資料夾（同一個遊戲版本可有多個整合包版本）。"""
    pack = pack_label(kind, ref.mod_id)
    return f"{ref.version} · {pack}" if pack else ref.version


class VersionDiffDialog:
    """版本差異審核：預設以各條目最近審核的版本當參考，可固定特定版本。"""

    def __init__(
        self,
        page: ft.Page,
        get_db,
        version: str | None,
        on_complete,
        *,
        last_history_id: int | None = None,
    ) -> None:
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self.rows: list[CrossVersionRow] = []
        self.tab = TAB_DIFF
        self.current_page = 1
        self.last_history_id = last_history_id  # 匯入後自動沿用的批次，可直接復原
        self.selected: set[int] = set()
        self._confirm_overwrite = False
        self._confirm_row: tuple[int, int] | None = (
            None  # 逐筆套用要再確認的（條目, 參考）
        )
        self._busy = False
        self._closed = False
        self._generation = 0
        self._catalog = source_catalog_for(self.get_db())
        self._build_controls(version)
        self._build_dialog()
        self._reload()

    # ------------------------------------------------------------------ 版面
    def _build_controls(self, version: str | None) -> None:
        db = self.get_db()
        versions = list(db.versions()) if db is not None else []
        self.version_dd = kit.dropdown(
            label="目標版本", dense=True, width=300, on_select=self._on_scope
        )
        packs = {v: db.pack_versions(v) for v in versions} if db is not None else {}
        options = target_options(versions, packs)
        kit.set_dropdown_options(self.version_dd, options)
        self.version_dd.value = (
            version if version in {key for key, _ in options} else None
        )
        self.ref_dd = kit.dropdown(
            label="參考版本", dense=True, width=240, on_select=self._on_scope
        )
        self.ref_dd.value = LATEST_KEY
        self._set_ref_options(versions)
        self.mod_dd = kit.searchable_dropdown(
            label="模組", dense=True, width=220, on_select=self._on_scope
        )
        kit.set_dropdown_options(self.mod_dd, [(ALL_MODS, "全部模組")])
        self.mod_dd.value = ALL_MODS
        self.tabs = kit.Segmented(
            [(TAB_DIFF, "差異"), (TAB_CONFLICT, "衝突"), (TAB_SOURCE, "原文不同")],
            TAB_DIFF,
            self._on_tab,
        )
        self.summary = ft.Text("", size=13, weight=ft.FontWeight.BOLD)
        # 內容相同卻沒被列為差異的條目與原因（來源語系不相容、參考尚未審核）
        self.exclusion_note = ft.Text(
            "", size=12, color=C.GOLD, selectable=True, visible=False
        )
        self.exclusions = None
        self.status = ft.Text("", size=12, color=C.MUTED)
        self.progress = ft.ProgressBar(visible=False)
        self.list_col = ft.Column([], spacing=8)
        self.pager = kit.Pager(0, page_size=PAGE_SIZE, on_change=self._on_page)
        self.pager.visible = False
        self.include_reviewed = ft.Checkbox(
            label="包含目標已審核的條目（會覆蓋已審核譯文）",
            value=False,
            on_change=self._on_include_reviewed,
        )
        self.select_all_btn = kit.button(
            "全選", "ghost", size="sm", on_click=self._select_all
        )
        self.clear_btn = kit.button(
            "取消全選", "ghost", size="sm", on_click=self._clear_selection
        )
        self.selection_row = ft.Row(
            [self.include_reviewed, self.select_all_btn, self.clear_btn],
            spacing=10,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.apply_selected_btn = kit.button(
            "套用已勾選",
            "primary",
            icon=ft.Icons.DOWNLOAD_DONE,
            on_click=self._apply_selected,
            disabled=True,
        )
        self.revert_btn = kit.button(
            "復原上一次沿用／套用", "secondary", on_click=self._revert
        )
        self.revert_btn.visible = self.last_history_id is not None
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

    def _set_ref_options(self, versions: list[str]) -> None:
        db = self.get_db()
        packs = {v: db.pack_versions(v) for v in versions} if db is not None else {}
        target_version, _ = split_reference(self.version_dd.value)
        options = reference_options(versions, packs, target_version)
        kit.set_dropdown_options(self.ref_dd, options)
        if self.ref_dd.value not in {key for key, _ in options}:
            self.ref_dd.value = LATEST_KEY

    def _build_dialog(self) -> None:
        width, height = dialog_dimensions(self.page, max_width=1080)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("版本差異審核"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "比較「內容相同（類型、模組、鍵值、原文）」條目在其他版本已審核的譯文。"
                            "套用參考譯文會把目標條目標為已審核；保留目標譯文會把目前譯文標為已審核，"
                            "之後匯入不會再被覆蓋。套用可整批復原。",
                            size=12,
                            color=C.MUTED,
                            selectable=True,
                        ),
                        ft.Row(
                            [self.version_dd, self.ref_dd, self.mod_dd],
                            spacing=12,
                            wrap=True,
                        ),
                        self.tabs,
                        self.summary,
                        self.exclusion_note,
                        self.selection_row,
                        self.status,
                        self.progress,
                        self.list_col,
                        self.pager,
                    ],
                    tight=True,
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=width,
                height=height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[self.revert_btn, self.close_btn, self.apply_selected_btn],
            on_dismiss=self._on_dismiss,
        )

    def open(self) -> None:
        try:
            isolate(self.dialog)
            present_page_dialog(self.page, self.dialog)
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟版本差異審核視窗。", C.GOLD)

    def _close(self, _e=None) -> None:
        if self._busy:
            show_snack(self.page, "資料庫處理中，請稍候。", C.GOLD)
            return
        self._on_dismiss()
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        self._closed = True
        self._generation += 1

    # ------------------------------------------------------------------ 載入
    def _scope(
        self,
    ) -> tuple[tuple[str, str] | None, tuple[str, str], list[str] | None]:
        mod = self.mod_dd.value
        target = self.version_dd.value
        return (
            split_reference(target) if target else None,
            split_reference(self.ref_dd.value),
            None if mod in (None, "", ALL_MODS) else [str(mod)],
        )

    def _on_scope(self, _e=None) -> None:
        db = self.get_db()
        if db is not None:
            self._set_ref_options(list(db.versions()))
        self.current_page = 1
        self._reload()

    def _on_tab(self, key: str) -> None:
        self.tab = key
        self.current_page = 1
        self._confirm_overwrite = False
        self._confirm_row = None
        self._render()

    def _on_page(self, page: int) -> None:
        self.current_page = page
        self._render()

    def _reload(self) -> None:
        target, (ref, ref_pack), mods = self._scope()
        db = self.get_db()
        if db is None or not target:
            self.rows = []
            self._render()
            return
        version, target_pack = target
        self._generation += 1
        generation = self._generation
        self._set_busy(True, "正在比較各版本已審核的譯文…")

        def work() -> None:
            rows = error = exclusions = None
            try:
                rows = db.list_cross_version_differences(
                    version,
                    ref_version=ref,
                    ref_pack=ref_pack,
                    target_pack=target_pack,
                    mod_ids=mods,
                    cancel=lambda: self._closed or generation != self._generation,
                )
                exclusions = db.explain_cross_version_exclusions(
                    version,
                    ref_version=ref,
                    ref_pack=ref_pack,
                    target_pack=target_pack,
                    mod_ids=mods,
                    cancel=lambda: self._closed or generation != self._generation,
                )
                all_mods = (
                    db.pack_mods(version, target_pack)
                    if target_pack
                    else list(db.mods(version))
                )
            except QueryCancelled:
                return  # 畫面已換條件或已關閉：不需要這次的結果
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                all_mods = []
                log_exception("版本差異審核載入失敗")
            self._post(self._loaded, generation, rows, all_mods, error, exclusions)

        self._launch(
            work,
            "載入版本差異",
            CommitPolicy.EPHEMERAL,
            presentation=OperationPresentation.MAINTENANCE,
        )

    def _loaded(self, generation: int, rows, all_mods, error, exclusions=None) -> None:
        if self._closed or generation != self._generation:
            return
        self._set_busy(False)
        if error is not None:
            self.status.value = f"載入失敗：{error}"
            self._safe_update()
            return
        current = self.mod_dd.value
        kit.set_dropdown_options(
            self.mod_dd, [(ALL_MODS, "全部模組")] + [(m, m) for m in all_mods]
        )
        if current != ALL_MODS and current not in all_mods:
            # 選的模組不在這個版本：改回「全部模組」並照新條件重新查詢，
            # 否則畫面顯示全部模組、結果卻仍是舊模組的篩選
            self.mod_dd.value = ALL_MODS
            self.current_page = 1
            self._reload()
            return
        self.mod_dd.value = current
        self.rows = rows
        self.exclusions = exclusions
        self.selected &= {row.entry_id for row in rows}
        self._confirm_overwrite = False
        self._confirm_row = None
        self._render()

    # ------------------------------------------------------------------ 畫面
    def _current_rows(self) -> list[CrossVersionRow]:
        return self._tab_rows(*split_rows(self.rows), source_changed_rows(self.rows))

    def _tab_rows(self, diffs, conflicts, changed) -> list[CrossVersionRow]:
        return {TAB_DIFF: diffs, TAB_CONFLICT: conflicts}.get(self.tab, changed)

    def _render(self) -> None:
        diffs, conflicts = split_rows(self.rows)
        changed = source_changed_rows(self.rows)
        shown = self._tab_rows(diffs, conflicts, changed)
        start = (self.current_page - 1) * PAGE_SIZE
        self.list_col.controls = [
            self._row_card(row) for row in shown[start : start + PAGE_SIZE]
        ] or [
            kit.hint_text(
                "沒有需要審核的差異。"
                if self.tab == TAB_DIFF
                else "沒有各版本已審核譯文不一致的條目。"
                if self.tab == TAB_CONFLICT
                else "沒有鍵值相同、原文不同的條目。"
            )
        ]
        self.pager.visible = len(shown) > PAGE_SIZE
        self.pager.set_state(len(shown), self.current_page)
        reviewed_total = sum(1 for r in diffs if r.target_reviewed)
        self.summary.value = (
            f"差異 {format_count(len(diffs))} 筆・衝突 {format_count(len(conflicts))} 筆"
            f"・原文不同 {format_count(len(changed))} 筆"
            + (
                f"・其中目標已審核 {format_count(reviewed_total)} 筆"
                "（預設不能批次套用，勾選下方「包含目標已審核」才可勾選）"
                if reviewed_total and not self.include_reviewed.value
                else ""
            )
        )
        self._render_exclusions()
        self.selection_row.visible = self.tab == TAB_DIFF and bool(diffs)
        self._render_apply_button(diffs)
        self._safe_update()

    def _render_exclusions(self) -> None:
        """說明哪些內容相同的條目沒有被列出，以及原因。"""
        ex = self.exclusions
        parts: list[str] = []
        if ex is not None and ex.locale_incompatible:
            examples = f"（例如 {'、'.join(ex.samples)}）" if ex.samples else ""
            parts.append(
                f"另有 {format_count(ex.locale_incompatible)} 筆內容相同、參考已審核，"
                f"但來源語系與目標不相容而未列出{examples}"
            )
            if ex.target_locale_unknown:
                parts.append(
                    f"其中 {format_count(ex.target_locale_unknown)} 筆目標的來源語系未記錄"
                    "（舊版匯入），重新掃描匯入目標版本即可補上"
                )
        if ex is not None and ex.ref_unreviewed:
            parts.append(
                f"{format_count(ex.ref_unreviewed)} 筆其他版本只有尚未審核的譯文，不當參考"
            )
        self.exclusion_note.value = "；".join(parts) + "。" if parts else ""
        self.exclusion_note.visible = bool(parts)

    def _render_apply_button(self, diffs: list[CrossVersionRow]) -> None:
        """按鈕顯示已勾選筆數；勾到目標已審核的條目時，第一次按下要再確認一次。"""
        include = bool(self.include_reviewed.value)
        items = selected_items(diffs, self.selected, include)
        overwrite = self._overwrite_count(diffs)
        if self._confirm_overwrite and overwrite:
            label = f"確認覆蓋 {format_count(overwrite)} 筆已審核譯文並套用 {format_count(len(items))} 筆"
        else:
            label = f"套用已勾選（{format_count(len(items))} 筆）"
        self.apply_selected_btn.content = label
        self.apply_selected_btn.disabled = self._busy or not items

    def _overwrite_count(self, diffs: list[CrossVersionRow]) -> int:
        include = bool(self.include_reviewed.value)
        return sum(
            1
            for row in diffs
            if row.target_reviewed
            and row.entry_id in self.selected
            and selectable(row, include)
        )

    def _row_card(self, row: CrossVersionRow) -> ft.Container:
        refs = (
            sorted(row.refs, key=lambda r: (r.reviewed_at, r.version), reverse=True)
            if row.conflict
            else [row.reference]
        )
        return ft.Container(
            padding=10,
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            content=ft.Column(
                [
                    self._row_header(row),
                    self._source_block(row),
                    ft.Row(
                        [
                            ft.Column(
                                [self._ref_block(row, ref) for ref in refs],
                                spacing=8,
                                expand=True,
                            ),
                            self._target_block(row, refs),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                ],
                spacing=8,
            ),
        )

    def _row_header(self, row: CrossVersionRow) -> ft.Row:
        can_select = selectable(row, bool(self.include_reviewed.value))
        checkbox = ft.Checkbox(
            value=row.entry_id in self.selected,
            disabled=not can_select,
            tooltip=(
                None
                if can_select
                else "目標已審核：勾選上方「包含目標已審核」後才能批次套用"
            ),
            on_change=lambda e, r=row: self._toggle_row(r, e),
        )
        return ft.Row(
            [
                *([] if row.conflict or row.source_changed else [checkbox]),
                ft.Text(
                    f"{row.mod_id} · {row.key}",
                    size=13,
                    weight=ft.FontWeight.W_600,
                    selectable=True,
                    expand=True,
                ),
                kit.chip(kind_label(row.kind), "neutral"),
            ]
        )

    @staticmethod
    def _source_block(row: CrossVersionRow) -> ft.Container:
        """原文：獨立一塊、字色較淡，只當參照；格式碼同樣淡化。"""
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            border=ft.Border(left=ft.BorderSide(3, C.LINE)),
            content=ft.Column(
                [
                    ft.Text("原文", size=11, color=C.DIM),
                    ft.Text(
                        spans=text_spans(row.en_us, [], C.EM_BG),
                        size=12,
                        color=C.MUTED,
                        selectable=True,
                    ),
                ],
                spacing=2,
            ),
        )

    def _ref_block(self, row: CrossVersionRow, ref: VersionRef) -> ft.Container:
        """參考譯文：綠底；與目標不同的片段加底色（差異太大時不標）。"""
        marks, _ = changed_ranges(ref.zh_tw, row.target_zh_tw)
        return ft.Container(
            padding=10,
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.10, C.EM),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            kit.chip(f"參考 {_ref_name(row.kind, ref)}", "em"),
                            ft.Text(
                                f"已審核・{_time_text(ref)}", size=11.5, color=C.MUTED
                            ),
                        ],
                        spacing=8,
                    ),
                    *(
                        # 原文不同：列出參考版本的原文，供對照後判斷譯文是否仍適用
                        [ft.Text(f"參考原文：{ref.en_us}", size=11.5, color=C.GOLD)]
                        if ref.en_us != row.en_us
                        else []
                    ),
                    *(
                        # KubeJS／FTB：參考來自不同的整合包身分，標示出來源以便辨認
                        [ft.Text(f"來自 {ref.mod_id}", size=11, color=C.DIM)]
                        if ref.mod_id and ref.mod_id != row.mod_id
                        else []
                    ),
                    ft.Text(
                        spans=text_spans(
                            ref.zh_tw, marks, ft.Colors.with_opacity(0.35, C.EM)
                        ),
                        size=13.5,
                        selectable=True,
                    ),
                    kit.button(
                        self._apply_label(row, ref),
                        "secondary",
                        size="sm",
                        on_click=lambda _e, r=row, f=ref: self._apply_one(r, f),
                    ),
                ],
                spacing=6,
            ),
        )

    def _apply_label(self, row: CrossVersionRow, ref: VersionRef) -> str:
        if self._needs_confirm(row) and self._confirm_row == (
            row.entry_id,
            ref.entry_id,
        ):
            return (
                "確認覆蓋已審核譯文並套用"
                if row.target_reviewed
                else "確認套用（原文不同）"
            )
        if row.conflict:
            return f"套用 {_ref_name(row.kind, ref)} 的譯文"
        return "套用參考譯文"

    def _target_block(
        self, row: CrossVersionRow, refs: list[VersionRef]
    ) -> ft.Container:
        """目標譯文：已審核時用金色底，提醒套用會覆蓋已審核內容。"""
        label = source_label(
            row.target_source,
            self._catalog,
            "reviewed" if row.target_reviewed else None,
        )
        tone = C.GOLD if row.target_reviewed else C.MUTED
        # 與預設參考比對；衝突時各參考不同，這裡就不標，避免誤導
        marks = (
            [] if row.conflict else changed_ranges(refs[0].zh_tw, row.target_zh_tw)[1]
        )
        return ft.Container(
            expand=True,
            padding=10,
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.10, tone),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            kit.chip(
                                "目標", "gold" if row.target_reviewed else "neutral"
                            ),
                            ft.Text(label, size=11.5, color=C.MUTED),
                        ],
                        spacing=8,
                    ),
                    ft.Text(
                        spans=text_spans(
                            row.target_zh_tw or "（沒有譯文）",
                            marks,
                            ft.Colors.with_opacity(0.35, tone),
                        ),
                        size=13.5,
                        selectable=True,
                    ),
                    kit.button(
                        "保留目標譯文",
                        "ghost",
                        size="sm",
                        on_click=lambda _e, r=row: self._keep_target(r),
                        disabled=not row.target_zh_tw or row.target_reviewed,
                        tooltip=(
                            "目標已經是已審核，不需要再標記"
                            if row.target_reviewed
                            else "把目前譯文標為已審核，之後匯入不會再被其他版本覆蓋"
                        ),
                    ),
                ],
                spacing=6,
            ),
        )

    # ------------------------------------------------------------------ 動作
    @staticmethod
    def _needs_confirm(row: CrossVersionRow) -> bool:
        """覆蓋已審核譯文，或參考譯文是針對不同原文寫的：逐筆套用也要再按一次確認。"""
        return row.target_reviewed or row.source_changed

    def _apply_one(self, row: CrossVersionRow, ref: VersionRef) -> None:
        token = (row.entry_id, ref.entry_id)
        if self._needs_confirm(row) and self._confirm_row != token:
            self._confirm_row = token
            self._render()
            return
        self._confirm_row = None
        item = ApplyItem.from_row(row, ref)
        self._apply([item], "套用參考譯文")

    def _toggle_row(self, row: CrossVersionRow, event) -> None:
        if event.control.value:
            self.selected.add(row.entry_id)
        else:
            self.selected.discard(row.entry_id)
        self._confirm_overwrite = False
        self._render_apply_button(split_rows(self.rows)[0])
        self._safe_update()

    def _select_all(self, _e=None) -> None:
        """勾選目前所有可批次套用的差異（含其他頁；是否含目標已審核看上方勾選）。"""
        include = bool(self.include_reviewed.value)
        diffs, _conflicts = split_rows(self.rows)
        self.selected |= {row.entry_id for row in diffs if selectable(row, include)}
        self._confirm_overwrite = False
        self._render()

    def _clear_selection(self, _e=None) -> None:
        self.selected.clear()
        self._confirm_overwrite = False
        self._render()

    def _on_include_reviewed(self, _e=None) -> None:
        if not self.include_reviewed.value:
            reviewed = {r.entry_id for r in self.rows if r.target_reviewed}
            self.selected -= reviewed
        self._confirm_overwrite = False
        self._render()

    def _apply_selected(self, _e=None) -> None:
        diffs, _conflicts = split_rows(self.rows)
        include = bool(self.include_reviewed.value)
        items = selected_items(diffs, self.selected, include)
        if not items:
            return
        if self._overwrite_count(diffs) and not self._confirm_overwrite:
            self._confirm_overwrite = True  # 會覆蓋已審核譯文：再按一次才執行
            self._render_apply_button(diffs)
            self._safe_update()
            return
        self._confirm_overwrite = False
        self._apply(items, "套用已勾選")

    def _apply(self, items: list[ApplyItem], name: str) -> None:
        db = self.get_db()
        if db is None or self._busy:
            return
        self._set_busy(True, f"{name}中…")

        def work() -> None:
            result = error = None
            try:
                result = db.apply_cross_version_references(items, actor=ACTOR)
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception(f"版本差異審核：{name}失敗")
            self._post(self._applied, name, result, error)

        self._launch_write(work, f"Mod 資料庫版本差異審核：{name}")

    def _applied(self, name: str, result, error) -> None:
        self._set_busy(False)
        if error is not None or result is None:
            show_snack(self.page, f"{name}失敗：{error}；詳情見後台 log", C.RED)
            return
        self.last_history_id = result.history_id
        self.revert_btn.visible = bool(result.applied)
        stale = f"、已過期略過 {result.stale} 筆" if result.stale else ""
        show_snack(self.page, f"已套用 {format_count(result.applied)} 筆{stale}", C.EM)
        self._after_write()

    def _keep_target(self, row: CrossVersionRow) -> None:
        db = self.get_db()
        if db is None or self._busy:
            return
        self._set_busy(True, "標記目標譯文為已審核…")

        def work() -> None:
            error = None
            try:
                db.review_manual(
                    row.entry_id,
                    expected_zh_tw=row.target_zh_tw,
                    expected_source=row.target_source,
                    propagate=False,
                    actor=ACTOR,
                )
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception("版本差異審核：保留目標譯文失敗")
            self._post(self._kept, error)

        self._launch_write(work, "Mod 資料庫版本差異審核：保留目標譯文")

    def _kept(self, error) -> None:
        self._set_busy(False)
        if error is not None:
            show_snack(self.page, f"保留目標譯文失敗：{error}；詳情見後台 log", C.RED)
            return
        show_snack(self.page, "已把目標譯文標為已審核", C.EM)
        self._after_write()

    def _revert(self, _e=None) -> None:
        db = self.get_db()
        if db is None or self._busy or self.last_history_id is None:
            return
        history_id = self.last_history_id
        self._set_busy(True, "復原上一次套用…")

        def work() -> None:
            count = error = None
            try:
                count = db.revert(history_id)
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception("版本差異審核：復原失敗")
            self._post(self._reverted, count, error)

        self._launch_write(work, "Mod 資料庫版本差異審核復原")

    def _reverted(self, count, error) -> None:
        self._set_busy(False)
        if error is not None:
            show_snack(self.page, f"復原失敗：{error}；詳情見後台 log", C.RED)
            return
        self.last_history_id = None
        self.revert_btn.visible = False
        show_snack(self.page, f"已復原 {format_count(count)} 筆", C.EM)
        self._after_write()

    def _after_write(self) -> None:
        self.selected.clear()
        self._confirm_overwrite = False
        self._confirm_row = None
        if self.on_complete:
            self.on_complete(None)
        self._reload()

    # ------------------------------------------------------------------ 工具
    def _launch_write(self, work, name: str) -> None:
        self._launch(
            work,
            name,
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
            shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
        )

    def _launch(
        self,
        work,
        name: str,
        commit: CommitPolicy,
        *,
        durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE,
        shutdown: ShutdownPolicy = ShutdownPolicy.ALLOW_TO_FINISH,
        presentation: OperationPresentation = OperationPresentation.USER_VISIBLE,
    ) -> None:
        try:
            launched = launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-version-diff",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                commit=commit,
                durability=durability,
                shutdown=shutdown,
                presentation=presentation,
            )
        except Exception as exc:  # noqa: BLE001 - restore the dialog if the worker cannot launch
            self._set_busy(False)
            log_exception(f"{name}無法啟動")
            show_snack(self.page, f"{name}無法啟動：{exc}", C.RED)
            return
        if not launched:
            self._set_busy(False)
            show_snack(self.page, "應用程式正在關閉，未啟動操作。", C.GOLD)

    def _post(self, callback, *args) -> None:
        post_to_ui(
            self.page,
            callback,
            *args,
            label="Mod 資料庫版本差異審核結果",
            on_failure=self._mark_post_failure,
        )

    def _mark_post_failure(self) -> None:
        self._busy = False

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.status.value = text
        self.status.visible = busy
        self.progress.visible = busy
        self.progress.value = None if busy else 0
        self.close_btn.disabled = busy
        self.revert_btn.disabled = busy
        if busy:
            self.apply_selected_btn.disabled = True
        self._safe_update()

    def _safe_update(self) -> None:
        update_regions(self.dialog)
