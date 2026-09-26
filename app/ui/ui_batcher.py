"""app/ui/ui_batcher.py 模組。

用途：背景執行緒 → UI 的批次更新（節流 + 背壓）。

背景任務只呼叫 add_lines() / set_state() / flush()；實際修改控制項與
page.update() 一律在 Flet event loop 上執行（page.run_task）。

- 節流：兩次刷新至少間隔 interval 秒。
- 背壓：上一次刷新還在 event loop 上執行時不再排新的刷新，資料持續累積，
  避免大量 log 時 run_task 佇列堆積、UI 延遲數十秒。
- 自適應：下一次刷新的間隔至少是上一次套用耗時的兩倍（大量 log 時 diff 較慢），
  讓 event loop 保留一半以上的時間處理使用者操作。
- force=True（任務結束時）保證最後一批一定會送出。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

ApplyFn = Callable[[list[tuple[str, str]], dict[str, Any]], None]


class UiBatcher:
    """累積背景任務的 log 與狀態，節流後交給 event loop 套用。"""

    def __init__(self, page: Any, apply: ApplyFn, interval: float = 0.2):
        """初始化。

        Args:
            page: ft.Page（需有 run_task；測試用的假 page 沒有時直接同步套用）
            apply: 在 event loop 上執行的套用函式 apply(lines, state)
            interval: 兩次刷新的最小間隔（秒）
        """
        self._page = page
        self._apply = apply
        self.interval = interval
        self._lock = threading.Lock()
        self._lines: list[tuple[str, str]] = []
        self._state: dict[str, Any] = {}
        self._in_flight = False
        self._rerun = False
        self._last = 0.0
        self._cost = 0.0

    def add_lines(self, items: list[tuple[str, str]]) -> None:
        """加入 (text, level) log 行。"""
        with self._lock:
            self._lines.extend(items)

    def set_state(self, **values: Any) -> None:
        """更新要套用的最新狀態（同名 key 只保留最後一次）。"""
        with self._lock:
            self._state.update(values)

    def flush(self, force: bool = False) -> None:
        """依節流 / 背壓規則排程一次刷新；force=True 表示一定要送出。"""
        with self._lock:
            if self._in_flight:
                if force:
                    self._rerun = True
                return
            wait = max(self.interval, 2 * self._cost)
            if not force and time.monotonic() - self._last < wait:
                return
            if not force and not self._lines and not self._state:
                return
            lines, state = self._take()
            self._in_flight = True
        self._schedule(lines, state)

    def _take(self) -> tuple[list[tuple[str, str]], dict[str, Any]]:
        lines, self._lines = self._lines, []
        state, self._state = self._state, {}
        return lines, state

    def _schedule(self, lines, state) -> None:
        run_task = getattr(self._page, "run_task", None)
        if run_task is None:
            self._run(lines, state)
            return

        async def _apply_on_loop():
            self._run(lines, state)

        run_task(_apply_on_loop)

    def _run(self, lines, state) -> None:
        start = time.monotonic()
        try:
            self._apply(lines, state)
        finally:
            with self._lock:
                self._in_flight = False
                self._last = time.monotonic()
                self._cost = self._last - start
                rerun, self._rerun = self._rerun, False
            if rerun:
                self.flush(force=True)
