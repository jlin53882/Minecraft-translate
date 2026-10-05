"""app/shell/topbar.py：頂列（麵包屑、任務膠囊、API Key 狀態、通知）。

資料都由外殼推進來（``set_task`` / ``set_keys`` / ``set_recent``），本模組不讀設定檔、
不查 API，所以能直接單元測試。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import flet as ft

from app.services_impl.key_health_service import STATUS_COOLING, KeyHealth
from app.shell.task_manager import STATUS_ERROR, TaskInfo
from app.ui import design, kit
from app.ui.design import C
from app.ui.design import tone as get_tone

TOPBAR_HEIGHT = 56


@dataclass(frozen=True)
class KeySummary:
    """API Key 健康度摘要（供狀態膠囊顯示）。"""

    total: int
    usable: int
    cooling: int
    tone: str
    text: str
    tooltip: str


def summarize_keys(snapshot: Sequence[KeyHealth]) -> KeySummary:
    """把每把 key 的狀態整理成一行摘要。

    冷卻中的 key 不會被領取（#113），所以可用數 = 總數 - 冷卻中。
    冷卻已到期、等待再試的 key（probing）算可用。
    """
    total = len(snapshot)
    cooling = [k for k in snapshot if k.status == STATUS_COOLING]
    usable = total - len(cooling)
    if total == 0:
        return KeySummary(0, 0, 0, "neutral", "未設定 Key", "尚未設定 Gemini API Key")
    if usable == 0:
        tone, tooltip = "red", "所有 Key 都已用盡或無權限，等冷卻結束或到設定更換"
    elif cooling:
        tone = "gold"
        tooltip = f"{len(cooling)} 把 Key 冷卻中（每日額度用盡或無權限），其餘照常輪替"
    else:
        tone, tooltip = "em", "所有 Key 狀態正常"
    return KeySummary(
        total, usable, len(cooling), tone, f"{usable}/{total} Key", tooltip
    )


class TaskPill(ft.Container):
    """任務膠囊：有任務時顯示「名稱 · 進度條 · 百分比」，沒有任務時整個隱藏。"""

    def __init__(self, on_click: Callable[[str | None], None] | None = None) -> None:
        self._on_pill_click = on_click
        self.view_key: str | None = None
        self.spinner = ft.ProgressRing(
            width=14, height=14, stroke_width=2, color=C.GOLD
        )
        self.name = ft.Text(
            "", size=12.5, weight=ft.FontWeight.W_600, color=C.GOLD, no_wrap=True
        )
        self.bar = kit.progress_bar(0.0, "gold", height=4)
        self.bar.width = 90
        self.percent = ft.Text(
            "", size=12, color=C.GOLD, font_family=design.FONT_MONO, no_wrap=True
        )
        super().__init__(
            content=ft.Row(
                [self.spinner, self.name, self.bar, self.percent],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            height=34,
            padding=ft.Padding.symmetric(horizontal=14),
            border_radius=17,
            bgcolor=get_tone("gold").bg,
            border=ft.Border.all(1, get_tone("gold").line),
            ink=True,
            on_click=self._click,
            visible=False,
            tooltip="執行中的任務（點擊回到該頁）",
        )

    def _click(self, _e=None) -> None:
        if self._on_pill_click is not None:
            self._on_pill_click(self.view_key)

    def set_task(self, task: TaskInfo | None) -> None:
        """``None`` 隱藏膠囊。"""
        if task is None:
            self.visible = False
            self.view_key = None
            return
        tone = get_tone("red" if task.status == STATUS_ERROR else "gold")
        self.visible = True
        self.view_key = task.view_key
        self.name.value = task.name
        self.name.color = tone.fg
        self.percent.value = f"{task.percent}%"
        self.percent.color = tone.fg
        self.bar.value = task.progress if task.progress > 0 else None
        self.bar.color = tone.fg
        self.spinner.color = tone.fg
        self.bgcolor = tone.bg
        self.border = ft.Border.all(1, tone.line)


class ApiStatusPill(ft.Container):
    """API 狀態：供應商名稱 + 圓點 + 可用 Key 數。點擊前往設定。"""

    def __init__(
        self, on_click: Callable[[], None] | None = None, provider: str = "Gemini"
    ) -> None:
        self._on_pill_click = on_click
        self.provider = provider
        self.summary = summarize_keys([])
        self.dot = ft.Container(width=8, height=8, border_radius=4)
        self.provider_text = ft.Text(provider, size=12.5, color=C.TEXT)
        self.count_text = ft.Text("", size=12, color=C.MUTED)
        super().__init__(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.WIFI, size=15, color=C.MUTED),
                    self.provider_text,
                    self.dot,
                    self.count_text,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            height=34,
            padding=ft.Padding.symmetric(horizontal=14),
            border_radius=17,
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            ink=True,
            on_click=lambda _e: self._on_pill_click() if self._on_pill_click else None,
        )
        self.set_keys([])

    def set_keys(self, snapshot: Sequence[KeyHealth]) -> None:
        self.summary = summarize_keys(snapshot)
        tone = get_tone(self.summary.tone)
        self.dot.bgcolor = tone.fg
        self.count_text.value = self.summary.text
        self.count_text.color = tone.fg if self.summary.tone != "em" else C.MUTED
        self.tooltip = self.summary.tooltip


class TopBar(ft.Container):
    """頂列。``set_breadcrumb`` / ``set_task`` / ``set_keys`` / ``set_recent`` 由外殼呼叫。"""

    def __init__(
        self,
        *,
        on_task_click: Callable[[str | None], None] | None = None,
        on_api_click: Callable[[], None] | None = None,
        on_recent_click: Callable[[str | None], None] | None = None,
    ) -> None:
        self._on_recent_click = on_recent_click
        self.group_text = ft.Text("", size=13, color=C.DIM)
        self.page_text = ft.Text(
            "", size=13.5, weight=ft.FontWeight.W_600, color=C.TEXT
        )
        self.separator = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16, color=C.DIM)
        self.task_pill = TaskPill(on_task_click)
        self.api_pill = ApiStatusPill(on_api_click)
        self.bell_dot = ft.Container(
            width=8,
            height=8,
            border_radius=4,
            bgcolor=C.RED,
            visible=False,
            right=9,
            top=9,
        )
        self.bell_menu = ft.PopupMenuButton(
            icon=ft.Icons.NOTIFICATIONS_NONE,
            icon_color=C.MUTED,
            tooltip="最近的任務",
            items=[ft.PopupMenuItem(content=ft.Text("目前沒有通知"), disabled=True)],
        )
        super().__init__(
            height=TOPBAR_HEIGHT,
            padding=ft.Padding.symmetric(horizontal=24),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
            content=ft.Row(
                [
                    ft.Row(
                        [self.group_text, self.separator, self.page_text],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        expand=True,
                    ),
                    self.task_pill,
                    self.api_pill,
                    ft.Stack(
                        [self.bell_menu, self.bell_dot],
                        width=40,
                        height=40,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # -- 由外殼推進的資料 -------------------------------------------------------

    def set_breadcrumb(self, group_label: str | None, page_label: str) -> None:
        self.group_text.value = group_label or ""
        self.separator.visible = bool(group_label)
        self.group_text.visible = bool(group_label)
        self.page_text.value = page_label

    def set_task(self, task: TaskInfo | None) -> None:
        self.task_pill.set_task(task)

    def set_keys(self, snapshot: Sequence[KeyHealth]) -> None:
        self.api_pill.set_keys(snapshot)

    def set_recent(self, tasks: Sequence[TaskInfo], *, unread: bool = False) -> None:
        """通知清單：最近結束的任務（失敗的標紅）。"""
        if not tasks:
            self.bell_menu.items = [
                ft.PopupMenuItem(content=ft.Text("目前沒有通知"), disabled=True)
            ]
        else:
            self.bell_menu.items = [self._recent_item(t) for t in tasks]
        self.bell_dot.visible = unread

    def _recent_item(self, task: TaskInfo) -> ft.PopupMenuItem:
        failed = task.status == STATUS_ERROR
        icon = ft.Icons.ERROR_OUTLINE if failed else ft.Icons.CHECK_CIRCLE_OUTLINE
        tone = get_tone("red" if failed else "em")
        text = f"{task.name}　{'失敗' if failed else '完成'}"
        return ft.PopupMenuItem(
            content=ft.Row(
                [ft.Icon(icon, size=16, color=tone.fg), ft.Text(text, size=13)],
                spacing=8,
            ),
            on_click=lambda _e, key=task.view_key: (
                self._on_recent_click(key) if self._on_recent_click else None
            ),
        )
