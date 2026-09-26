"""app/ui/debounce.py 模組。

用途：在 Flet event loop 上做 debounce（取代 threading.Timer）。

threading.Timer 的 callback 在背景執行緒執行，直接修改控制項並呼叫
page.update() 會與 event loop 的 diff 競爭（Flet 1.0 非執行緒安全）。
Debouncer 改用 page.run_task + asyncio.sleep，callback 一律在 event loop 上執行。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class Debouncer:
    """延遲執行最後一次呼叫；期間內的新呼叫會取代舊呼叫。"""

    def __init__(self, get_page: Callable[[], Any], delay: float):
        """初始化。

        Args:
            get_page: 回傳目前 ft.Page 的函式（控制項掛載前 page 可能為 None）
            delay: 延遲秒數
        """
        self._get_page = get_page
        self.delay = delay
        self._seq = 0

    def cancel(self) -> None:
        """取消尚未執行的呼叫。"""
        self._seq += 1

    def call(self, fn: Callable[..., Any], *args: Any) -> None:
        """排程在 delay 秒後於 event loop 上執行 fn(*args)。"""
        self._seq += 1
        seq = self._seq
        try:
            page = self._get_page()
        except (AssertionError, RuntimeError):
            page = None  # 控制項尚未掛上頁面
        if page is None:
            return

        async def _run():
            await asyncio.sleep(self.delay)
            if seq == self._seq:
                fn(*args)

        page.run_task(_run)
