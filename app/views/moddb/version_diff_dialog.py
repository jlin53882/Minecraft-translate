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
    VersionRef,
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

PAGE_SIZE = 20
ACTOR = "使用者"
ALL_MODS = "__all__"
TAB_DIFF = "diff"
TAB_CONFLICT = "conflict"
LATEST_LABEL = "各條目最近審核的版本"


def split_rows(
    rows: list[CrossVersionRow],
) -> tuple[list[CrossVersionRow], list[CrossVersionRow]]:
    """把差異清單分成（可直接套用的差異, 各版本不一致的衝突）。"""
    diffs = [row for row in rows if not row.conflict]
    return diffs, [row for row in rows if row.conflict]


def applicable_items(rows: list[CrossVersionRow]) -> list[ApplyItem]:
    """批次套用的內容：只含目標尚未審核的差異（目標已審核者必須逐筆決定）。"""
    return [
        ApplyItem(
            row.entry_id,
            row.reference.version,
            row.target_zh_tw,
            row.reference.zh_tw,
        )
        for row in rows
        if not row.conflict and not row.target_reviewed
    ]


def _time_text(ref: VersionRef) -> str:
    return ref.reviewed_at[:16] if ref.reviewed_at else "審核時間不明"


class VersionDiffDialog:
    """版本差異審核：預設以各條目最近審核的版本當參考，可固定特定版本。"""

    def __init__(self, page: ft.Page, get_db, version: str | None, on_complete) -> None:
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self.rows: list[CrossVersionRow] = []
        self.tab = TAB_DIFF
        self.current_page = 1
        self.last_history_id: int | None = None
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
            label="目標版本", dense=True, width=170, on_select=self._on_scope
        )
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        self.version_dd.value = version if version in versions else None
        self.ref_dd = kit.dropdown(
            label="參考版本", dense=True, width=240, on_select=self._on_scope
        )
        self.ref_dd.value = "__latest__"
        self._set_ref_options(versions)
        self.mod_dd = kit.searchable_dropdown(
            label="模組", dense=True, width=220, on_select=self._on_scope
        )
        kit.set_dropdown_options(self.mod_dd, [(ALL_MODS, "全部模組")])
        self.mod_dd.value = ALL_MODS
        self.tabs = kit.Segmented(
            [(TAB_DIFF, "差異"), (TAB_CONFLICT, "衝突")], TAB_DIFF, self._on_tab
        )
        self.summary = ft.Text("", size=13, weight=ft.FontWeight.BOLD)
        self.status = ft.Text("", size=12, color=C.MUTED)
        self.progress = ft.ProgressBar(visible=False)
        self.list_col = ft.Column([], spacing=8)
        self.pager = kit.Pager(0, page_size=PAGE_SIZE, on_change=self._on_page)
        self.pager.visible = False
        self.apply_all_btn = kit.button(
            "套用全部差異",
            "primary",
            icon=ft.Icons.DOWNLOAD_DONE,
            on_click=self._apply_all,
            disabled=True,
        )
        self.revert_btn = kit.button(
            "復原上一次套用", "secondary", on_click=self._revert
        )
        self.revert_btn.visible = False
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

    def _set_ref_options(self, versions: list[str]) -> None:
        target = self.version_dd.value
        options = [("__latest__", LATEST_LABEL)] + [
            (v, f"固定 {v}") for v in versions if v != target
        ]
        kit.set_dropdown_options(self.ref_dd, options)
        if self.ref_dd.value not in {key for key, _ in options}:
            self.ref_dd.value = "__latest__"

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
            actions=[self.revert_btn, self.close_btn, self.apply_all_btn],
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
    def _scope(self) -> tuple[str | None, str, list[str] | None]:
        ref = self.ref_dd.value
        mod = self.mod_dd.value
        return (
            self.version_dd.value,
            REFERENCE_LATEST if ref in (None, "", "__latest__") else str(ref),
            None if mod in (None, "", ALL_MODS) else [str(mod)],
        )

    def _on_scope(self, _e=None) -> None:
        self._set_ref_options(list(self.get_db().versions()))
        self.current_page = 1
        self._reload()

    def _on_tab(self, key: str) -> None:
        self.tab = key
        self.current_page = 1
        self._render()

    def _on_page(self, page: int) -> None:
        self.current_page = page
        self._render()

    def _reload(self) -> None:
        version, ref, mods = self._scope()
        db = self.get_db()
        if db is None or not version:
            self.rows = []
            self._render()
            return
        self._generation += 1
        generation = self._generation
        self._set_busy(True, "正在比較各版本已審核的譯文…")

        def work() -> None:
            rows = error = None
            try:
                rows = db.list_cross_version_differences(
                    version, ref_version=ref, mod_ids=mods
                )
                all_mods = list(db.mods(version))
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                all_mods = []
                log_exception("版本差異審核載入失敗")
            self._post(self._loaded, generation, rows, all_mods, error)

        self._launch(
            work,
            "載入版本差異",
            CommitPolicy.EPHEMERAL,
            presentation=OperationPresentation.MAINTENANCE,
        )

    def _loaded(self, generation: int, rows, all_mods, error) -> None:
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
        self.mod_dd.value = current if current in all_mods else ALL_MODS
        self.rows = rows
        self._render()

    # ------------------------------------------------------------------ 畫面
    def _current_rows(self) -> list[CrossVersionRow]:
        diffs, conflicts = split_rows(self.rows)
        return diffs if self.tab == TAB_DIFF else conflicts

    def _render(self) -> None:
        diffs, conflicts = split_rows(self.rows)
        shown = diffs if self.tab == TAB_DIFF else conflicts
        start = (self.current_page - 1) * PAGE_SIZE
        self.list_col.controls = [
            self._row_card(row) for row in shown[start : start + PAGE_SIZE]
        ] or [
            kit.hint_text(
                "沒有需要審核的差異。"
                if self.tab == TAB_DIFF
                else "沒有各版本已審核譯文不一致的條目。"
            )
        ]
        self.pager.visible = len(shown) > PAGE_SIZE
        self.pager.set_state(len(shown), self.current_page)
        items = applicable_items(diffs)
        reviewed_skip = sum(1 for r in diffs if r.target_reviewed)
        self.summary.value = (
            f"差異 {format_count(len(diffs))} 筆・衝突 {format_count(len(conflicts))} 筆"
            + (
                f"・目標已審核 {format_count(reviewed_skip)} 筆需逐筆決定"
                if reviewed_skip
                else ""
            )
        )
        self.apply_all_btn.content = f"套用全部差異（{format_count(len(items))} 筆）"
        self.apply_all_btn.disabled = self._busy or not items
        self._safe_update()

    def _row_card(self, row: CrossVersionRow) -> ft.Container:
        target_label = source_label(
            row.target_source,
            self._catalog,
            "reviewed" if row.target_reviewed else None,
        )
        refs = (
            sorted(row.refs, key=lambda r: (r.reviewed_at, r.version), reverse=True)
            if row.conflict
            else [row.reference]
        )
        ref_cols = [self._ref_block(row, ref, row.conflict) for ref in refs]
        target = ft.Column(
            [
                ft.Text(f"目標：{target_label}", size=11.5, color=C.MUTED),
                ft.Text(row.target_zh_tw or "（沒有譯文）", size=13, selectable=True),
                kit.button(
                    "保留目標譯文",
                    "ghost",
                    size="sm",
                    on_click=lambda _e, r=row: self._keep_target(r),
                    disabled=not row.target_zh_tw or row.target_reviewed,
                    tooltip="把目前譯文標為已審核，之後匯入不會再被其他版本覆蓋",
                ),
            ],
            spacing=4,
            expand=True,
        )
        return ft.Container(
            padding=10,
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                f"{row.mod_id} · {row.key}",
                                size=13,
                                weight=ft.FontWeight.W_600,
                                selectable=True,
                                expand=True,
                            ),
                            kit.chip(kind_label(row.kind), "neutral"),
                        ]
                    ),
                    ft.Text(shorten(row.en_us, 160), size=12, color=C.MUTED),
                    ft.Row(
                        [ft.Column(ref_cols, spacing=8, expand=True), target],
                        spacing=16,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                ],
                spacing=6,
            ),
        )

    def _ref_block(
        self, row: CrossVersionRow, ref: VersionRef, conflict: bool
    ) -> ft.Column:
        label = f"{ref.version} 已審核・{_time_text(ref)}"
        return ft.Column(
            [
                ft.Text(label, size=11.5, color=C.MUTED),
                ft.Text(ref.zh_tw, size=13, selectable=True),
                kit.button(
                    f"套用 {ref.version} 的譯文" if conflict else "套用參考譯文",
                    "secondary",
                    size="sm",
                    on_click=lambda _e, r=row, f=ref: self._apply_one(r, f),
                ),
            ],
            spacing=4,
        )

    # ------------------------------------------------------------------ 動作
    def _apply_one(self, row: CrossVersionRow, ref: VersionRef) -> None:
        item = ApplyItem(row.entry_id, ref.version, row.target_zh_tw, ref.zh_tw)
        self._apply([item], "套用參考譯文")

    def _apply_all(self, _e=None) -> None:
        diffs, _conflicts = split_rows(self.rows)
        items = applicable_items(diffs)
        if items:
            self._apply(items, "套用全部差異")

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
            self.apply_all_btn.disabled = True
        self._safe_update()

    def _safe_update(self) -> None:
        update_regions(self.dialog)
