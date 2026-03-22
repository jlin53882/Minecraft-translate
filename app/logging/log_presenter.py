"""app/logging/log_presenter.py

日誌呈現策略：append 與 tail 兩種模式。

職責：
- 管理 UI ListView controls 與底層 LogEntry 的同步
- 負責 level filtering / 顏色對應
- 防止 UI controls 無限膨脹導致凍住
- 只在有新內容時 scroll
"""

from __future__ import annotations

from typing import List, Literal, Optional, Sequence

import flet as ft

from .log_colors import get_level_color
from .log_entry import LogEntry


class LogPresenter:
    """
    日誌呈現器，支援 append / tail 兩種模式。

    兩種模式的差異：
    - append：每次只渲染新增的 entries（用 seq 追蹤）
    - tail：每次取最後 N 筆，整批替換 controls

    Attributes:
        mode: "append" 或 "tail"
        max_ui_lines: UI ListView 最大保留行數
        tail_lines: tail 模式每次取的筆數
        show_levels: 要顯示的等級白名單
        colorize: 是否套用等級顏色
        _last_seq: append 模式上次渲染到的最大 seq
    """

    def __init__(
        self,
        mode: Literal["append", "tail"] = "append",
        max_ui_lines: int = 300,
        tail_lines: int = 250,
        show_levels: Optional[List[str]] = None,
        colorize: bool = True,
        text_size: int = 13,
        default_color: str = "#FFFFFF",
    ):
        self.mode = mode
        self.max_ui_lines = max_ui_lines
        self.tail_lines = tail_lines
        self.show_levels = show_levels or ["system", "info", "warning", "error"]
        self.colorize = colorize
        self.text_size = text_size
        self.default_color = default_color
        # 初始值 -1：確保首次 sync 時所有 entry（包含 seq=0）都被視為新的
        self._last_seq: int = -1

    # ──── Public API ────────────────────────────────────────────────

    def reset(self) -> None:
        """重置 presenter 狀態（適用於新任務開始）。"""
        # -1 確保 reset 後首次 sync 時，所有 entry 都被視為新的
        self._last_seq = -1

    def sync(
        self,
        list_view: ft.ListView,
        entries: Sequence[LogEntry],
    ) -> List[LogEntry]:
        """
        將 LogEntry 同步到 UI ListView。

        模式行為：
        - append：只渲染 self._last_seq 之後的新 entries
        - tail：只取最後 tail_lines 筆，全量替換 controls

        Args:
            list_view: Flet ListView widget
            entries:  來自 TaskSession.snapshot()["logs"]

        Returns:
            新增的 entries list（供 caller 做 side effect，如 stats 更新）
        """
        filtered = [e for e in entries if e.level in self.show_levels]

        if self.mode == "append":
            return self._sync_append(list_view, filtered)
        else:
            return self._sync_tail(list_view, filtered)

    # ──── Append Mode ────────────────────────────────────────────────

    def _sync_append(
        self,
        list_view: ft.ListView,
        entries: Sequence[LogEntry],
    ) -> List[LogEntry]:
        """Append 模式：只渲染 self._last_seq 之後的新 entries。"""
        # 跳過已處理的
        new_entries = [e for e in entries if e.seq > self._last_seq]
        if not new_entries:
            return []

        for entry in new_entries:
            color = self._entry_color(entry)
            list_view.controls.append(
                ft.Text(entry.text, size=self.text_size, color=color)
            )

        self._last_seq = max(e.seq for e in new_entries)

        # 防止 UI controls 膨脹：超過上限時移除最舊的
        self._truncate(list_view)
        return new_entries

    # ──── Tail Mode ────────────────────────────────────────────────

    def _sync_tail(
        self,
        list_view: ft.ListView,
        entries: Sequence[LogEntry],
    ) -> List[LogEntry]:
        """Tail 模式：全量替換為最後 N 筆。"""
        tail = list(entries[-self.tail_lines:]) if entries else []
        list_view.controls.clear()
        for entry in tail:
            color = self._entry_color(entry)
            list_view.controls.append(
                ft.Text(entry.text, size=self.text_size, color=color)
            )
        return tail

    # ──── Helpers ────────────────────────────────────────────────────

    def _entry_color(self, entry: LogEntry) -> str:
        """根據 entry 等級取得顏色。"""
        if not self.colorize:
            color = self.default_color
            # Colors enum 直接回傳（str() 回成 "Colors.GREY_100"，不能拿來拼接）
            # Flet 接受 Colors enum 或 hex string
            return color
        return "#" + get_level_color(entry.level)

    def _truncate(self, list_view: ft.ListView) -> None:
        """確保 list_view.controls 不超過 max_ui_lines。"""
        if len(list_view.controls) > self.max_ui_lines:
            overflow = len(list_view.controls) - self.max_ui_lines
            del list_view.controls[:overflow]
