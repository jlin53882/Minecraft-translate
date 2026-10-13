"""Mod 資料庫的學名候選掃描與查詢視窗。"""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

from app.services_impl.moddb_service import (
    SpeciesCandidate,
    SpeciesScanResult,
    lookup_species_name,
    scan_species_candidates,
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
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    close_page_dialog,
    dialog_dimensions,
    present_page_dialog,
)
from app.ui.snack import show_snack
from app.views.moddb.formatting import kind_label
from translation_tool.utils.log_unit import log_exception, log_warning


@dataclass
class _LookupState:
    value: str | None = None
    running: bool = False
    done: bool = False


class SpeciesCandidateDialog:
    """在目前條目篩選範圍內找出並查詢可能的學名。"""

    def __init__(
        self,
        page: ft.Page,
        get_db,
        version: str | None,
        mod_id: str | None,
        kind: str | None,
        on_open_entry=None,
    ):
        self.page = page
        self.get_db = get_db
        self.version = version
        self.mod_id = mod_id
        self.kind = kind
        self.on_open_entry = on_open_entry
        self.result: SpeciesScanResult | None = None
        self._states: dict[int, _LookupState] = {}
        self._busy = False
        self._generation = 0
        self._build_controls()
        self._build_dialog()

    def _scope_text(self) -> str:
        version = self.version or "未選擇版本"
        mod = self.mod_id or "全部模組"
        kind = kind_label(self.kind) if self.kind else "全部類型"
        return f"目前範圍：{version}／{mod}／{kind}"

    def _build_controls(self) -> None:
        self.scope = ft.Text(self._scope_text(), size=13, color=C.TEXT)
        self.hint = ft.Text(
            "掃描只依格式找出候選；按「查詢全部」才會逐筆查詢學名資料庫。",
            size=12,
            color=C.MUTED,
            selectable=True,
        )
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
        self.status = ft.Text("尚未掃描", size=12, color=C.MUTED)
        self.progress = ft.ProgressBar(visible=False)
        self.rows = ft.ListView(
            spacing=8,
            expand=True,
            height=400,
            controls=[kit.hint_text("按「掃描候選」開始檢查目前範圍。")],
        )
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

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
                            [self.scan_btn, self.query_all_btn],
                            spacing=8,
                            wrap=True,
                        ),
                        self.status,
                        self.progress,
                        self.rows,
                    ],
                    tight=True,
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=width,
                height=height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[self.close_btn],
            on_dismiss=self._on_dismiss,
        )

    def open(self) -> None:
        try:
            present_page_dialog(self.page, self.dialog)
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟學名掃描視窗。", C.GOLD)

    def _close(self, _e=None) -> None:
        if self._busy:
            show_snack(self.page, "學名查詢中，請稍候。", C.GOLD)
            return
        self._generation += 1
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        self._generation += 1

    def _scan(self, _e=None) -> None:
        if self._busy:
            return
        db = self.get_db()
        if db is None or not self.version:
            show_snack(self.page, "目前沒有可用的資料庫版本。", C.GOLD)
            return
        generation = self._generation
        self._set_busy(True, "掃描目前版本的英文原文…")

        def work() -> None:
            result = error = None
            try:
                result = scan_species_candidates(
                    db,
                    self.version,
                    mod_id=self.mod_id,
                    kind=self.kind,
                )
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_exception("Mod 資料庫學名候選掃描失敗")
            self._post(self._apply_scan, generation, result, error)

        self._launch(work, "Mod 資料庫學名候選掃描")

    def _apply_scan(
        self,
        generation: int,
        result: SpeciesScanResult | None,
        error: Exception | None,
    ) -> None:
        self._set_busy(False)
        if generation != self._generation:
            return
        if error is not None or result is None:
            self.status.value = f"掃描失敗：{error}"
            show_snack(self.page, f"學名候選掃描失敗：{error}", C.RED)
            self._safe_update()
            return
        self.result = result
        self._states = {
            candidate.entry_id: _LookupState() for candidate in result.candidates
        }
        if result.truncated:
            suffix = "；候選超過 500 筆，請縮小版本／模組／類型範圍"
        else:
            suffix = ""
        self.status.value = f"掃描 {result.scanned_entries:,} 筆，找到 {result.total_candidates:,} 個候選{suffix}"
        self.query_all_btn.disabled = not result.candidates
        self._render_rows()
        self._safe_update()

    def _query_one(self, candidate: SpeciesCandidate, _e=None) -> None:
        state = self._states.get(candidate.entry_id)
        if state is None or state.running or self._busy:
            return
        generation = self._generation
        state.running = True
        self._render_rows()
        self._safe_update()

        def work() -> None:
            result = error = None
            try:
                result = lookup_species_name(candidate.value)
            except Exception as exc:  # noqa: BLE001 - worker boundary
                error = exc
                log_warning("學名查詢失敗 %r：%r", candidate.value, exc)
            self._post(
                self._apply_lookup, generation, candidate.entry_id, result, error
            )

        self._launch(work, "Mod 資料庫學名查詢", allow_parallel=True)

    def _query_all(self, _e=None) -> None:
        if self._busy or self.result is None:
            return
        pending = [
            candidate
            for candidate in self.result.candidates
            if not self._states[candidate.entry_id].done
        ]
        if not pending:
            return
        generation = self._generation
        self._set_busy(True, f"查詢學名 0/{len(pending)}…")

        def work() -> None:
            for index, candidate in enumerate(pending, start=1):
                try:
                    result = lookup_species_name(candidate.value)
                    error = None
                except Exception as exc:  # noqa: BLE001 - worker boundary
                    result, error = None, exc
                    log_warning("學名查詢失敗 %r：%r", candidate.value, exc)
                self._post(
                    self._apply_lookup,
                    generation,
                    candidate.entry_id,
                    result,
                    error,
                )
                self._post(self._set_query_progress, generation, index, len(pending))
            self._post(self._finish_bulk_lookup, generation)

        self._launch(work, "Mod 資料庫學名批次查詢")

    def _set_query_progress(self, generation: int, done: int, total: int) -> None:
        if generation == self._generation:
            self.status.value = f"查詢學名 {done}/{total}…"
            self._safe_update()

    def _finish_bulk_lookup(self, generation: int) -> None:
        if generation != self._generation:
            return
        self._set_busy(False)
        self.status.value = "學名查詢完成"
        self._safe_update()

    def _apply_lookup(
        self,
        generation: int,
        entry_id: int,
        result: str | None,
        error: Exception | None,
    ) -> None:
        if generation != self._generation:
            return
        state = self._states.get(entry_id)
        if state is None:
            return
        state.running = False
        state.done = True
        state.value = result
        self._render_rows()
        if error is not None:
            show_snack(self.page, f"學名查詢失敗：{error}", C.GOLD)
        self._safe_update()

    def _render_rows(self) -> None:
        result = self.result
        if result is None or not result.candidates:
            self.rows.controls = [kit.hint_text("目前範圍沒有符合格式的學名候選。")]
            return
        controls = []
        for candidate in result.candidates:
            state = self._states[candidate.entry_id]
            if state.running:
                status = "查詢中…"
                color = C.GOLD
            elif not state.done:
                status = "格式候選，尚未查詢"
                color = C.MUTED
            elif state.value:
                status = f"找到：{state.value}"
                color = C.EM
            else:
                status = "找不到對應條目"
                color = C.GOLD
            metadata = (
                f"{candidate.mod_id} · {kind_label(candidate.kind)} · {candidate.key}"
            )
            actions = [
                kit.button(
                    "查詢",
                    "secondary",
                    size="sm",
                    icon=ft.Icons.SEARCH,
                    on_click=lambda _e, item=candidate: self._query_one(item),
                    disabled=state.done or self._busy,
                ),
                kit.button(
                    "開啟條目",
                    "ghost",
                    size="sm",
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=lambda _e, entry_id=candidate.entry_id: self._open_entry(
                        entry_id
                    ),
                ),
            ]
            controls.append(
                ft.Container(
                    ft.Column(
                        [
                            ft.Text(
                                candidate.value, size=14, weight=ft.FontWeight.BOLD
                            ),
                            ft.Text(metadata, size=11, color=C.MUTED, selectable=True),
                            ft.Row(
                                [
                                    ft.Text(status, size=12, color=color, expand=True),
                                    *actions,
                                ],
                                spacing=6,
                                wrap=True,
                            ),
                        ],
                        spacing=4,
                    ),
                    padding=10,
                    border=ft.Border.all(1, design.tone("dia").line),
                    border_radius=design.RADIUS_CONTROL,
                )
            )
        self.rows.controls = controls

    def _open_entry(self, entry_id: int) -> None:
        if self._busy:
            return
        self._generation += 1
        close_page_dialog(self.page, self.dialog)
        if self.on_open_entry is not None:
            self.on_open_entry(entry_id)

    def _launch(self, work, name: str, *, allow_parallel: bool = False) -> None:
        try:
            launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-species-dialog",
                cancellation=(
                    CancellationPolicy.COOPERATIVE
                    if allow_parallel
                    else CancellationPolicy.NON_CANCELLABLE
                ),
                commit=CommitPolicy.EPHEMERAL,
                durability=DurabilityPolicy.RECOMPUTABLE,
                shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
            )
        except Exception as exc:  # noqa: BLE001 - restore UI after launch failure
            self._set_busy(False)
            log_exception(f"{name}無法啟動")
            show_snack(self.page, f"{name}無法啟動：{exc}", C.RED)

    def _post(self, callback, *args) -> None:
        run_task = getattr(self.page, "run_task", None)
        try:
            if callable(run_task):

                async def runner() -> None:
                    callback(*args)

                run_task(runner)
            else:
                callback(*args)
        except Exception as exc:  # noqa: BLE001 - disposed page
            log_warning("學名掃描結果無法排回畫面：%r", exc)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.scan_btn.disabled = busy
        self.query_all_btn.disabled = busy or not bool(
            self.result and self.result.candidates
        )
        self.close_btn.disabled = busy
        self.progress.visible = busy
        self.progress.value = None if busy else 0
        if text:
            self.status.value = text
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.page.update()
        except RuntimeError:
            pass


__all__ = ["SpeciesCandidateDialog"]
