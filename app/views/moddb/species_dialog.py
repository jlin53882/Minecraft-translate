"""Mod 資料庫的學名候選掃描與查詢視窗。"""

from __future__ import annotations

import time
from dataclasses import dataclass

import flet as ft

from app.services_impl.moddb_service import (
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_RATE_LIMITED,
    SpeciesCandidate,
    SpeciesLookup,
    SpeciesScanResult,
    cached_species_lookup,
    scan_species_candidates,
    species_scope_options,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    OperationPresentation,
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
from app.ui.page_post import post_to_ui
from app.ui.snack import show_snack
from app.views.moddb import species_bulk_lookup as bulk
from app.views.moddb.formatting import kind_label
from translation_tool.utils.log_unit import log_exception

# 候選列表每頁顯示幾筆（候選數量不設上限，用頁碼切換）。
PAGE_SIZE = 50
ALL_MODS = "__all__"
ALL_SOURCES = "__all__"
# 批次查詢時畫面最多每隔這麼久才重繪一次。
UI_FLUSH_INTERVAL_SECONDS = 0.25


@dataclass
class _LookupState:
    lookup: SpeciesLookup | None = None
    running: bool = False

    @property
    def done(self) -> bool:
        """已有確定結果，且上一層物種的參考也查完；暫時性失敗或上一層待查都可以再查。"""
        return self.lookup is not None and self.lookup.complete


def _grey_when_disabled(button: ft.Button) -> None:
    """停用時改成灰底灰字；主要按鈕的實心底色在停用時看起來仍像可以按。"""
    style = button.style
    style.bgcolor = {
        ft.ControlState.DISABLED: C.HOVER,
        ft.ControlState.DEFAULT: style.bgcolor,
    }
    style.color = {
        ft.ControlState.DISABLED: C.DIM,
        ft.ControlState.DEFAULT: style.color,
    }


class SpeciesCandidateDialog:
    """在目前條目篩選範圍內找出並查詢可能的學名。

    「查詢全部候選」交給頁面共用的 :class:`SpeciesBulkLookup` 在背景執行；
    視窗只是它的檢視，關閉視窗不會停止查詢，重新開啟會接回進行中的查詢。
    """

    def __init__(
        self,
        page: ft.Page,
        get_db,
        version: str | None,
        mod_id: str | None,
        kind: str | None,
        on_open_entry=None,
        *,
        effective_source: int | None = None,
    ):
        self.page = page
        self.get_db = get_db
        self.version = version
        self.mod_id = mod_id
        self.kind = kind
        self.effective_source = effective_source
        self.on_open_entry = on_open_entry
        self.result: SpeciesScanResult | None = None
        self._states: dict[int, _LookupState] = {}
        self._row_index: dict[int, int] = {}
        self._page_candidates: list[SpeciesCandidate] = []
        self._busy = False
        self._last_flush = 0.0
        self._generation = 0
        self._scope_seq = 0
        self._scope_loading = False
        self._closed = False
        self._opened = False
        self._scope_modules: tuple[str, ...] = ()
        self._scope_sources_by_mod: dict[str | None, tuple[int, ...]] = {}
        self._source_catalog = getattr(self.get_db(), "source_catalog", None)
        self.job = bulk.bulk_lookup_for(page)
        self._build_controls()
        self._build_dialog()
        self._start_scope_options_load()

    def _scope_text(self) -> str:
        version = self.version or "未選擇版本"
        mod = self.mod_id or "全部模組"
        kind = kind_label(self.kind) if self.kind else "全部類型"
        source = (
            self.effective_source if self.effective_source is not None else "全部來源"
        )
        return f"目前範圍：{version}／{mod}／{kind}／來源 {source}"

    def _build_controls(self) -> None:
        self.scope = ft.Text(self._scope_text(), size=13, color=C.TEXT)
        self.hint = ft.Text(
            "掃描會找出 key 帶有 latin／scientific 等提示、且格式像學名的條目；"
            "按「查詢全部」會逐筆查詢；查詢中按「背景執行」可收起視窗繼續查詢，"
            "進度顯示在頂列任務，按「關閉」則會停止查詢。",
            size=12,
            color=C.MUTED,
            selectable=True,
        )
        self.mod_dd = kit.dropdown(
            label="模組",
            dense=True,
            width=220,
            on_select=self._on_mod,
        )
        self.source_dd = kit.dropdown(
            label="目前生效來源",
            dense=True,
            width=260,
            on_select=self._on_source,
        )
        self.loose_check = ft.Checkbox(
            label="包含沒有學名 key 的候選（雜訊較多）",
            value=False,
            on_change=self._on_loose_change,
        )
        # 先放入呼叫端帶來的預填值；完整模組／來源選項由對話框建立後背景載入，
        # 避免開啟掃描視窗時同步掃描大型資料庫。
        initial_mod = self.mod_id or ALL_MODS
        if initial_mod != ALL_MODS:
            self._scope_modules = (initial_mod,)
        initial_source = (
            str(self.effective_source)
            if self.effective_source is not None
            else ALL_SOURCES
        )
        self._scope_sources_by_mod = {
            None: ((int(initial_source),) if initial_source != ALL_SOURCES else ()),
        }
        if initial_mod != ALL_MODS:
            self._scope_sources_by_mod[initial_mod] = self._scope_sources_by_mod[None]
        kit.set_dropdown_options(
            self.mod_dd,
            [(ALL_MODS, "全部模組")]
            + ([(initial_mod, initial_mod)] if initial_mod != ALL_MODS else []),
        )
        self.mod_dd.value = initial_mod
        catalog = self._source_catalog
        kit.set_dropdown_options(
            self.source_dd,
            [(ALL_SOURCES, "全部來源")]
            + (
                [
                    (
                        initial_source,
                        catalog.label(int(initial_source))
                        if catalog is not None
                        else initial_source,
                    )
                ]
                if initial_source != ALL_SOURCES
                else []
            ),
        )
        self.source_dd.value = initial_source
        self.scan_btn = kit.button(
            "掃描候選",
            "secondary",
            icon=ft.Icons.SCIENCE_OUTLINED,
            on_click=self._scan,
        )
        self.query_all_btn = kit.button(
            "查詢全部候選",
            "primary",
            icon=ft.Icons.SEARCH,
            on_click=self._query_all,
            disabled=True,
        )
        _grey_when_disabled(self.query_all_btn)
        self.stop_btn = kit.button(
            "停止查詢",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            on_click=self._stop,
        )
        self.stop_btn.visible = False
        self.status = ft.Text("尚未掃描", size=12, color=C.MUTED)
        self.progress = ft.ProgressBar(visible=False)
        self.pager = kit.Pager(
            0, page_size=PAGE_SIZE, on_change=self._on_page, unit="筆"
        )
        self.pager.visible = False
        self.rows = ft.Column(
            spacing=8,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[kit.hint_text("按「掃描候選」開始檢查目前範圍。")],
        )
        self.background_btn = kit.button(
            "背景執行",
            "secondary",
            icon=ft.Icons.OPEN_IN_BROWSER,
            on_click=self._run_in_background,
            tooltip="收起視窗，查詢在背景繼續；再按「掃描學名」可回到這裡",
        )
        self.background_btn.visible = False
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

    def _scope_query(self, require_key_hint: bool):
        """在背景工作中讀取學名候選的模組與來源對照。"""
        return species_scope_options(
            self.get_db(),
            self.version,
            kind=self.kind,
            require_key_hint=require_key_hint,
        )

    def _start_scope_options_load(self) -> None:
        """背景載入學名篩選選項，避免開啟或切換條件時阻塞 UI。"""
        self._scope_seq += 1
        seq = self._scope_seq
        require_key_hint = not bool(self.loose_check.value)
        self._scope_loading = True
        self.status.value = "正在更新學名候選的模組與來源選單…"
        if self._opened:
            self._flush(force=True)

        def work():
            """執行範圍唯讀查詢，只回傳下拉選單需要的資料。"""
            result = error = None
            try:
                result = self._scope_query(require_key_hint)
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception("Mod 資料庫學名掃描範圍選單載入失敗")
            self._post(done, result, error, on_failure=self._abandon_scope_load)

        def done(result, error: str | None) -> None:
            """只套用仍屬於最新條件的背景查詢結果。"""
            if self._closed or seq != self._scope_seq:
                return
            self._scope_loading = False
            if error:
                self.status.value = f"範圍選單載入失敗：{error}"
                if self._opened:
                    self._flush(force=True)
                return
            modules, sources_by_mod = result
            self._scope_modules = tuple(modules)
            self._scope_sources_by_mod = {
                key: tuple(values) for key, values in sources_by_mod.items()
            }
            current_mod = self.mod_dd.value
            known_mods = {ALL_MODS, *self._scope_modules}
            selected_mod = current_mod if current_mod in known_mods else ALL_MODS
            self.mod_dd.value = selected_mod
            self.mod_id = None if selected_mod == ALL_MODS else str(selected_mod)
            self._refresh_source_options()
            if not self._busy:
                self.status.value = "尚未掃描"
            if self._opened:
                self._flush(force=True)

        self._launch(
            work,
            "載入學名掃描範圍選單",
            presentation=OperationPresentation.MAINTENANCE,
        )

    def _refresh_source_options(self) -> None:
        """依目前選定模組重建學名掃描的目前生效來源清單。"""
        mod_key = self.mod_id or None
        codes = self._scope_sources_by_mod.get(mod_key, ())
        catalog = self._source_catalog
        options = [(ALL_SOURCES, "全部來源")] + [
            (str(code), catalog.label(code) if catalog is not None else str(code))
            for code in codes
        ]
        kit.set_dropdown_options(self.source_dd, options)
        keys = {key for key, _label in options}
        selected = (
            str(self.effective_source)
            if self.effective_source is not None
            else ALL_SOURCES
        )
        if selected not in keys:
            selected = ALL_SOURCES
        self.source_dd.value = selected
        self.effective_source = None if selected == ALL_SOURCES else int(selected)
        self.scope.value = self._scope_text()

    def _on_mod(self, _e=None) -> None:
        """模組變更時更新來源選單，並要求使用者重新掃描。"""
        value = self.mod_dd.value
        self.mod_id = None if value in (None, "", ALL_MODS) else str(value)
        self._refresh_source_options()
        self._invalidate_scan()

    def _on_source(self, _e=None) -> None:
        """目前生效來源變更時更新掃描範圍，不自動開始掃描。"""
        value = self.source_dd.value
        self.effective_source = None if value in (None, "", ALL_SOURCES) else int(value)
        self.scope.value = self._scope_text()
        self._invalidate_scan()

    def _on_loose_change(self, _e=None) -> None:
        """切換寬鬆學名條件時重新過濾模組與來源，但不自動掃描。"""
        self._invalidate_scan()
        self._start_scope_options_load()

    def _build_dialog(self) -> None:
        width, height = dialog_dimensions(self.page)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("學名候選掃描"),
            content=ft.Container(
                content=ft.Column(
                    [
                        self.scope,
                        self.hint,
                        ft.Row(
                            [self.mod_dd, self.source_dd],
                            spacing=10,
                            wrap=True,
                        ),
                        self.loose_check,
                        ft.Row(
                            [
                                self.scan_btn,
                                self.query_all_btn,
                                self.stop_btn,
                            ],
                            spacing=8,
                            wrap=True,
                        ),
                        self.status,
                        self.progress,
                        self.rows,
                        self.pager,
                    ],
                    spacing=10,
                    expand=True,
                ),
                width=width,
                height=height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[self.background_btn, self.close_btn],
            on_dismiss=self._on_dismiss,
        )

    def open(self) -> None:
        self.job.add_listener(self._on_job_event)
        if self.job.running:
            self._attach_running_job()
        try:
            present_page_dialog(self.page, self.dialog)
            self._opened = True
        except (AttributeError, RuntimeError):
            self.job.remove_listener(self._on_job_event)
            show_snack(self.page, "目前畫面無法開啟學名掃描視窗。", C.GOLD)

    def _detach(self) -> None:
        """忽略掃描與單筆查詢之後送回的結果；背景批次查詢照常進行。"""
        self._closed = True
        self._scope_seq += 1
        self._generation += 1
        self.job.remove_listener(self._on_job_event)

    def _hide(self) -> None:
        self._detach()
        close_page_dialog(self.page, self.dialog)

    def _close(self, _e=None) -> None:
        """「關閉」：停止進行中的批次查詢並關閉視窗。"""
        self.job.stop()
        self._hide()

    def _run_in_background(self, _e=None) -> None:
        """「背景執行」：收起視窗，批次查詢繼續，進度看頂列任務。"""
        self._hide()
        if self.job.running:
            show_snack(
                self.page,
                "學名查詢在背景繼續，進度顯示在頂列任務；再按「掃描學名」可回到視窗。",
                C.EM,
            )

    def _on_dismiss(self, _e=None) -> None:
        # Esc 等非按鈕關閉不視為「關閉」：只退訂，不停止背景查詢。
        self._detach()

    def _stop(self, _e=None) -> None:
        if not self.job.running:
            return
        self.job.stop()
        self._show_bulk_controls()
        self._set_bulk_progress()
        self._flush(force=True)

    # ------------------------------------------------------------------ 掃描
    def _scan(self, _e=None) -> None:
        if self._busy:
            return
        db = self.get_db()
        if db is None or not self.version:
            show_snack(self.page, "目前沒有可用的資料庫版本。", C.GOLD)
            return
        generation = self._generation
        loose = bool(self.loose_check.value)
        self._set_busy(True, "掃描目前範圍的英文原文…")

        def work() -> None:
            result = error = None
            try:
                result = scan_species_candidates(
                    db,
                    self.version,
                    mod_id=self.mod_id,
                    kind=self.kind,
                    effective_source=self.effective_source,
                    require_key_hint=not loose,
                )
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception("Mod 資料庫學名候選掃描失敗")
            self._post(
                self._apply_scan,
                generation,
                result,
                error,
                on_failure=lambda: setattr(self, "_busy", False),
            )

        if not self._launch(work, "Mod 資料庫學名候選掃描"):
            self._set_busy(False, "掃描無法啟動")

    def _apply_scan(
        self,
        generation: int,
        result: SpeciesScanResult | None,
        error: Exception | None,
    ) -> None:
        if generation != self._generation:
            return
        self._set_busy(False)
        if error is not None or result is None:
            self.status.value = f"掃描失敗：{error}"
            show_snack(self.page, f"學名候選掃描失敗：{error}", C.RED)
            self._flush(force=True)
            return
        self._show_result(result)
        cached = sum(1 for state in self._states.values() if state.done)
        self.status.value = f"找到 {result.total_candidates:,} 個候選" + (
            f"，其中 {cached:,} 個已查過" if cached else ""
        )
        self._flush(force=True)

    def _show_result(
        self,
        result: SpeciesScanResult,
        lookups: dict[int, SpeciesLookup] | None = None,
    ) -> None:
        """顯示候選列；沒有本次查詢結果的候選先帶入本機快取裡已查到的名稱。"""
        self.result = result
        lookups = lookups or {}
        self._states = {}
        for candidate in result.candidates:
            lookup = lookups.get(candidate.entry_id) or cached_species_lookup(
                candidate.value
            )
            self._states[candidate.entry_id] = _LookupState(lookup)
        self.pager.set_state(result.total_candidates, 1)
        self.pager.visible = result.total_candidates > PAGE_SIZE
        self._sync_query_all_enabled()
        self._render_rows()

    def _invalidate_scan(self) -> None:
        """清除舊掃描結果，要求使用者依新範圍重新掃描。"""
        self._generation += 1
        self.result = None
        self._states = {}
        self._row_index = {}
        self._page_candidates = []
        self.status.value = "尚未掃描"
        self.pager.visible = False
        self.query_all_btn.disabled = True
        self.rows.controls = [kit.hint_text("按「掃描候選」開始檢查目前範圍。")]
        self._flush(force=True)

    def _on_page(self, page: int) -> None:
        self.pager.set_state(self.pager.total_items, page)
        self._render_rows()
        self._flush(force=True)

    # ------------------------------------------------------------------ 單筆查詢
    def _abandon_scope_load(self) -> None:
        """範圍選單結果無法排回畫面時，結束載入中狀態。"""
        self._scope_loading = False

    def _abandon_lookup(self, candidate: SpeciesCandidate) -> None:
        """單筆查詢結果無法排回畫面時，結束這一筆的執行中狀態。"""
        state = self._states.get(candidate.entry_id)
        if state is not None:
            state.running = False

    def _any_running(self) -> bool:
        return any(state.running for state in self._states.values())

    def _query_one(
        self, candidate: SpeciesCandidate, _e=None, *, refresh: bool = False
    ) -> None:
        """查詢一筆候選；``refresh`` 用於「再查一次」，略過快取裡的查無紀錄。"""
        state = self._states.get(candidate.entry_id)
        if state is None or state.running or self._locked():
            return
        if state.done and not refresh:
            return
        generation = self._generation
        state.running = True
        self._refresh_row(candidate)
        self._sync_query_all_enabled()
        self._flush(force=True)

        def work() -> None:
            lookup = bulk.safe_lookup(candidate.value, refresh=refresh)
            self._post(
                self._apply_lookup,
                generation,
                candidate.entry_id,
                lookup,
                on_failure=lambda: self._abandon_lookup(candidate),
            )

        if not self._launch(work, "Mod 資料庫學名查詢"):
            state.running = False
            self._refresh_row(candidate)
            self._sync_query_all_enabled()
            self._flush(force=True)

    # ------------------------------------------------------------------ 批次查詢
    def _query_all(self, _e=None) -> None:
        if self._locked() or self._any_running() or self.result is None:
            return
        pending = [
            candidate
            for candidate in self.result.candidates
            if not self._states[candidate.entry_id].done
        ]
        if not pending:
            return
        refresh_ids = [
            candidate.entry_id
            for candidate in pending
            if bulk.retry_needs_refresh(self._states[candidate.entry_id].lookup)
        ]
        if not self.job.start(
            self.result, pending, self._scope_text(), refresh_ids=refresh_ids
        ):
            self._flush(force=True)
            return
        if not self.job.running:  # 已經結束：結果由 finished 事件顯示
            return
        self._show_bulk_controls()
        self._set_bulk_progress()
        self._flush(force=True)

    def _attach_running_job(self) -> None:
        """重新開啟視窗時接回進行中的背景查詢與它的候選範圍。"""
        job = self.job
        self.scope.value = job.scope_text
        if job.result is not None:
            self._show_result(job.result, job.lookups)
        self._show_bulk_controls()
        self._set_bulk_progress()

    def _show_bulk_controls(self) -> None:
        """依背景查詢是否進行中切換按鈕與進度列。"""
        running = self.job.running
        self.stop_btn.visible = running
        self.background_btn.visible = running
        self.stop_btn.disabled = self.job.stopping
        self.scan_btn.disabled = running or self._busy
        self.loose_check.disabled = running or self._busy
        self.progress.visible = running or self._busy
        self._sync_query_all_enabled()
        self._render_rows()

    def _set_bulk_progress(self) -> None:
        job = self.job
        if job.stopping:
            self.status.value = "正在停止…（目前這一筆查完後停止）"
        else:
            self.status.value = (
                f"查詢學名 {job.done}/{job.total}…（按「背景執行」可收起視窗繼續查詢）"
            )
        self.progress.value = job.done / job.total if job.total else None

    def _on_job_event(self, event: str, entry_id: int | None) -> None:
        if self.result is None or self.result is not self.job.result:
            return
        if event == "lookup" and entry_id is not None:
            state = self._states.get(entry_id)
            lookup = self.job.lookups.get(entry_id)
            if state is not None and lookup is not None:
                state.running = False
                state.lookup = lookup
                index = self._row_index.get(entry_id)
                if index is not None:
                    self._refresh_row(self._page_candidates[index])
            self._set_bulk_progress()
            self._flush()
            return
        if event == "finished":
            self._show_bulk_controls()
            reason = self.job.stop_reason or "completed"
            self.status.value = self._summary_text(bulk.STOP_MESSAGES[reason])
            self._flush(force=True)

    def _summary_text(self, prefix: str) -> str:
        return bulk.summary_text(prefix, (s.lookup for s in self._states.values()))

    def _apply_lookup(
        self, generation: int, entry_id: int, lookup: SpeciesLookup
    ) -> None:
        if generation != self._generation:
            return
        state = self._states.get(entry_id)
        if state is None:
            return
        state.running = False
        state.lookup = lookup
        index = self._row_index.get(entry_id)
        if index is not None:
            self._refresh_row(self._page_candidates[index])
        self._sync_query_all_enabled()
        if not lookup.definitive:
            show_snack(self.page, f"學名查詢失敗：{lookup.detail}", C.GOLD)
        self._flush(force=True)

    # ------------------------------------------------------------------ 畫面
    def _status_for(self, state: _LookupState) -> tuple[str, str]:
        lookup = state.lookup
        if state.running:
            return "查詢中…", C.GOLD
        if lookup is None:
            return "格式候選，尚未查詢", C.MUTED
        if lookup.status == STATUS_FOUND:
            return f"找到：{lookup.value}", C.EM
        if lookup.status == STATUS_NOT_FOUND:
            if reference := bulk.parent_reference_text(lookup):
                return f"找不到對應條目；{reference}（僅供參考）", C.GOLD
            if issue := bulk.parent_issue_text(lookup):
                return f"找不到對應條目；{issue}，可按「補查上一層」", C.GOLD
            return "找不到對應條目", C.GOLD
        if lookup.status == STATUS_RATE_LIMITED:
            return f"請求過於頻繁，稍後可重試（{lookup.detail}）", C.RED
        return f"查詢失敗，可重試（{lookup.detail}）", C.RED

    def _build_row(self, candidate: SpeciesCandidate) -> ft.Container:
        state = self._states[candidate.entry_id]
        status, color = self._status_for(state)
        metadata = (
            f"{candidate.mod_id} · {kind_label(candidate.kind)} · {candidate.key}"
        )
        lookup = state.lookup
        if lookup is None:
            label, icon = "查詢", ft.Icons.SEARCH
        elif lookup.definitive:  # 原名稱已確定查無，只差上一層物種
            label, icon = "補查上一層", ft.Icons.REFRESH
        else:
            label, icon = "重試", ft.Icons.REFRESH
        refresh = bulk.retry_needs_refresh(lookup)
        actions = []
        if not state.done:
            actions.append(
                kit.button(
                    label,
                    "secondary",
                    size="sm",
                    icon=icon,
                    on_click=lambda _e, item=candidate, refresh=refresh: (
                        self._query_one(item, refresh=refresh)
                    ),
                    disabled=state.running or self._locked(),
                )
            )
        elif state.lookup is not None and state.lookup.status == STATUS_NOT_FOUND:
            actions.append(
                kit.button(
                    "再查一次",
                    "ghost",
                    size="sm",
                    icon=ft.Icons.REFRESH,
                    on_click=lambda _e, item=candidate: self._query_one(
                        item, refresh=True
                    ),
                    disabled=state.running or self._locked(),
                    tooltip="略過快取重新查詢維基百科",
                )
            )
        actions.append(
            kit.button(
                "開啟條目",
                "secondary",
                size="sm",
                icon=ft.Icons.OPEN_IN_NEW,
                on_click=lambda _e, entry_id=candidate.entry_id: self._open_entry(
                    entry_id
                ),
            )
        )
        return ft.Container(
            ft.Column(
                [
                    ft.Text(candidate.value, size=14, weight=ft.FontWeight.BOLD),
                    ft.Text(metadata, size=11, color=C.MUTED, selectable=True),
                    ft.Text(status, size=12, color=color),
                    ft.Row(actions, spacing=6, wrap=True),
                ],
                spacing=4,
            ),
            padding=10,
            border=ft.Border.all(1, design.tone("dia").line),
            border_radius=design.RADIUS_CONTROL,
        )

    def _render_rows(self) -> None:
        result = self.result
        if result is None or not result.candidates:
            self._row_index = {}
            self._page_candidates = []
            self.rows.controls = [kit.hint_text("目前範圍沒有符合條件的學名候選。")]
            return
        first = (self.pager.current_page - 1) * PAGE_SIZE
        self._page_candidates = list(result.candidates[first : first + PAGE_SIZE])
        self._row_index = {
            candidate.entry_id: index
            for index, candidate in enumerate(self._page_candidates)
        }
        self.rows.controls = [
            self._build_row(candidate) for candidate in self._page_candidates
        ]

    def _refresh_row(self, candidate: SpeciesCandidate) -> None:
        """只重建這一筆候選列；已掛載的 Flet 控制項要替換整個列才會更新。"""
        index = self._row_index.get(candidate.entry_id)
        if index is not None and index < len(self.rows.controls):
            self.rows.controls[index] = self._build_row(candidate)

    def _locked(self) -> bool:
        """掃描中或背景批次查詢中，不接受新的掃描或查詢。"""
        return self._busy or self.job.running

    def _sync_query_all_enabled(self) -> None:
        has_pending = any(not state.done for state in self._states.values())
        self.query_all_btn.disabled = (
            self._locked() or self._any_running() or not has_pending
        )

    def _open_entry(self, entry_id: int) -> None:
        self._hide()  # 開啟條目不中斷背景查詢
        if self.on_open_entry is not None:
            self.on_open_entry(entry_id)

    # ------------------------------------------------------------------ 背景工作
    def _launch(
        self,
        work,
        name: str,
        *,
        presentation: OperationPresentation = OperationPresentation.USER_VISIBLE,
    ) -> bool:
        """啟動背景工作；被拒絕或啟動失敗時回傳 ``False``，由呼叫端還原畫面。"""
        try:
            handle = launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-species-dialog",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                commit=CommitPolicy.EPHEMERAL,
                durability=DurabilityPolicy.RECOMPUTABLE,
                shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
                presentation=presentation,
            )
        except Exception as exc:  # noqa: BLE001 - restore UI after launch failure
            log_exception(f"{name}無法啟動")
            show_snack(self.page, f"{name}無法啟動：{exc}", C.RED)
            return False
        if handle is False:
            show_snack(self.page, f"{name}無法啟動：應用程式正在關閉。", C.GOLD)
            return False
        return True

    def _post(self, callback, *args, on_failure=None) -> None:
        """把背景結果排回畫面執行緒；排程失敗時由呼叫端提供的 ``on_failure`` 回收狀態。"""
        post_to_ui(
            self.page, callback, *args, label="學名掃描結果", on_failure=on_failure
        )

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.scan_btn.disabled = busy or self.job.running
        self.loose_check.disabled = busy or self.job.running
        self.mod_dd.disabled = busy or self.job.running
        self.source_dd.disabled = busy or self.job.running
        self._sync_query_all_enabled()
        self.progress.visible = busy or self.job.running
        self.progress.value = None if busy else 0
        if text:
            self.status.value = text
        self._flush(force=True)

    def _flush(self, *, force: bool = False) -> None:
        """重繪畫面；批次查詢時節流，避免每完成一筆就整頁更新。"""
        now = time.monotonic()
        if not force and now - self._last_flush < UI_FLUSH_INTERVAL_SECONDS:
            return
        self._last_flush = now
        try:
            self.page.update()
        except RuntimeError:
            pass


__all__ = ["SpeciesCandidateDialog"]
