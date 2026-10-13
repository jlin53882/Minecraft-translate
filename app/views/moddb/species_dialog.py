"""Mod 資料庫的學名候選掃描與查詢視窗。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import flet as ft

from app.services_impl.moddb_service import (
    DEFAULT_SCAN_LIMIT,
    STATUS_ERROR,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_RATE_LIMITED,
    SpeciesCandidate,
    SpeciesLookup,
    SpeciesScanResult,
    lookup_species,
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

# 批次查詢連續幾筆失敗（非「查無」）就停止，避免離線時逐筆等待逾時。
MAX_CONSECUTIVE_FAILURES = 3
# 批次查詢時畫面最多每隔這麼久才重繪一次。
UI_FLUSH_INTERVAL_SECONDS = 0.25

_STOP_MESSAGES = {
    "cancelled": "已停止查詢",
    "rate_limited": "維基百科回應請求過於頻繁，已停止；稍後再按「查詢全部候選」繼續",
    "failures": "連續查詢失敗，已停止；請檢查網路後再按「查詢全部候選」重試",
}


@dataclass
class _LookupState:
    lookup: SpeciesLookup | None = None
    running: bool = False

    @property
    def done(self) -> bool:
        """已有確定結果（找到或查無）；暫時性失敗不算完成，可以重試。"""
        return self.lookup is not None and self.lookup.definitive


def _safe_lookup(value: str) -> SpeciesLookup:
    try:
        return lookup_species(value)
    except Exception as exc:  # noqa: BLE001 - worker boundary
        log_warning("學名查詢失敗 %r：%r", value, exc)
        return SpeciesLookup(STATUS_ERROR, None, f"{type(exc).__name__}: {exc}")


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
        self._row_index: dict[int, int] = {}
        self._busy = False
        self._bulk_running = False
        self._cancel = threading.Event()
        self._last_flush = 0.0
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
        self.stop_btn = kit.button(
            "停止查詢",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            on_click=self._stop,
        )
        self.stop_btn.visible = False
        self.status = ft.Text("尚未掃描", size=12, color=C.MUTED)
        self.progress = ft.ProgressBar(visible=False)
        self.rows = ft.Column(
            spacing=8,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
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
                            [self.scan_btn, self.query_all_btn, self.stop_btn],
                            spacing=8,
                            wrap=True,
                        ),
                        self.status,
                        self.progress,
                        self.rows,
                    ],
                    spacing=10,
                    expand=True,
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

    def _discard_pending_work(self) -> None:
        """讓尚在背景執行的查詢停止，並忽略它們之後送回來的結果。"""
        self._generation += 1
        self._cancel.set()

    def _close(self, _e=None) -> None:
        self._discard_pending_work()
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        self._discard_pending_work()

    def _stop(self, _e=None) -> None:
        if not self._bulk_running:
            return
        self._cancel.set()
        self.stop_btn.disabled = True
        self.status.value = "正在停止…（目前這一筆查完後停止）"
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
        self.result = result
        self._states = {
            candidate.entry_id: _LookupState() for candidate in result.candidates
        }
        suffix = (
            f"；候選超過 {DEFAULT_SCAN_LIMIT} 筆，請縮小版本／模組／類型範圍"
            if result.truncated
            else ""
        )
        self.status.value = (
            f"掃描 {result.scanned_entries:,} 筆，"
            f"找到 {result.total_candidates:,} 個候選{suffix}"
        )
        self.query_all_btn.disabled = not result.candidates
        self._render_rows()
        self._flush(force=True)

    # ------------------------------------------------------------------ 單筆查詢
    def _any_running(self) -> bool:
        return any(state.running for state in self._states.values())

    def _query_one(self, candidate: SpeciesCandidate, _e=None) -> None:
        state = self._states.get(candidate.entry_id)
        if state is None or state.running or state.done or self._busy:
            return
        generation = self._generation
        state.running = True
        self._refresh_row(candidate)
        self._sync_query_all_enabled()
        self._flush(force=True)

        def work() -> None:
            lookup = _safe_lookup(candidate.value)
            self._post(self._apply_lookup, generation, candidate.entry_id, lookup)

        if not self._launch(work, "Mod 資料庫學名查詢"):
            state.running = False
            self._refresh_row(candidate)
            self._sync_query_all_enabled()
            self._flush(force=True)

    # ------------------------------------------------------------------ 批次查詢
    def _query_all(self, _e=None) -> None:
        if self._busy or self._any_running() or self.result is None:
            return
        pending = [
            candidate
            for candidate in self.result.candidates
            if not self._states[candidate.entry_id].done
        ]
        if not pending:
            return
        generation = self._generation
        self._cancel.clear()
        self._bulk_running = True
        self.stop_btn.visible = True
        self.stop_btn.disabled = False
        self._set_busy(True, f"查詢學名 0/{len(pending)}…")

        def work() -> None:
            stop_reason = "completed"
            failures = 0
            for index, candidate in enumerate(pending, start=1):
                if self._cancel.is_set():
                    stop_reason = "cancelled"
                    break
                lookup = _safe_lookup(candidate.value)
                self._post(self._apply_lookup, generation, candidate.entry_id, lookup)
                self._post(self._set_query_progress, generation, index, len(pending))
                if lookup.status == STATUS_RATE_LIMITED:
                    stop_reason = "rate_limited"
                    break
                failures = 0 if lookup.definitive else failures + 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    stop_reason = "failures"
                    break
            self._post(self._finish_bulk_lookup, generation, stop_reason)

        if not self._launch(work, "Mod 資料庫學名批次查詢", cancellable=True):
            self._finish_bulk_lookup(generation, "launch_failed")

    def _set_query_progress(self, generation: int, done: int, total: int) -> None:
        if generation == self._generation and not self._cancel.is_set():
            self.status.value = f"查詢學名 {done}/{total}…"
            self._flush()

    def _finish_bulk_lookup(self, generation: int, stop_reason: str) -> None:
        if generation != self._generation:
            return
        self._bulk_running = False
        self.stop_btn.visible = False
        self._set_busy(False)
        if stop_reason == "completed":
            self.status.value = self._summary_text("學名查詢完成")
        elif stop_reason == "launch_failed":
            self.status.value = "批次查詢無法啟動"
        else:
            self.status.value = self._summary_text(_STOP_MESSAGES[stop_reason])
        self._flush(force=True)

    def _summary_text(self, prefix: str) -> str:
        lookups = [s.lookup for s in self._states.values() if s.lookup is not None]
        found = sum(1 for item in lookups if item.status == STATUS_FOUND)
        missing = sum(1 for item in lookups if item.status == STATUS_NOT_FOUND)
        retry = sum(1 for item in lookups if not item.definitive)
        parts = [f"找到 {found}", f"查無 {missing}"]
        if retry:
            parts.append(f"失敗可重試 {retry}")
        return f"{prefix}（{'、'.join(parts)}）"

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
        if index is not None and self.result is not None:
            self._refresh_row(self.result.candidates[index])
        self._sync_query_all_enabled()
        if self._bulk_running:
            self._flush()
            return
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
        retry = state.lookup is not None and not state.lookup.definitive
        actions = []
        if not state.done:
            actions.append(
                kit.button(
                    "重試" if retry else "查詢",
                    "secondary",
                    size="sm",
                    icon=ft.Icons.REFRESH if retry else ft.Icons.SEARCH,
                    on_click=lambda _e, item=candidate: self._query_one(item),
                    disabled=state.running or self._busy,
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
            self.rows.controls = [kit.hint_text("目前範圍沒有符合格式的學名候選。")]
            return
        self._row_index = {
            candidate.entry_id: index
            for index, candidate in enumerate(result.candidates)
        }
        self.rows.controls = [
            self._build_row(candidate) for candidate in result.candidates
        ]

    def _refresh_row(self, candidate: SpeciesCandidate) -> None:
        """只重建這一筆候選列；已掛載的 Flet 控制項要替換整個列才會更新。"""
        index = self._row_index.get(candidate.entry_id)
        if index is not None and index < len(self.rows.controls):
            self.rows.controls[index] = self._build_row(candidate)

    def _sync_query_all_enabled(self) -> None:
        has_candidates = bool(self.result and self.result.candidates)
        self.query_all_btn.disabled = (
            self._busy or self._any_running() or not has_candidates
        )

    def _open_entry(self, entry_id: int) -> None:
        self._close()
        if self.on_open_entry is not None:
            self.on_open_entry(entry_id)

    # ------------------------------------------------------------------ 背景工作
    def _launch(self, work, name: str, *, cancellable: bool = False) -> bool:
        """啟動背景工作；被拒絕或啟動失敗時回傳 ``False``，由呼叫端還原畫面。"""
        try:
            handle = launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-species-dialog",
                cancellation=(
                    CancellationPolicy.COOPERATIVE
                    if cancellable
                    else CancellationPolicy.NON_CANCELLABLE
                ),
                commit=CommitPolicy.EPHEMERAL,
                durability=DurabilityPolicy.RECOMPUTABLE,
                shutdown=(
                    ShutdownPolicy.CANCEL_AND_DRAIN
                    if cancellable
                    else ShutdownPolicy.ALLOW_TO_FINISH
                ),
                on_cancel=self._cancel.set if cancellable else None,
            )
        except Exception as exc:  # noqa: BLE001 - restore UI after launch failure
            log_exception(f"{name}無法啟動")
            show_snack(self.page, f"{name}無法啟動：{exc}", C.RED)
            return False
        if handle is False:
            show_snack(self.page, f"{name}無法啟動：應用程式正在關閉。", C.GOLD)
            return False
        return True

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
        self._sync_query_all_enabled()
        self.progress.visible = busy
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
