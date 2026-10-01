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

from collections.abc import Sequence
from typing import Literal

import flet as ft

from app.ui import design, theme

from .log_entry import LogEntry
from .log_presenter import LogPresenter
from .task_session import TaskSession

_LEVEL_COLORS = {
    "error": theme.TEXT_LOG_ERROR,
    "warning": theme.TEXT_LOG_WARNING,
    "info": theme.TEXT_LOG_INFO,
    "system": theme.TEXT_LOG_SYSTEM,
    "debug": theme.TEXT_LOG_DEBUG,
}


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
    DEFAULT_RADIUS = 12
    DEFAULT_PADDING = 12
    DEFAULT_SPACING = 4
    DEFAULT_FONT = design.FONT_MONO
    DEFAULT_TEXT_SIZE = 12

    def __init__(
        self,
        page: ft.Page,
        *,
        mode: Literal["append", "tail"] = "append",
        max_lines: int = 2000,
        tail_lines: int = 250,
        show_levels: list[str] | None = None,
        height: int | None = None,
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

        # append 模式允許暫時超出 max_lines 的緩衝量；超過時一次換成新的 ListView。
        # 原因：Flet 的 keyed list diff 每刪除開頭一筆就重排整個清單（O(刪除數×行數)），
        # 2000 行的 log 每次刷新都從開頭截斷時，單次 diff 可達 0.3～0.8 秒並卡住 UI。
        self._trim_slack = max_lines // 4

        # 內部 ListView
        self._list_view = self._new_list_view()

        # 內建 LogPresenter（保留供 sync_from_session / sync_entries 使用，
        # add() 不走 presenter 因為 append 模式用 seq dedup，手動 add 用 seq=0 會被吃掉）
        self._presenter = LogPresenter(
            mode=mode,
            # 截斷交給 LogView._compact()；presenter 的上限只作為保險
            max_ui_lines=max_lines + 2 * self._trim_slack,
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
        update: bool = True,
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
            update: 是否立即刷新畫面；批次新增時傳 False，最後再呼叫 refresh()
        """
        if not text:
            return

        # 等級過濾（不在白名單就跳過）
        if level not in self.show_levels:
            return

        self._list_view.controls.append(self._make_text(text, level))
        self._compact()

        if update:
            self.refresh()

    def add_many(self, items: Sequence[tuple[str, str]]) -> None:
        """批次新增多行 log，只刷新一次畫面。

        只保留批次中最後 max_lines（tail 模式為 tail_lines）筆，並一次截斷，
        避免大量 log 時逐行建立控制項與逐行搬移清單。

        Args:
            items: (text, level) 序列
        """
        limit = self._line_limit()
        kept = [(t, lv) for t, lv in items if t and lv in self.show_levels][-limit:]
        if not kept:
            return
        self._list_view.controls.extend(self._make_text(t, lv) for t, lv in kept)
        self._compact()
        self.refresh()

    def _new_list_view(self, controls: list | None = None) -> ft.ListView:
        return ft.ListView(
            controls=controls or [],
            expand=True,
            spacing=self.DEFAULT_SPACING,
            auto_scroll=True,
        )

    def _compact(self) -> None:
        """行數超過上限時截斷。

        tail 模式行數少，直接刪除開頭。append 模式在超過 max_lines + 緩衝量時，
        把最後 max_lines 行搬到新的 ListView（diff 成本為線性），
        避免每次刷新都從開頭刪除造成的高成本 diff。
        """
        controls = self._list_view.controls
        if self.mode == "tail":
            if len(controls) > self.tail_lines:
                del controls[: len(controls) - self.tail_lines]
            return
        if len(controls) > self.max_lines + self._trim_slack:
            self._list_view = self._new_list_view(controls[-self.max_lines :])
            self.content = self._list_view

    def _line_limit(self) -> int:
        return self.tail_lines if self.mode == "tail" else self.max_lines

    def _make_text(self, text: str, level: str) -> ft.Text:
        """依等級建立 log 行（顏色取自 theme token）。"""
        return ft.Text(
            text,
            size=self.DEFAULT_TEXT_SIZE,
            color=_LEVEL_COLORS.get(level, theme.TEXT_LOG_DEFAULT),
            font_family=self.DEFAULT_FONT,
        )

    def refresh(self) -> None:
        """只刷新本元件（而非整頁 diff）；尚未掛上頁面時退回整頁更新。"""
        try:
            self.update()
        except (AssertionError, RuntimeError):
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

    def sync_from_session(self, session: TaskSession) -> list[LogEntry]:
        """從 TaskSession 同步 log（給 poller 用）。

        走 LogPresenter.sync，會處理 dedup 與顏色。

        Returns:
            新增的 entries list
        """
        snapshot = session.snapshot()
        new_entries = self._presenter.sync(self._list_view, snapshot["logs"])
        self._compact()
        if self._page:
            self._page.update()
        return new_entries

    def sync_entries(
        self, entries: Sequence[LogEntry], update: bool = True
    ) -> list[LogEntry]:
        """從 logs list 同步（給沒用 TaskSession 的 caller，如 bundler_view）。

        走 LogPresenter.sync，會處理 dedup 與顏色。

        Args:
            entries: LogEntry list
            update: 有新 entries 時是否立即刷新（caller 之後會自行 page.update 時傳 False）

        Returns:
            新增的 entries list
        """
        new_entries = self._presenter.sync(self._list_view, entries)
        self._compact()
        if new_entries and update:
            self.refresh()
        return new_entries

    # ──── 設定變更（給 settings 頁用）────────────────────────────

    def set_show_levels(self, levels: list[str]) -> None:
        """更新要顯示的等級白名單。"""
        self.show_levels = levels
        self._presenter.show_levels = levels
