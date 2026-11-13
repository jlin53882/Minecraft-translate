"""ModDB「同文一致性審核」對話框：原文相同、譯文卻不一致的條目，統一成已審核的譯文。

範圍是單一遊戲版本（可再選模組、模組包版本與原文）；類型以內容身分的同文群組分組
（模組 lang 與整合包 KubeJS lang 同組，其他類型只和自己比），不排除任何類型，
掃描完成後可用類型篩選縮小清單。每一組只能從已審核的譯文中選統一譯文（預設建議是
成員最多的），套用到勾選的成員；目標已審核的成員需先勾「包含已審核」才能勾選。
套用可整批復原；「保留不動」就是不勾選，不留任何記錄。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import (
    CATEGORY_FILL,
    CATEGORY_INCONSISTENT,
    MAX_SOURCE_CHARS,
    QueryCancelled,
    SameTextApplyItem,
    SameTextGroup,
    SameTextMember,
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
from app.views.moddb.formatting import format_count, kind_label, shorten
from translation_tool.utils.log_unit import log_exception

PAGE_SIZE = 10  # 每頁顯示的群組數
MEMBER_LIMIT = 30  # 每組最多逐筆列出的成員數（勾選與套用仍涵蓋整組）
ACTOR = "使用者"
ALL_MODS = "__all__"
ALL_KINDS = "__all_kinds__"
VIEW_ALL = "all"
VIEW_APPLICABLE = "applicable"
VIEW_BLOCKED = "blocked"


def selectable(
    member: SameTextMember, canonical_text: str | None, include_reviewed: bool
) -> bool:
    """可勾選套用：有統一譯文、目前譯文與它不同，且（已審核者需先納入）。"""
    return (
        canonical_text is not None
        and member.zh_tw != canonical_text
        and (include_reviewed or not member.reviewed)
    )


def chosen_text(group: SameTextGroup, choice: dict[str, str]) -> str | None:
    """這一組目前選定的統一譯文：使用者選的候選，沒選就是預設建議；沒有已審核候選為 None。"""
    candidate = group.candidate(choice.get(group.group_id))
    return None if candidate is None else candidate.zh_tw


def selectable_ids(
    group: SameTextGroup, choice: dict[str, str], include_reviewed: bool
) -> set[int]:
    text = chosen_text(group, choice)
    return {m.entry_id for m in group.members if selectable(m, text, include_reviewed)}


def selected_items(
    groups: list[SameTextGroup],
    selected: set[int],
    choice: dict[str, str],
    include_reviewed: bool,
) -> list[SameTextApplyItem]:
    """批次套用的內容：已勾選且目前仍可勾選的成員，統一譯文取各組選定的候選。"""
    items: list[SameTextApplyItem] = []
    for group in groups:
        candidate = group.candidate(choice.get(group.group_id))
        if candidate is None:
            continue
        items += [
            SameTextApplyItem.from_member(
                m, candidate, allow_overwrite_reviewed=include_reviewed
            )
            for m in group.members
            if m.entry_id in selected
            and selectable(m, candidate.zh_tw, include_reviewed)
        ]
    return items


def overwrite_count(items: list[SameTextApplyItem]) -> int:
    """這批會覆蓋已審核譯文的筆數。"""
    return sum(1 for item in items if item.expected_target_reviewed)


def kind_options(groups: list[SameTextGroup]) -> list[tuple[str, str]]:
    """類型篩選選項：全部，以及掃描結果中實際出現的類型群組（例如「模組語系＋KubeJS 語系」）。"""
    seen: dict[str, str] = {}
    for group in groups:
        seen.setdefault(
            "|".join(group.kinds), "＋".join(kind_label(k) for k in group.kinds)
        )
    return [(ALL_KINDS, "全部類型"), *sorted(seen.items(), key=lambda kv: kv[1])]


CATEGORY_LABELS = {CATEGORY_INCONSISTENT: "譯文不一致", CATEGORY_FILL: "補譯"}


def filter_groups(
    groups: list[SameTextGroup],
    kind_key: str,
    view: str,
    category: str | None = None,
) -> list[SameTextGroup]:
    """依分類（譯文不一致／補譯）、類型群組與「可統一／無已審核譯文」篩選。"""
    out = groups
    if category is not None:
        out = [g for g in out if g.category == category]
    if kind_key != ALL_KINDS:
        out = [g for g in out if "|".join(g.kinds) == kind_key]
    if view == VIEW_APPLICABLE:
        out = [g for g in out if g.applicable]
    elif view == VIEW_BLOCKED:
        out = [g for g in out if not g.applicable]
    return out


class SameTextReviewDialog:
    """同文一致性審核：列出原文相同、譯文不一致的群組，統一成已審核的譯文。"""

    def __init__(
        self,
        page: ft.Page,
        get_db,
        version: str | None,
        on_complete,
        *,
        pack_scope: str | None = None,
        en_us: str | None = None,
    ) -> None:
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self.pack_scope = pack_scope or ""
        self.en_us = en_us
        self.groups: list[SameTextGroup] = []
        self.view = VIEW_ALL
        self.category = CATEGORY_INCONSISTENT
        self.current_page = 1
        self.selected: set[int] = set()
        self.choice: dict[str, str] = {}  # 群組 → 使用者選的統一譯文
        self.last_history_id: int | None = None
        self._confirm_overwrite = False
        self._busy = False
        self._closed = False
        self._generation = 0
        self._last_progress = 0.0
        self._catalog = source_catalog_for(self.get_db())
        self._build_controls(version)
        self._build_dialog()
        self._reload()

    # ------------------------------------------------------------------ 版面
    def _build_controls(self, version: str | None) -> None:
        db = self.get_db()
        versions = list(db.versions()) if db is not None else []
        self.version_dd = kit.dropdown(
            label="遊戲版本", dense=True, width=220, on_select=self._on_version
        )
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        self.version_dd.value = version if version in versions else None
        self.mod_dd = kit.searchable_dropdown(
            label="模組", dense=True, width=220, on_select=self._on_scope
        )
        kit.set_dropdown_options(self.mod_dd, [(ALL_MODS, "全部模組")])
        self.mod_dd.value = ALL_MODS
        self.kind_dd = kit.dropdown(
            label="類型", dense=True, width=220, on_select=self._on_kind
        )
        kit.set_dropdown_options(self.kind_dd, [(ALL_KINDS, "全部類型")])
        self.kind_dd.value = ALL_KINDS
        self.category_seg = kit.Segmented(
            [(key, label) for key, label in CATEGORY_LABELS.items()],
            CATEGORY_INCONSISTENT,
            self._on_category,
        )
        self.view_seg = kit.Segmented(
            [
                (VIEW_ALL, "全部"),
                (VIEW_APPLICABLE, "可統一"),
                (VIEW_BLOCKED, "無已審核譯文"),
            ],
            VIEW_ALL,
            self._on_view,
        )
        self.scope_note = ft.Text("", size=12, color=C.GOLD, selectable=True)
        self.clear_source_btn = kit.button(
            "清除原文條件", "ghost", size="sm", on_click=self._clear_source
        )
        self.summary = ft.Text("", size=13, weight=ft.FontWeight.BOLD)
        self.status = ft.Text("", size=12, color=C.MUTED)
        self.progress = ft.ProgressBar(visible=False)
        self.list_col = ft.Column([], spacing=8)
        self.pager = kit.Pager(
            0, page_size=PAGE_SIZE, on_change=self._on_page, unit="組"
        )
        self.pager.visible = False
        self.include_reviewed = ft.Checkbox(
            label="包含已審核的條目（會覆蓋已審核譯文）",
            value=False,
            on_change=self._on_include_reviewed,
        )
        self.select_all_btn = kit.button(
            "全選目前分類可統一的條目", "ghost", size="sm", on_click=self._select_all
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
        self.apply_btn = kit.button(
            "統一已勾選",
            "primary",
            icon=ft.Icons.DOWNLOAD_DONE,
            on_click=self._apply_selected,
            disabled=True,
        )
        self.revert_btn = kit.button(
            "復原上一次統一", "secondary", on_click=self._revert
        )
        self.revert_btn.visible = False
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

    def _build_dialog(self) -> None:
        width, height = dialog_dimensions(self.page, max_width=1080)
        intro = (
            "列出同一個遊戲版本內，原文相同（不分大小寫與空白）但譯文不一致的條目。"
            f"只比對原文 {MAX_SOURCE_CHARS} 字以內的條目；統一譯文只能選已審核的譯文，"
            "套用後目標條目標為已審核。不想動的條目不要勾選即可。套用可整批復原。"
        )
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("同文一致性審核"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(intro, size=12, color=C.MUTED, selectable=True),
                        ft.Row(
                            [self.version_dd, self.mod_dd, self.kind_dd],
                            spacing=12,
                            wrap=True,
                        ),
                        ft.Row(
                            [self.scope_note, self.clear_source_btn],
                            spacing=8,
                            wrap=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self.category_seg,
                        self.view_seg,
                        self.summary,
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
            actions=[self.revert_btn, self.close_btn, self.apply_btn],
            on_dismiss=self._on_dismiss,
        )

    def open(self) -> None:
        try:
            isolate(self.dialog)
            present_page_dialog(self.page, self.dialog)
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟同文一致性審核視窗。", C.GOLD)

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
    def _mods(self) -> list[str] | None:
        mod = self.mod_dd.value
        return None if mod in (None, "", ALL_MODS) else [str(mod)]

    def _on_version(self, _e=None) -> None:
        self.pack_scope = ""  # 模組包版本屬於特定遊戲版本
        self.mod_dd.value = ALL_MODS
        self._on_scope()

    def _on_scope(self, _e=None) -> None:
        self.current_page = 1
        self._reload()

    def _clear_source(self, _e=None) -> None:
        self.en_us = None
        self._on_scope()

    def _reload(self) -> None:
        db = self.get_db()
        version = self.version_dd.value
        if db is None or not version:
            self.groups = []
            self._render()
            return
        self._generation += 1
        generation = self._generation
        self._last_progress = 0.0
        self._set_busy(True, "正在掃描原文相同的條目…", determinate=True)
        cancel = lambda: self._closed or generation != self._generation

        def on_progress(fraction: float) -> None:
            if fraction - self._last_progress >= 0.01 or fraction >= 1.0:
                self._last_progress = fraction
                self._post(self._on_progress, generation, fraction)

        def work() -> None:
            groups = error = None
            all_mods: list[str] = []
            try:
                groups = db.list_same_text_groups(
                    str(version),
                    mod_ids=self._mods(),
                    pack_scope=self.pack_scope,
                    en_us=self.en_us,
                    cancel=cancel,
                    progress=on_progress,
                )
                all_mods = list(db.mods(str(version)))
            except QueryCancelled:
                return  # 畫面已換條件或已關閉：不需要這次的結果
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception("同文一致性審核載入失敗")
            self._post(self._loaded, generation, groups, all_mods, error)

        self._launch(
            work,
            "載入同文一致性",
            CommitPolicy.EPHEMERAL,
            presentation=OperationPresentation.MAINTENANCE,
        )

    def _on_progress(self, generation: int, fraction: float) -> None:
        if self._closed or generation != self._generation or not self._busy:
            return
        self.progress.value = fraction
        self.status.value = f"正在掃描原文相同的條目…{int(fraction * 100)}%"
        self._safe_update()

    def _loaded(self, generation: int, groups, all_mods, error) -> None:
        if self._closed or generation != self._generation:
            return
        self._set_busy(False)
        if error is not None:
            self.status.value = f"載入失敗：{error}"
            self.status.visible = True
            self._safe_update()
            return
        current = self.mod_dd.value
        kit.set_dropdown_options(
            self.mod_dd, [(ALL_MODS, "全部模組")] + [(m, m) for m in all_mods]
        )
        self.mod_dd.value = current if current in all_mods else ALL_MODS
        self.groups = groups
        options = kind_options(groups)
        kit.set_dropdown_options(self.kind_dd, options)
        if self.kind_dd.value not in {key for key, _ in options}:
            self.kind_dd.value = ALL_KINDS
        members = {m.entry_id for g in groups for m in g.members}
        self.selected &= members
        self.choice = {k: v for k, v in self.choice.items() if k in self._group_ids()}
        self._confirm_overwrite = False
        self._render()

    def _group_ids(self) -> set[str]:
        return {g.group_id for g in self.groups}

    # ------------------------------------------------------------------ 畫面
    def _shown(self) -> list[SameTextGroup]:
        return filter_groups(
            self.groups, self.kind_dd.value or ALL_KINDS, self.view, self.category
        )

    def _include(self) -> bool:
        return bool(self.include_reviewed.value)

    def _on_kind(self, _e=None) -> None:
        self.current_page = 1
        self._render()

    def _on_category(self, key: str) -> None:
        """切換分類：勾選只對目前分類有效，不帶到另一個分類。"""
        self.category = key
        self.current_page = 1
        self.selected.clear()
        self._confirm_overwrite = False
        self._render()

    def _on_view(self, key: str) -> None:
        self.view = key
        self.current_page = 1
        self._render()

    def _on_page(self, page: int) -> None:
        self.current_page = page
        self._render()

    def _render(self) -> None:
        shown = self._shown()
        start = (self.current_page - 1) * PAGE_SIZE
        self.list_col.controls = [
            self._group_card(g) for g in shown[start : start + PAGE_SIZE]
        ] or [
            kit.hint_text(
                "沒有原文相同但譯文不一致的條目。"
                if self.category == CATEGORY_INCONSISTENT
                else "沒有只缺譯文的組別（其餘成員已有同一種譯文）。"
            )
        ]
        self.pager.visible = len(shown) > PAGE_SIZE
        self.pager.set_state(len(shown), self.current_page)
        inconsistent = sum(
            1 for g in self.groups if g.category == CATEGORY_INCONSISTENT
        )
        blocked = sum(1 for g in self.groups if not g.applicable)
        self.summary.value = (
            f"譯文不一致 {format_count(inconsistent)} 組"
            f"・補譯 {format_count(len(self.groups) - inconsistent)} 組"
            f"・無已審核譯文 {format_count(blocked)} 組"
        )
        self.scope_note.value = self._scope_text()
        self.scope_note.visible = bool(self.scope_note.value)
        self.clear_source_btn.visible = self.en_us is not None
        self.selection_row.visible = bool(self.groups)
        self._render_apply_button()
        self._safe_update()

    def _scope_text(self) -> str:
        parts = []
        if self.en_us is not None:
            parts.append(f"只看原文：{shorten(self.en_us, 40)}")
        if self.pack_scope:
            parts.append(f"模組包版本：{self.pack_scope.rpartition('@')[0]}")
        return "・".join(parts)

    def _render_apply_button(self) -> None:
        items = selected_items(self.groups, self.selected, self.choice, self._include())
        risky = overwrite_count(items)
        if self._confirm_overwrite and risky:
            label = f"確認覆蓋 {format_count(risky)} 筆已審核譯文並統一 {format_count(len(items))} 筆"
        else:
            label = f"統一已勾選（{format_count(len(items))} 筆）"
        self.apply_btn.content = label
        self.apply_btn.disabled = self._busy or not items

    def _group_card(self, group: SameTextGroup) -> ft.Container:
        text = chosen_text(group, self.choice)
        return ft.Container(
            padding=10,
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            content=ft.Column(
                [
                    self._group_header(group),
                    *self._candidate_section(group, text),
                    *self._member_section(group, text),
                ],
                spacing=8,
            ),
        )

    def _group_header(self, group: SameTextGroup) -> ft.Control:
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            group.en_us,
                            size=13.5,
                            weight=ft.FontWeight.W_600,
                            selectable=True,
                            expand=True,
                        ),
                        *[kit.chip(kind_label(k), "neutral") for k in group.kinds],
                    ]
                ),
                ft.Text(
                    f"{format_count(len(group.members))} 筆・"
                    f"{group.distinct_translations} 種譯法"
                    + (
                        f"・{format_count(group.untranslated)} 筆未翻譯"
                        if group.untranslated
                        else ""
                    ),
                    size=11.5,
                    color=C.MUTED,
                ),
            ],
            spacing=2,
        )

    def _candidate_section(
        self, group: SameTextGroup, text: str | None
    ) -> list[ft.Control]:
        if not group.applicable:
            return [
                ft.Text(
                    "這一組沒有已審核的譯文可當統一譯文：請先在條目校對審核其中一種譯法，再回來統一。",
                    size=12,
                    color=C.GOLD,
                )
            ]
        return [
            ft.Text("統一譯文（只能選已審核的譯文）", size=11, color=C.DIM),
            ft.Row(
                [
                    self._candidate_chip(group, c, c.zh_tw == text)
                    for c in group.candidates
                ],
                spacing=8,
                wrap=True,
            ),
        ]

    def _candidate_chip(self, group: SameTextGroup, candidate, active: bool):
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            border_radius=8,
            border=ft.Border.all(2 if active else 1, C.EM if active else C.LINE),
            bgcolor=ft.Colors.with_opacity(0.10, C.EM) if active else None,
            ink=True,
            on_click=lambda _e, g=group, c=candidate: self._choose(g, c.zh_tw),
            content=ft.Column(
                [
                    ft.Text(candidate.zh_tw, size=13.5),
                    ft.Row(
                        [
                            kit.chip(
                                source_label(
                                    candidate.source, self._catalog, "reviewed"
                                ),
                                "em",
                            ),
                            ft.Text(
                                f"{format_count(candidate.member_count)} 筆",
                                size=11,
                                color=C.MUTED,
                            ),
                        ],
                        spacing=6,
                    ),
                ],
                spacing=4,
                tight=True,
            ),
        )

    def _member_section(
        self, group: SameTextGroup, text: str | None
    ) -> list[ft.Control]:
        targets = [m for m in group.members if m.zh_tw != text]
        if text is None:
            targets = list(group.members)
        include = self._include()
        ids = selectable_ids(group, self.choice, include)
        rows = [self._member_row(m, text, include) for m in targets[:MEMBER_LIMIT]]
        more = len(targets) - MEMBER_LIMIT
        if more > 0:
            rows.append(
                ft.Text(
                    f"…另 {format_count(more)} 筆（勾選與套用涵蓋整組）",
                    size=11.5,
                    color=C.DIM,
                )
            )
        actions = ft.Row(
            [
                kit.button(
                    f"勾選此組可統一的 {format_count(len(ids))} 筆",
                    "ghost",
                    size="sm",
                    on_click=lambda _e, g=group: self._select_group(g),
                    disabled=not ids,
                ),
                kit.button(
                    "取消此組",
                    "ghost",
                    size="sm",
                    on_click=lambda _e, g=group: self._clear_group(g),
                ),
            ],
            spacing=6,
        )
        return [actions, *rows]

    def _member_row(
        self, member: SameTextMember, text: str | None, include: bool
    ) -> ft.Row:
        can = selectable(member, text, include)
        label = source_label(
            member.source,
            self._catalog,
            "reviewed" if member.reviewed else None,
        )
        return ft.Row(
            [
                ft.Checkbox(
                    value=member.entry_id in self.selected,
                    disabled=not can,
                    tooltip=(
                        None
                        if can or text is None
                        else "已審核：勾選上方「包含已審核」後才能統一"
                    ),
                    on_change=lambda e, m=member: self._toggle(m, e),
                ),
                ft.Column(
                    [
                        ft.Text(
                            f"{member.mod_id} · {member.key}",
                            size=12,
                            color=C.MUTED,
                            selectable=True,
                        ),
                        ft.Text(
                            member.zh_tw or "（沒有譯文）",
                            size=13,
                            color=C.TEXT if member.zh_tw else C.DIM,
                            selectable=True,
                        ),
                    ],
                    spacing=1,
                    expand=True,
                ),
                kit.chip(
                    label if member.zh_tw else "未翻譯",
                    "gold" if member.reviewed else "neutral",
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    # ------------------------------------------------------------------ 動作
    def _choose(self, group: SameTextGroup, text: str) -> None:
        self.choice[group.group_id] = text
        # 已等於新統一譯文的成員不需要（也不能）再統一
        self.selected -= {m.entry_id for m in group.members if m.zh_tw == text}
        self._confirm_overwrite = False
        self._render()

    def _toggle(self, member: SameTextMember, event) -> None:
        if event.control.value:
            self.selected.add(member.entry_id)
        else:
            self.selected.discard(member.entry_id)
        self._confirm_overwrite = False
        self._render_apply_button()
        self._safe_update()

    def _select_group(self, group: SameTextGroup) -> None:
        self.selected |= selectable_ids(group, self.choice, self._include())
        self._confirm_overwrite = False
        self._render()

    def _clear_group(self, group: SameTextGroup) -> None:
        self.selected -= {m.entry_id for m in group.members}
        self._confirm_overwrite = False
        self._render()

    def _select_all(self, _e=None) -> None:
        """勾選目前篩選結果（含其他頁）所有可統一的條目。"""
        for group in self._shown():
            self.selected |= selectable_ids(group, self.choice, self._include())
        self._confirm_overwrite = False
        self._render()

    def _clear_selection(self, _e=None) -> None:
        self.selected.clear()
        self._confirm_overwrite = False
        self._render()

    def _on_include_reviewed(self, _e=None) -> None:
        if not self._include():
            reviewed = {
                m.entry_id for g in self.groups for m in g.members if m.reviewed
            }
            self.selected -= reviewed
        self._confirm_overwrite = False
        self._render()

    def _apply_selected(self, _e=None) -> None:
        items = selected_items(self.groups, self.selected, self.choice, self._include())
        if not items:
            return
        if overwrite_count(items) and not self._confirm_overwrite:
            # 會覆蓋已審核譯文：再按一次才執行
            self._confirm_overwrite = True
            self._render_apply_button()
            self._safe_update()
            return
        self._confirm_overwrite = False
        self._apply(items)

    def _apply(self, items: list[SameTextApplyItem]) -> None:
        db = self.get_db()
        if db is None or self._busy:
            return
        self._set_busy(True, "統一譯文中…")

        def work() -> None:
            result = error = None
            try:
                result = db.apply_same_text_canonical(items, actor=ACTOR)
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception("同文一致性審核：統一失敗")
            self._post(self._applied, result, error)

        self._launch_write(work, "Mod 資料庫同文一致性審核：統一譯文")

    def _applied(self, result, error) -> None:
        self._set_busy(False)
        if error is not None or result is None:
            show_snack(self.page, f"統一失敗：{error}；詳情見後台 log", C.RED)
            return
        if result.applied:
            self.last_history_id = result.history_id
        self.revert_btn.visible = self.last_history_id is not None
        notes = [
            f"、已過期略過 {result.stale} 筆" if result.stale else "",
            f"、已審核未覆蓋 {result.skipped_reviewed} 筆"
            if result.skipped_reviewed
            else "",
        ]
        show_snack(
            self.page, f"已統一 {format_count(result.applied)} 筆{''.join(notes)}", C.EM
        )
        self._after_write()

    def _revert(self, _e=None) -> None:
        db = self.get_db()
        if db is None or self._busy or self.last_history_id is None:
            return
        history_id = self.last_history_id
        self._set_busy(True, "復原上一次統一…")

        def work() -> None:
            count = error = None
            try:
                count = db.revert(history_id)
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception("同文一致性審核：復原失敗")
            self._post(self._reverted, count, error)

        self._launch_write(work, "Mod 資料庫同文一致性審核復原")

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
                owner="moddb-same-text-review",
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
            label="Mod 資料庫同文一致性審核結果",
            on_failure=self._mark_post_failure,
        )

    def _mark_post_failure(self) -> None:
        self._busy = False

    def _set_busy(
        self, busy: bool, text: str = "", *, determinate: bool = False
    ) -> None:
        self._busy = busy
        self.status.value = text
        self.status.visible = busy
        self.progress.visible = busy
        self.progress.value = 0 if determinate or not busy else None
        self.close_btn.disabled = busy
        self.revert_btn.disabled = busy
        if busy:
            self.apply_btn.disabled = True
        self._safe_update()

    def _safe_update(self) -> None:
        update_regions(self.dialog)
