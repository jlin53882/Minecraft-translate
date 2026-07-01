"""app/views/_log/log_view.py

統一的 log 顯示 widget。

設計目標：
- 一個 widget = 一個好看的 log 區塊（深色 Container + 等寬字 ListView）
- 內建 LogPresenter，自動處理限流 + 顏色 + 防止 UI 凍住
- 不重新發明視覺：使用 theme 既有 token + 既有 deep-dark 風格

用法：
    self.log_view = LogView(page=self.page)

    # 從 TaskSession 同步（給 poller 用）
    self.log_view.sync_from_session(self.session)

    # 或手動新增（給 reset 動作、純事件 log 用）
    self.log_view.add("已重置", level="info")
    self.log_view.add_error("找不到檔案")
    self.log_view.clear()
"""

from __future__ import annotations

from typing import List, Literal, Optional, Sequence

import flet as ft

from app.ui import theme

from .log_entry import LogEntry
from .log_presenter import LogPresenter
from .task_session import TaskSession


class LogView(ft.Container):
    """統一的 log 顯示 widget。

    取代散落在各 view 的：
        ft.Container(
            bgcolor="#1e1e1e",
            border=ft.Border.all(1, "#4b5563"),
            border_radius=8,
            padding=10,
            content=ft.ListView(expand=True, spacing=4, auto_scroll=True),
        )

    Attributes:
        page: Flet Page（用於 page.update）
        mode: "append"（新增式）或 "tail"（最後 N 筆整批重建）
        max_lines: append 模式最大保留行數
        tail_lines: tail 模式每次取的筆數
        show_levels: 要顯示的等級白名單
    """

    # 視覺常數（從 theme 來，集中在這裡）
    DEFAULT_RADIUS = 8
    DEFAULT_PADDING = 10
    DEFAULT_SPACING = 4
    DEFAULT_FONT = "Consolas,Monospace"
    DEFAULT_TEXT_SIZE = 12

    def __init__(
        self,
        page: ft.Page,
        *,
        mode: Literal["append", "tail"] = "append",
        max_lines: int = 2000,
        tail_lines: int = 250,
        show_levels: Optional[List[str]] = None,
        height: Optional[int] = None,
        expand: bool = True,
    ):
        # ⚠️ 必須先設定所有 attribute，最後才呼叫 super().__init__()
        # 原因：Flet 0.85+ 在 super().__init__() 過程中可能觸發 callback（如 _notify），
        # 若 attribute 還沒設好會炸。Codebase 慣例見 cache_view.py:79-82。
        # ⚠️ 注意：Flet Container 有內建 `page` property（無 setter），所以這裡用 `self._page`。
        self._page = page
        self.mode = mode
        self.max_lines = max_lines
        self.tail_lines = tail_lines
        self.show_levels = show_levels or ["system", "info", "warning", "error"]

        # 內部 ListView
        self._list_view = ft.ListView(
            expand=True,
            spacing=self.DEFAULT_SPACING,
            auto_scroll=True,
        )

        # 內建 LogPresenter（保留供 sync_from_session / sync_entries 使用，
        # add() 不走 presenter 因為 append 模式用 seq dedup，手動 add 用 seq=0 會被吃掉）
        self._presenter = LogPresenter(
            mode=mode,
            max_ui_lines=max_lines,
            tail_lines=tail_lines,
            show_levels=self.show_levels,
            colorize=True,
            text_size=self.DEFAULT_TEXT_SIZE,
            default_color=theme.TEXT_LOG_DEFAULT,
        )

        # 最後才 super().__init__()
        super().__init__(
            expand=expand,
            height=height,
            bgcolor=theme.BG_LOG_PANEL,
            border=ft.Border.all(1, theme.BORDER_LOG_PANEL),
            border_radius=self.DEFAULT_RADIUS,
            padding=self.DEFAULT_PADDING,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=self._list_view,
        )

    # ──── 對外 API ────────────────────────────────────────────────

    def add(
        self,
        text: str,
        level: str = "info",
        source: str = "ui",
    ) -> None:
        """新增一行 log（給 reset 動作、純事件用）。

        重要：此方法**不走 LogPresenter**，因為 LogPresenter 的 append 模式用
        `e.seq > _last_seq` 做 dedup。若每次都用 seq=0，第二筆以後會被吃掉。
        這裡直接操作 _list_view.controls.append()，自己處理等級過濾與截斷。

        過濾行為：若 level 不在 self.show_levels 白名單中，會被靜默跳過。

        Args:
            text: log 文字
            level: 等級（debug/info/warning/error/system）
            source: 來源標記
        """
        if not text:
            return

        # 等級過濾（不在白名單就跳過）
        if level not in self.show_levels:
            return

        # 取對應等級的顏色（從 theme token）
        color = theme.TEXT_LOG_DEFAULT
        if level == "error":
            color = theme.TEXT_LOG_ERROR
        elif level == "warning":
            color = theme.TEXT_LOG_WARNING
        elif level == "info":
            color = theme.TEXT_LOG_INFO
        elif level == "system":
            color = theme.TEXT_LOG_SYSTEM
        elif level == "debug":
            color = theme.TEXT_LOG_DEBUG

        if self.mode == "tail":
            # tail 模式：保留最後 tail_lines 筆
            self._list_view.controls.append(
                ft.Text(
                    text,
                    size=self.DEFAULT_TEXT_SIZE,
                    color=color,
                    font_family=self.DEFAULT_FONT,
                )
            )
            if len(self._list_view.controls) > self.tail_lines:
                overflow = len(self._list_view.controls) - self.tail_lines
                del self._list_view.controls[:overflow]
        else:
            # append 模式：超過 max_lines 時自動截斷
            self._list_view.controls.append(
                ft.Text(
                    text,
                    size=self.DEFAULT_TEXT_SIZE,
                    color=color,
                    font_family=self.DEFAULT_FONT,
                )
            )
            if len(self._list_view.controls) > self.max_lines:
                overflow = len(self._list_view.controls) - self.max_lines
                del self._list_view.controls[:overflow]

        if self._page:
            self._page.update()

    def add_error(self, text: str) -> None:
        """快速新增 error 等級 log。"""
        self.add(text, level="error")

    def add_warning(self, text: str) -> None:
        self.add(text, level="warning")

    def add_info(self, text: str) -> None:
        self.add(text, level="info")

    def add_system(self, text: str) -> None:
        """新增 system 等級 log。"""
        self.add(text, level="system")

    def add_debug(self, text: str) -> None:
        """新增 debug 等級 log。"""
        self.add(text, level="debug")

    def clear(self) -> None:
        """清空所有 log。"""
        self._list_view.controls.clear()
        self._presenter.reset()
        if self._page:
            self._page.update()

    def sync_from_session(self, session: TaskSession) -> List[LogEntry]:
        """從 TaskSession 同步 log（給 poller 用）。

        走 LogPresenter.sync，會處理 dedup 與顏色。

        Returns:
            新增的 entries list
        """
        snapshot = session.snapshot()
        new_entries = self._presenter.sync(self._list_view, snapshot["logs"])
        if self._page:
            self._page.update()
        return new_entries

    def sync_entries(self, entries: Sequence[LogEntry]) -> List[LogEntry]:
        """從 logs list 同步（給沒用 TaskSession 的 caller，如 bundler_view）。

        走 LogPresenter.sync，會處理 dedup 與顏色。

        Args:
            entries: LogEntry list

        Returns:
            新增的 entries list
        """
        new_entries = self._presenter.sync(self._list_view, entries)
        if self._page:
            self._page.update()
        return new_entries

    # ──── 設定變更（給 settings 頁用）────────────────────────────

    def set_show_levels(self, levels: List[str]) -> None:
        """更新要顯示的等級白名單。"""
        self.show_levels = levels
        self._presenter.show_levels = levels