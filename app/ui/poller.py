"""View 擁有的輪詢（poller）生命週期：保存 Future、可重複呼叫的 stop、世代判斷。

用法（View 內）::

    self._poller = PollerHandle()

    def _start_poll(self):
        self._poller.start(self._page, self._poll)

    async def _poll(self, alive):
        while alive():          # stop()／重新 start() 後立刻變 False，不必等 cancel 送達
            self._sync()
            await asyncio.sleep(0.2)

    def will_unmount(self):     # 換頁／關閉：停止輪詢（背景任務本身照常執行）
        self._poller.stop()

``stop()`` 是 idempotent、可在任何執行緒呼叫；``start()`` 會先停止舊的輪詢，所以重複 mount 不會累積。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class PollerHandle:
    """持有一個 ``page.run_task`` 輪詢的 Future 與「世代」。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._generation = 0
        self._active_generation: int | None = None
        self._future: Any = None

    @property
    def running(self) -> bool:
        """目前是否有一個尚未結束（也未被 stop）的輪詢。"""
        with self._lock:
            return self._active_generation == self._generation

    def start(
        self, page: Any, handler: Callable[[Callable[[], bool]], Awaitable[None]]
    ) -> bool:
        """停止舊輪詢並啟動新的；``handler(alive)`` 是 coroutine function。

        回傳是否已排程。``alive()`` 在 ``stop()``／再次 ``start()`` 後回傳 False。
        """
        with self._lock:
            self._generation += 1
            generation = self._generation
            old, self._future = self._future, None
            self._active_generation = generation
        self._cancel(old)

        def alive() -> bool:
            with self._lock:
                return self._active_generation == generation == self._generation

        async def _runner() -> None:
            try:
                await handler(alive)
            finally:
                self._finished(generation)

        try:
            future = page.run_task(_runner)
        except Exception:
            logger.debug("page.run_task 排程輪詢失敗", exc_info=True)
            self._finished(generation)
            return False
        with self._lock:
            if self._active_generation == generation:
                self._future = future
        return True

    def stop(self) -> None:
        """停止輪詢（可重複呼叫；清掉 Future 與 active 狀態）。"""
        with self._lock:
            self._generation += 1
            self._active_generation = None
            future, self._future = self._future, None
            self._idle.notify_all()
        self._cancel(future)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """（背景執行緒）等到沒有執行中的輪詢（結束或被 stop）；逾時回傳 False。

        不得在 event loop 上呼叫（會阻塞）。沒有輪詢在跑時立刻回傳 True。
        """
        with self._idle:
            return self._idle.wait_for(
                lambda: self._active_generation is None, timeout=timeout
            )

    def _finished(self, generation: int) -> None:
        with self._lock:
            if self._active_generation == generation:
                self._active_generation = None
                self._future = None
            self._idle.notify_all()

    @staticmethod
    def _cancel(future: Any) -> None:
        if future is None:
            return
        try:
            future.cancel()
        except Exception:
            logger.debug("取消輪詢 Future 失敗", exc_info=True)
