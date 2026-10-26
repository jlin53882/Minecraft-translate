"""app/shell/task_list_dialog.py：本次執行的任務清單（工作台「本次任務」、頂列任務膠囊、通知選單共用）。

頂列膠囊只顯示最近開始的一個任務、通知選單只列最近幾筆；這個對話框列出本次執行的全部任務
（進行中＋已結束，已結束最多保留 ``HISTORY_LIMIT`` 筆），可依狀態篩選，點一列回到該頁。
資料整理（``build_task_rows``）是純函式，畫面由 ``TaskListDialog`` 負責。
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import flet as ft

from app.shell.task_manager import (
    HISTORY_LIMIT,
    STATUS_ABORTED,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_RUNNING,
    TaskInfo,
    TaskManager,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.ui.dialogs import close_page_dialog, dialog_dimensions, present_page_dialog

FILTER_ALL = "all"
FILTER_RUNNING = "running"
FILTER_DONE = "done"
FILTER_FAILED = "failed"
FILTER_CANCELLED = "cancelled"

# 篩選鍵 → 包含的任務狀態
_FILTER_STATUSES: dict[str, frozenset[str]] = {
    FILTER_RUNNING: frozenset({STATUS_RUNNING}),
    FILTER_DONE: frozenset({STATUS_DONE}),
    FILTER_FAILED: frozenset({STATUS_ERROR}),
    FILTER_CANCELLED: frozenset({STATUS_CANCELLED, STATUS_ABORTED}),
}
FILTER_LABELS = {
    FILTER_ALL: "全部",
    FILTER_RUNNING: "進行中",
    FILTER_DONE: "完成",
    FILTER_FAILED: "失敗",
    FILTER_CANCELLED: "已取消",
}
STATUS_STYLE = {
    STATUS_RUNNING: ("進行中", "gold"),
    STATUS_DONE: ("完成", "em"),
    STATUS_ERROR: ("失敗", "red"),
    STATUS_CANCELLED: ("已取消", "gold"),
    STATUS_ABORTED: ("已中止", "gold"),
}


@dataclass(frozen=True)
class TaskRow:
    """清單的一列（純資料）。"""

    name: str
    view_key: str | None
    page_label: str
    status: str
    status_text: str
    tone: str
    started: str  # 本機時間 HH:MM:SS
    duration: str


def format_duration(seconds: float) -> str:
    """「12 秒」「3 分 05 秒」「1 小時 02 分」。"""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} 秒"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小時 {minutes:02d} 分"


def _page_label(view_key: str | None) -> str:
    if not view_key:
        return ""
    from app.view_registry import VIEW_SPECS

    return next((spec.label for spec in VIEW_SPECS if spec.key == view_key), view_key)


def status_counts(
    active: Sequence[TaskInfo], finished_counts: Mapping[str, int]
) -> dict[str, int]:
    """各篩選鍵的筆數（已結束的部分用 ``TaskManager.finished_counts``，不受清單上限影響）。"""
    counts = {
        FILTER_RUNNING: len(active),
        FILTER_DONE: finished_counts.get(STATUS_DONE, 0),
        FILTER_FAILED: finished_counts.get(STATUS_ERROR, 0),
        FILTER_CANCELLED: finished_counts.get(STATUS_CANCELLED, 0)
        + finished_counts.get(STATUS_ABORTED, 0),
    }
    counts[FILTER_ALL] = sum(counts.values())
    return counts


def build_task_rows(
    active: Sequence[TaskInfo],
    history: Sequence[TaskInfo],
    status_filter: str = FILTER_ALL,
    *,
    now: float | None = None,
) -> list[TaskRow]:
    """進行中（新的在前）＋已結束（新的在前），依篩選鍵過濾。"""
    now = time.time() if now is None else now
    allowed = _FILTER_STATUSES.get(status_filter)
    running = sorted(active, key=lambda task: task.started_at, reverse=True)
    rows: list[TaskRow] = []
    for task in [*running, *history]:
        status = STATUS_RUNNING if task.running else task.status
        if allowed is not None and status not in allowed:
            continue
        text, tone = STATUS_STYLE.get(status, ("完成", "em"))
        if status == STATUS_RUNNING:
            text = f"進行中 {task.percent}%" if task.progress > 0 else "準備中"
        rows.append(
            TaskRow(
                name=task.name,
                view_key=task.view_key,
                page_label=_page_label(task.view_key),
                status=status,
                status_text=text,
                tone=tone,
                started=dt.datetime.fromtimestamp(task.started_at, dt.UTC)
                .astimezone()
                .strftime("%H:%M:%S"),
                duration=format_duration(task.elapsed(now)),
            )
        )
    return rows


class TaskListDialog:
    """任務清單對話框；``open`` 顯示，``refresh`` 由外殼在任務變動時呼叫（已在 UI 執行緒）。"""

    def __init__(
        self,
        page: ft.Page,
        tasks: TaskManager,
        *,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        self._page = page
        self._tasks = tasks
        self._on_navigate = on_navigate
        self._filter = FILTER_ALL
        self._dialog: ft.AlertDialog | None = None
        self.summary = ft.Text("", size=12.5, color=C.MUTED)
        self.filter_bar = kit.Segmented(
            [(key, label) for key, label in FILTER_LABELS.items()],
            FILTER_ALL,
            self._set_filter,
        )
        self.list_view = ft.ListView(spacing=2, expand=True)
        self.footnote = ft.Text("", size=11.5, color=C.DIM, visible=False)

    @property
    def is_open(self) -> bool:
        return bool(self._dialog is not None and getattr(self._dialog, "open", False))

    def open(self, status_filter: str = FILTER_ALL) -> None:
        self._filter = status_filter
        self.filter_bar.select(status_filter)
        self.refresh(update=False)
        width, height = dialog_dimensions(
            self._page, width_ratio=0.55, max_width=760, max_height=620
        )
        self._dialog = ft.AlertDialog(
            modal=False,
            title=ft.Text("本次任務", size=18, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                width=width,
                height=height,
                content=ft.Column(
                    [self.summary, self.filter_bar, self.list_view, self.footnote],
                    spacing=10,
                    expand=True,
                ),
            ),
            actions=[kit.button("關閉", "secondary", on_click=lambda _e: self.close())],
            on_dismiss=lambda _e: setattr(self, "_dialog", None),
        )
        present_page_dialog(self._page, self._dialog)

    def close(self) -> None:
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            close_page_dialog(self._page, dialog)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self.refresh()

    def refresh(self, *, update: bool = True) -> None:
        active = self._tasks.active()
        history = self._tasks.history()
        counts = status_counts(active, self._tasks.finished_counts())
        self.summary.value = "・".join(
            f"{FILTER_LABELS[key]} {counts[key]:,}"
            for key in (FILTER_RUNNING, FILTER_DONE, FILTER_FAILED, FILTER_CANCELLED)
        )
        rows = build_task_rows(active, history, self._filter)
        self.list_view.controls = [self._row(row) for row in rows] or [
            kit.empty_state(
                "沒有符合的任務",
                "切換上方的狀態篩選，或到左側選一個流程開始",
                icon=ft.Icons.HISTORY,
            )
        ]
        hidden = counts[FILTER_ALL] - len(active) - len(history)
        self.footnote.value = f"清單只保留最近 {HISTORY_LIMIT} 筆已結束的任務；更早的 {hidden:,} 筆只計入上方統計"
        self.footnote.visible = hidden > 0
        if update and self.is_open:
            try:
                self._dialog.update()
            except (AssertionError, RuntimeError):
                pass

    def _row(self, row: TaskRow) -> ft.Control:
        tone = get_tone(row.tone)
        meta = "・".join(
            part
            for part in (row.page_label, f"開始 {row.started}", row.duration)
            if part
        )
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            border_radius=design.RADIUS_CONTROL,
            ink=bool(row.view_key),
            on_click=(lambda _e, key=row.view_key: self._navigate(key))
            if row.view_key
            else None,
            tooltip=f"前往 {row.page_label}" if row.view_key else None,
            content=ft.Row(
                [
                    ft.Container(
                        width=9,
                        height=9,
                        border_radius=5,
                        border=ft.Border.all(2, tone.fg),
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                row.name,
                                size=13.5,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT,
                            ),
                            ft.Text(meta, size=11.5, color=C.DIM),
                        ],
                        spacing=1,
                        tight=True,
                        expand=True,
                    ),
                    kit.chip(
                        row.status_text, row.tone, dot=row.status == STATUS_RUNNING
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _navigate(self, view_key: str) -> None:
        self.close()
        if self._on_navigate is not None:
            self._on_navigate(view_key)
