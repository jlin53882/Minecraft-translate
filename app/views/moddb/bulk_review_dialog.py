"""ModDB 批量審核對話框與其預覽、範圍驗證及確認流程。"""

from __future__ import annotations

import sqlite3

import flet as ft

from app.services_impl.moddb_service import BulkReviewPlan, BulkReviewResult
from app.services_impl.moddb_source_service import source_catalog_for, source_label
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    close_page_dialog,
    dialog_dimensions,
    present_page_dialog,
)
from app.ui.snack import show_snack
from app.views.moddb.formatting import format_count, shorten
from translation_tool.utils.log_unit import log_exception, log_info, log_warning

PAGE_SIZE = 50
ACTOR = "使用者"
ALL_MODS = "__all__"
ALL_SOURCES = "__all__"


class BulkReviewDialog:
    """批量審核對話框：依版本、模組與目前生效來源預覽候選，確認後才執行批次審核。"""

    def __init__(self, page: ft.Page, get_db, version: str | None, on_complete):
        """初始化批量審核對話框的篩選、預覽與執行狀態。"""
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self.plan: BulkReviewPlan | None = None
        self.excluded: set[int] = set()
        self.result: BulkReviewResult | None = None
        self.preview_page = 1
        self._busy = False
        self._generation = 0
        self._build_controls(version)
        self._build_dialog()

    # ------------------------------------------------------------------ 版面
    def _build_controls(self, version: str | None) -> None:
        """建立批量審核的版本、模組、來源篩選與操作控制項。"""
        db = self.get_db()
        versions = db.versions() if db else []
        self.version_dd = kit.dropdown(
            label="版本", dense=True, width=180, on_select=self._on_version
        )
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        self.version_dd.value = version if version in versions else None
        self.mod_dd = kit.dropdown(
            label="模組", dense=True, width=220, on_select=self._invalidate
        )
        self.source_dd = kit.dropdown(
            label="目前生效來源", dense=True, width=260, on_select=self._invalidate
        )
        self._refresh_mods()
        self._refresh_sources()
        self.quality_check = ft.Checkbox(
            label="只審核格式檢查通過的條目",
            value=True,
            on_change=self._invalidate,
        )
        self.sync_switch = ft.Switch(
            label="同步其他版本（原文相同的版本一併改為此譯文並標為已審核）",
            value=False,
            on_change=self._invalidate,
        )
        self.preview_btn = kit.button("產生預覽", "secondary", on_click=self._preview)
        self.summary = ft.Text("尚未預覽", size=14, weight=ft.FontWeight.BOLD)
        self.warning = ft.Text("", size=12, color=C.GOLD, visible=False)
        self.status = ft.Text("", size=12, color=C.MUTED, visible=False)
        self.progress = ft.ProgressBar(visible=False)
        self.rows = ft.Column(
            [kit.hint_text("選好版本、模組與來源後，按「產生預覽」。")],
            spacing=8,
        )  # 捲動交給外層對話框，避免兩層卷軸重疊
        self.pager = kit.Pager(0, page_size=PAGE_SIZE, on_change=self._on_page)
        self.pager.visible = False
        self.include_all_btn = kit.button(
            "全部納入", "ghost", size="sm", on_click=self._include_all
        )
        self.exclude_all_btn = kit.button(
            "全部排除", "ghost", size="sm", on_click=self._exclude_all
        )
        self.selection_row = ft.Row(
            [self.include_all_btn, self.exclude_all_btn], spacing=6, visible=False
        )
        self.apply_btn = kit.button(
            "確認審核",
            "primary",
            icon=ft.Icons.VERIFIED_OUTLINED,
            on_click=self._confirm,
            disabled=True,
        )
        self.revert_btn = kit.button(
            "還原本次批量審核", "secondary", on_click=self._revert
        )
        self.revert_btn.visible = False
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

    def _build_dialog(self) -> None:
        """組合批量審核對話框的說明、篩選區、預覽區與操作按鈕。"""
        width, height = dialog_dimensions(self.page)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("批量審核"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "把「所選版本、模組、目前生效來源（可選全部）」的條目一次標為人工已審核（來源不變）；"
                            "預覽不會寫入，確認後才會審核，且可整批還原。",
                            size=12,
                            color=C.MUTED,
                            selectable=True,
                        ),
                        ft.Row(
                            [self.version_dd, self.mod_dd, self.source_dd],
                            spacing=12,
                            wrap=True,
                        ),
                        self.quality_check,
                        self.sync_switch,
                        ft.Row([self.preview_btn], spacing=8),
                        self.status,
                        self.progress,
                        self.summary,
                        self.warning,
                        self.selection_row,
                        self.rows,
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
            present_page_dialog(self.page, self.dialog)
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟批量審核視窗。", C.GOLD)

    def _close(self, _e=None) -> None:
        if self._busy:
            show_snack(self.page, "資料庫處理中，請稍候。", C.GOLD)
            return
        self._generation += 1
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        self._generation += 1

    # ------------------------------------------------------------------ 選項
    def _refresh_mods(self) -> None:
        """依選定版本載入可用模組，並保持目前選擇值有效。"""
        db = self.get_db()
        version = self.version_dd.value
        mods = db.mods(version) if db and version else ()
        options = [(ALL_MODS, "全部模組"), *((mod, mod) for mod in mods)]
        kit.set_dropdown_options(self.mod_dd, options)
        keys = {key for key, _label in options}
        if self.mod_dd.value not in keys:
            self.mod_dd.value = ALL_MODS

    def _refresh_sources(self) -> None:
        """依版本與模組載入目前生效來源選項，並提供全部來源選項。"""
        db = self.get_db()
        version = self.version_dd.value
        codes = db.effective_source_codes(version) if db and version else ()
        catalog = source_catalog_for(db)
        options = [
            (ALL_SOURCES, "全部來源"),
            *((str(code), source_label(code, catalog)) for code in codes),
        ]
        selected = self.source_dd.value
        kit.set_dropdown_options(
            self.source_dd, options
        )
        keys = {key for key, _label in options}
        if selected not in keys:
            self.source_dd.value = ALL_SOURCES

    def _on_version(self, _e=None) -> None:
        """版本選擇變更時重載模組與來源，並使舊預覽失效。"""
        self._refresh_mods()
        self._refresh_sources()
        self._invalidate()

    def _invalidate(self, _e=None) -> None:
        """選項變動後舊預覽失效：清掉預覽與排除清單，要求重新預覽。"""
        self._generation += 1
        self.plan = None
        self.excluded = set()
        self.result = None
        self.summary.value = "選項已變更，請重新產生預覽"
        self.warning.visible = False
        self.rows.controls = [kit.hint_text("選好版本、模組與來源後，按「產生預覽」。")]
        self.pager.visible = False
        self.selection_row.visible = False
        self.apply_btn.disabled = True
        self.revert_btn.visible = False
        self._safe_update()

    # ------------------------------------------------------------------ 預覽
    def _preview(self, _e=None) -> None:
        """在背景執行目前範圍查詢並產生批量審核預覽。"""
        db = self.get_db()
        version = self.version_dd.value
        source_value = self.source_dd.value
        if db is None or not version or source_value in (None, ""):
            show_snack(self.page, "請先選擇版本與目前生效來源。", C.GOLD)
            return
        if self._busy:
            return
        source = None if source_value == ALL_SOURCES else int(source_value)
        source_text = "全部來源" if source is None else str(source)
        mod_id = self.mod_dd.value if self.mod_dd.value != ALL_MODS else None
        quality = bool(self.quality_check.value)
        propagate = bool(self.sync_switch.value)
        generation = self._generation
        self._set_busy(True, "查詢符合條目…")

        def work() -> None:
            """執行背景查詢或寫入工作，完成後把結果排回 UI 執行緒。"""
            plan = error = None
            try:
                plan = db.preview_bulk_review(
                    version,
                    source,
                    mod_id=mod_id,
                    quality_pass_only=quality,
                    propagate=propagate,
                )
            except (ValueError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
                error = exc
                log_exception(
                    f"Mod 資料庫批量審核預覽失敗：{version} 模組 {mod_id or '全部'} 來源 {source_text}"
                )
            self._post(self._apply_preview, generation, plan, error)

        self._launch(work, "Mod 資料庫批量審核預覽", CommitPolicy.PARTIAL_ALLOWED)

    def _apply_preview(self, generation: int, plan, error) -> None:
        self._set_busy(False)
        if generation != self._generation:
            return  # 預覽期間選項又改了
        if error is not None:
            self.summary.value = f"預覽失敗：{error}"
            show_snack(self.page, f"批量審核預覽失敗：{error}", C.RED)
            self._safe_update()
            return
        self.plan = plan
        self.excluded = set()
        self.preview_page = 1
        self._render_plan()

    def _render_plan(self) -> None:
        plan = self.plan
        if plan is None:
            return
        total = len(plan.candidates)
        included = total - len(self.excluded)
        self.selection_row.visible = total > 0
        self.pager.visible = total > PAGE_SIZE
        self.pager.set_state(total, self.preview_page)
        if total == 0:
            self.summary.value = "沒有符合條件的條目"
            self.warning.visible = False
            self.apply_btn.disabled = True
            self.rows.controls = [kit.hint_text("沒有需要審核的條目。")]
            self._safe_update()
            return
        parts = [f"符合 {format_count(total)} 筆，納入 {format_count(included)} 筆"]
        # 同步影響範圍只計算仍納入的條目，排除後會跟著減少
        sibling_total, sibling_replaced = plan.sibling_totals(self.excluded)
        if plan.propagate:
            parts.append(
                f"同步其他版本 {format_count(sibling_total)} 筆"
                f"（其中改寫不同譯文 {format_count(sibling_replaced)} 筆）"
            )
        self.summary.value = "；".join(parts)
        self.warning.visible = plan.propagate and sibling_replaced > 0
        self.warning.value = (
            f"同步會把 {format_count(sibling_replaced)} 筆其他版本目前不同的譯文"
            "改寫為所選版本的譯文；被取代的譯文會留在歷史，可整批還原。"
        )
        self.apply_btn.disabled = included == 0 or self._busy
        self.apply_btn.content = f"確認審核 {format_count(included)} 筆"
        start = (self.preview_page - 1) * PAGE_SIZE
        page_rows = plan.candidates[start : start + PAGE_SIZE]
        self.rows.controls = [self._row(candidate) for candidate in page_rows]
        self._safe_update()

    def _row(self, candidate) -> ft.Control:
        def toggle(e, entry_id=candidate.entry_id) -> None:
            if e.control.value:
                self.excluded.discard(entry_id)
            else:
                self.excluded.add(entry_id)
            self._render_plan()

        included = candidate.entry_id not in self.excluded
        return ft.Container(
            opacity=1.0 if included else 0.5,
            padding=ft.Padding.symmetric(horizontal=8, vertical=8),
            border_radius=design.RADIUS_CONTROL,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE if included else C.DIM),
            content=ft.Row(
                [
                    ft.Checkbox(value=included, on_change=toggle),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        candidate.mod_id,
                                        size=12,
                                        weight=ft.FontWeight.BOLD,
                                        color=C.TEXT,
                                    ),
                                    kit.mono_text(
                                        shorten(candidate.key, 70),
                                        size=11,
                                        color=C.DIM,
                                    ),
                                ],
                                spacing=8,
                                wrap=True,
                            ),
                            self._text_line("原文", shorten(candidate.en_us, 80)),
                            self._text_line(
                                "譯文", shorten(candidate.zh_tw, 80), main=True
                            ),
                        ],
                        spacing=4,
                        tight=True,
                        expand=True,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        )

    @staticmethod
    def _text_line(label: str, text: str, *, main: bool = False) -> ft.Control:
        """卡片內的「原文／譯文」一行：左側小標籤，右側內容（譯文用主色、較大字）。"""
        return ft.Row(
            [
                ft.Container(
                    ft.Text(label, size=10.5, color=C.DIM),
                    width=30,
                ),
                ft.Text(
                    text or "（無原文）",
                    size=13 if main else 12.5,
                    color=C.TEXT if main else C.MUTED,
                    expand=True,
                ),
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _on_page(self, page: int) -> None:
        self.preview_page = page
        self._render_plan()

    def _include_all(self, _e=None) -> None:
        self.excluded = set()
        self._render_plan()

    def _exclude_all(self, _e=None) -> None:
        if self.plan is not None:
            self.excluded = {c.entry_id for c in self.plan.candidates}
            self._render_plan()

    # ------------------------------------------------------------------ 寫入
    def _confirm(self, _e=None) -> None:
        """確認目前批量審核預覽，並在背景執行套用前的範圍檢查。"""
        db, plan = self.get_db(), self.plan
        if db is None or plan is None or self._busy:
            return
        excluded = tuple(self.excluded)
        generation = self._generation
        source_text = "全部來源" if plan.source is None else str(plan.source)
        self._set_busy(True, "審核中…")

        def work() -> None:
            """執行背景查詢或寫入工作，完成後把結果排回 UI 執行緒。"""
            result = error = None
            try:
                result = db.execute_bulk_review(
                    plan,
                    excluded_ids=excluded,
                    actor=ACTOR,
                    progress_callback=self._on_progress,
                )
            except ValueError as exc:
                error = exc
            except sqlite3.Error as exc:
                error = exc
                log_exception(
                    f"Mod 資料庫批量審核失敗：{plan.version} 模組 {plan.mod_id or '全部'} 來源 {source_text}"
                )
            except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
                error = exc
                log_exception(
                    f"Mod 資料庫批量審核發生未預期錯誤：{plan.version} 模組 {plan.mod_id or '全部'} 來源 {source_text}"
                )
            self._post(self._apply_result, generation, plan, result, error)

        self._launch(
            work,
            "Mod 資料庫批量審核",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
        )

    def _on_progress(self, text: str, fraction: float) -> None:
        """把背景批量審核的文字與比例進度排回 UI 執行緒。"""
        self._post(self._show_progress, text, fraction)

    def _show_progress(self, text: str, fraction: float) -> None:
        """在 UI 執行緒顯示批量審核的狀態文字與進度比例。"""
        self.status.value = text
        self.progress.value = fraction
        self._safe_update()

    def _apply_result(self, generation: int, plan, result, error) -> None:
        """把批量審核結果轉成 UI 摘要，並清除已失效的預覽狀態。"""
        self._set_busy(False)
        source_text = "全部來源" if plan.source is None else str(plan.source)
        if error is not None:
            log_warning(f"Mod 資料庫批量審核被拒絕或失敗：{error!r}")
            self.summary.value = f"批量審核未執行：{error}"
            show_snack(self.page, f"批量審核未執行：{error}", C.GOLD)
            if isinstance(error, ValueError):
                self.plan = None  # 範圍已過期，必須重新預覽
                self.apply_btn.disabled = True
            self._safe_update()
            return
        self.result = result
        self.plan = None
        self.excluded = set()
        message = f"已審核 {format_count(result.reviewed)} 筆"
        if result.synced:
            message += f"，並同步其他版本 {format_count(result.synced)} 筆"
        if result.stale_skipped:
            message += (
                f"（{format_count(result.stale_skipped)} 筆已被其他操作改動而略過）"
            )
        log_info(
            f"Mod 資料庫批量審核：{plan.version} 模組 {plan.mod_id or '全部'} "
            f"來源 {source_text}；{message}"
        )
        self.summary.value = message
        self.warning.visible = False
        self.rows.controls = [kit.hint_text("完成。可按「還原本次批量審核」整批復原。")]
        self.pager.visible = False
        self.selection_row.visible = False
        self.apply_btn.disabled = True
        self.revert_btn.visible = result.reviewed + result.synced > 0
        show_snack(self.page, message, C.EM, text_color=C.ON_EM)
        if self.on_complete:
            self.on_complete(result)
        self._safe_update()

    def _revert(self, _e=None) -> None:
        db, result = self.get_db(), self.result
        if db is None or result is None or self._busy:
            return
        self._set_busy(True, "還原中…")

        def work() -> None:
            count = error = None
            try:
                count = db.revert_review_batch(result.batch)
            except sqlite3.Error as exc:
                error = exc
                log_exception(f"Mod 資料庫批量審核還原失敗：batch={result.batch}")
            self._post(self._apply_revert, count, error)

        self._launch(
            work,
            "Mod 資料庫批量審核還原",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
        )

    def _apply_revert(self, count, error) -> None:
        self._set_busy(False)
        if error is not None:
            show_snack(
                self.page, f"還原失敗（資料庫錯誤：{error}）；詳情見後台 log", C.RED
            )
            self._safe_update()
            return
        self.result = None
        self.revert_btn.visible = False
        self.summary.value = f"已還原 {format_count(count)} 筆"
        show_snack(self.page, f"批量審核已還原 {format_count(count)} 筆", C.EM)
        if self.on_complete:
            self.on_complete(None)
        self._safe_update()

    # ------------------------------------------------------------------ 工具
    def _launch(
        self,
        work,
        name: str,
        commit: CommitPolicy,
        *,
        durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE,
        cancellation: CancellationPolicy = CancellationPolicy.NON_CANCELLABLE,
        shutdown: ShutdownPolicy = ShutdownPolicy.ALLOW_TO_FINISH,
    ) -> None:
        try:
            launched = launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-bulk-review",
                cancellation=cancellation,
                commit=commit,
                durability=durability,
                shutdown=shutdown,
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
        """把背景結果排回畫面執行緒（沒有 ``run_task`` 的測試頁面直接呼叫）。"""
        run_task = getattr(self.page, "run_task", None)
        try:
            if callable(run_task):

                async def runner() -> None:
                    callback(*args)

                run_task(runner)
            else:
                callback(*args)
        except Exception as exc:  # noqa: BLE001 - a disposed page may reject the callback
            log_warning(f"Mod 資料庫批量審核結果無法排回畫面：{exc!r}")

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.preview_btn.disabled = busy
        self.version_dd.disabled = busy
        self.source_dd.disabled = busy
        self.quality_check.disabled = busy
        self.sync_switch.disabled = busy
        self.close_btn.disabled = busy
        self.revert_btn.disabled = busy
        self.status.visible = busy
        self.status.value = text
        self.progress.visible = busy
        self.progress.value = None if busy else 0
        if busy:
            self.apply_btn.disabled = True
        elif self.plan is not None:
            self.apply_btn.disabled = len(self.plan.candidates) == len(self.excluded)
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.page.update()
        except RuntimeError:
            pass  # 視窗已關閉
