"""Mod 資料庫的學名批次查詢背景工作。

工作屬於頁面而不是學名掃描視窗：關閉視窗後仍會繼續查詢，進度顯示在頂列任務膠囊，
查到的名稱寫入本機學名快取，條目校對的「學名」分頁可以直接讀到。
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable, Collection, Sequence

import flet as ft

from app.services_impl.moddb_service import (
    STATUS_ERROR,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    SpeciesCandidate,
    SpeciesLookup,
    SpeciesScanResult,
    lookup_species,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.tasks.task_session import TaskSession, tag_session
from app.ui.design import C
from app.ui.page_post import post_to_ui
from app.ui.snack import show_snack
from translation_tool.utils.log_unit import log_exception, log_warning
from translation_tool.utils.ui_mirror import session_task_scope

TASK_NAME = "學名批次查詢"
# 連續幾筆失敗（非「查無」）就停止，避免離線時逐筆等待逾時。
MAX_CONSECUTIVE_FAILURES = 3

STOP_MESSAGES = {
    "completed": "學名查詢完成",
    "cancelled": "已停止查詢",
    "rate_limited": "維基百科回應請求過於頻繁，已停止；稍後再按「查詢全部候選」繼續",
    "failures": "連續查詢失敗，已停止；請檢查網路後再按「查詢全部候選」重試",
    "error": "學名批次查詢發生錯誤，已停止",
}

# 事件：``lookup``（一筆結果送達，附 entry_id）與 ``finished``（工作結束）。
Listener = Callable[[str, int | None], None]

_PAGE_ATTR = "_moddb_species_bulk_lookup"


def safe_lookup(value: str, *, refresh: bool = False) -> SpeciesLookup:
    """查詢學名；例外轉成可重試的失敗結果，不讓背景工作中斷。

    ``refresh`` 略過快取重新連線（「再查一次」用）。
    """
    try:
        return lookup_species(value, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - worker boundary
        log_warning("學名查詢失敗 %r：%r", value, exc)
        return SpeciesLookup(STATUS_ERROR, None, f"{type(exc).__name__}: {exc}")


def retry_needs_refresh(lookup: SpeciesLookup | None) -> bool:
    """上次查詢是暫時性失敗：重試時要略過快取。

    失敗結果不寫入快取，所以快取裡若有紀錄，一定比這次失敗更舊（例如「再查一次」
    之前的查無），重試必須重新連線才算真的重試。上一層物種待查（原名稱已確定查無）
    不在此列：補查上一層不需要重查原名稱。
    """
    return lookup is not None and not lookup.definitive


def parent_reference_text(lookup: SpeciesLookup) -> str:
    """查無時附上的上一層物種參考，例如「上一層物種 Fagus sylvatica：歐洲山毛櫸」。"""
    if lookup.parent_name and lookup.parent_value:
        return f"上一層物種 {lookup.parent_name}：{lookup.parent_value}"
    return ""


def parent_issue_text(lookup: SpeciesLookup) -> str:
    """上一層物種還沒有確定結果時的說明：查詢失敗，或尚未查詢。"""
    if lookup.parent_resolved:
        return ""
    if lookup.parent_status:
        return f"上一層物種 {lookup.parent_name} 查詢失敗（{lookup.parent_detail}）"
    return f"上一層物種 {lookup.parent_name} 尚未查詢"


def summary_text(prefix: str, lookups) -> str:
    """把查詢結果整理成「找到 N、查無 N、失敗可重試 N」摘要。"""
    lookups = [item for item in lookups if item is not None]
    found = sum(1 for item in lookups if item.status == STATUS_FOUND)
    missing = sum(1 for item in lookups if item.status == STATUS_NOT_FOUND)
    retry = sum(1 for item in lookups if item.failed)
    parts = [f"找到 {found}", f"查無 {missing}"]
    if retry:
        parts.append(f"失敗可重試 {retry}")
    return f"{prefix}（{'、'.join(parts)}）"


class SpeciesBulkLookup:
    """一個頁面同時只有一個學名批次查詢；狀態欄位只在 UI 執行緒讀寫。"""

    def __init__(self, page: ft.Page):
        self.page = page
        self.session: TaskSession | None = None
        self.result: SpeciesScanResult | None = None
        self.scope_text = ""
        self.lookups: dict[int, SpeciesLookup] = {}
        self.done = 0
        self.total = 0
        self.running = False
        self.stop_reason: str | None = None
        self._cancel = threading.Event()
        self._listeners: list[Callable[[], Listener | None]] = []
        self._generation = 0

    # ------------------------------------------------------------------ 訂閱
    def add_listener(self, listener: Listener) -> None:
        """訂閱查詢事件；bound method 以弱參照保存，畫面被丟棄後自動退訂。"""
        self.remove_listener(listener)
        ref = (
            weakref.WeakMethod(listener)
            if hasattr(listener, "__self__")
            else (lambda: listener)
        )
        self._listeners.append(ref)

    def remove_listener(self, listener: Listener) -> None:
        self._listeners = [
            ref for ref in self._listeners if ref() not in (None, listener)
        ]

    def _notify(self, event: str, entry_id: int | None = None) -> None:
        for ref in list(self._listeners):
            listener = ref()
            if listener is None:
                continue
            try:
                listener(event, entry_id)
            except Exception:  # noqa: BLE001 - one closed view must not stop others
                log_exception("學名批次查詢畫面更新失敗")

    # ------------------------------------------------------------------ 控制
    @property
    def stopping(self) -> bool:
        return self.running and self._cancel.is_set()

    def start(
        self,
        result: SpeciesScanResult,
        pending: Sequence[SpeciesCandidate],
        scope_text: str,
        refresh_ids: Collection[int] = (),
    ) -> bool:
        """開始在背景查詢 ``pending``；已有工作在跑或無法啟動時回傳 ``False``。

        ``refresh_ids`` 列出要略過快取重新連線的候選（上次查詢失敗的重試）。
        """
        if self.running or not pending:
            return False
        self._generation += 1
        generation = self._generation
        self.result = result
        self.scope_text = scope_text
        self.lookups = {}
        self.done = 0
        self.total = len(pending)
        self.stop_reason = None
        self._cancel.clear()
        session = tag_session(TaskSession(), TASK_NAME, "moddb", page=self.page)
        self.session = session
        self.running = True
        items = list(pending)
        refresh = frozenset(refresh_ids)

        def work() -> None:
            self._run(generation, session, items, refresh)

        try:
            handle = launch_page_operation(
                self.page,
                work,
                name=TASK_NAME,
                owner="moddb-species-bulk-lookup",
                task_session=session,
                cancellation=CancellationPolicy.COOPERATIVE,
                commit=CommitPolicy.EPHEMERAL,
                durability=DurabilityPolicy.RECOMPUTABLE,
                shutdown=ShutdownPolicy.CANCEL_AND_DRAIN,
                on_cancel=self._cancel.set,
            )
        except Exception as exc:  # noqa: BLE001 - restore state after launch failure
            log_exception(f"{TASK_NAME}無法啟動")
            show_snack(self.page, f"{TASK_NAME}無法啟動：{exc}", C.RED)
            handle = None
        if handle is False:
            show_snack(self.page, f"{TASK_NAME}無法啟動：應用程式正在關閉。", C.GOLD)
        if handle is None or handle is False:
            self.running = False
            self.session = None
            return False
        return True

    def stop(self) -> None:
        """要求停止；目前這一筆查完後停止。"""
        if not self.running:
            return
        self._cancel.set()
        if self.session is not None:
            self.session.request_cancel()

    # ------------------------------------------------------------------ 背景執行緒
    def _cancelled(self, session: TaskSession) -> bool:
        return self._cancel.is_set() or session.cancel_requested

    def _run(
        self,
        generation: int,
        session: TaskSession,
        pending: list[SpeciesCandidate],
        refresh_ids: frozenset[int] = frozenset(),
    ) -> None:
        try:
            session.start()
        except Exception:  # noqa: BLE001 - admission closed while app shuts down
            log_exception(f"{TASK_NAME}無法開始")
            self._post(
                self._finish,
                generation,
                "error",
                [],
                on_failure=lambda: self._abandon(generation),
            )
            return
        total = len(pending)
        session.add_log(f"開始查詢 {total} 個學名候選")
        stop_reason = "completed"
        failures = 0
        lookups: list[SpeciesLookup] = []
        try:
            for index, candidate in enumerate(pending, start=1):
                if self._cancelled(session):
                    stop_reason = "cancelled"
                    break
                lookup = safe_lookup(
                    candidate.value, refresh=candidate.entry_id in refresh_ids
                )
                lookups.append(lookup)
                session.set_progress(index / total)
                self._post(self._record, generation, candidate.entry_id, lookup, index)
                if lookup.rate_limited:  # 含上一層物種查詢遇到的限流
                    stop_reason = "rate_limited"
                    break
                failures = failures + 1 if lookup.failed else 0
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    stop_reason = "failures"
                    break
        except Exception:  # noqa: BLE001 - worker boundary
            # 此工作執行緒沒有任務歸屬；重新歸屬讓 app.log 的完整堆疊帶 [task=…]，
            # 畫面只顯示下方 add_log 的摘要
            with session_task_scope(session):
                log_exception(f"{TASK_NAME}失敗")
            stop_reason = "error"
            session.set_error()
        message = summary_text(STOP_MESSAGES[stop_reason], lookups)
        session.add_log(message, "info" if stop_reason == "completed" else "warning")
        session.set_summary({"stop_reason": stop_reason, "queried": len(lookups)})
        session.finish()
        self._post(
            self._finish,
            generation,
            stop_reason,
            lookups,
            on_failure=lambda: self._abandon(generation),
        )

    # ------------------------------------------------------------------ UI 執行緒
    def _record(
        self, generation: int, entry_id: int, lookup: SpeciesLookup, done: int
    ) -> None:
        if generation != self._generation:
            return
        self.lookups[entry_id] = lookup
        self.done = done
        self._notify("lookup", entry_id)

    def _finish(
        self, generation: int, stop_reason: str, lookups: list[SpeciesLookup]
    ) -> None:
        if generation != self._generation:
            return
        self.running = False
        self.stop_reason = stop_reason
        self._notify("finished")
        color = C.EM if stop_reason == "completed" else C.GOLD
        show_snack(self.page, summary_text(STOP_MESSAGES[stop_reason], lookups), color)

    def _post(self, callback, *args, on_failure=None) -> None:
        """把背景結果排回畫面執行緒；排程失敗時由呼叫端提供的 ``on_failure`` 回收狀態。"""
        post_to_ui(
            self.page, callback, *args, label="學名批次查詢結果", on_failure=on_failure
        )

    def _abandon(self, generation: int) -> None:
        """結束通知無法排回畫面時，收回執行中狀態，讓下一次查詢可以啟動。"""
        if generation == self._generation:
            self.running = False
            self.session = None


def bulk_lookup_for(page: ft.Page) -> SpeciesBulkLookup:
    """取得這個頁面共用的學名批次查詢（不存在時建立）。"""
    job = getattr(page, _PAGE_ATTR, None)
    if not isinstance(job, SpeciesBulkLookup):
        job = SpeciesBulkLookup(page)
        setattr(page, _PAGE_ATTR, job)
    return job


__all__ = [
    "MAX_CONSECUTIVE_FAILURES",
    "STOP_MESSAGES",
    "SpeciesBulkLookup",
    "bulk_lookup_for",
    "parent_issue_text",
    "parent_reference_text",
    "retry_needs_refresh",
    "safe_lookup",
    "summary_text",
]
