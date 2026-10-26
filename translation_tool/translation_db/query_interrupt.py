"""背景讀取的查詢中斷：讓另一個執行緒中斷 worker 唯讀連線上正在跑的 SQLite 查詢。"""

from __future__ import annotations

import contextlib
import sqlite3
import threading


class QueryCancelled(Exception):
    """背景讀取在開始前或執行中被較新的請求取代。"""


class QueryInterrupt:
    """讓其他執行緒中斷 worker 唯讀連線上正在跑的查詢。

    搜尋時每個字都可能換條件；舊查詢要掃過整個版本（數十萬筆時數百毫秒），
    讓它跑完只會擋住新的查詢。``cancel()`` 可在任何執行緒呼叫：連線還沒開就讓
    ``raise_if_cancelled()`` 擋下，已經在查就用 SQLite 的 ``interrupt``。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._event = threading.Event()
        self.cancelled = False

    def attach(self, conn: sqlite3.Connection) -> None:
        """登記要被中斷的連線（讀取快照開啟後呼叫）。"""
        with self._lock:
            self._conn = conn

    def detach(self) -> None:
        """連線即將關閉：之後的 ``cancel()`` 只會設旗標。"""
        with self._lock:
            self._conn = None

    def cancel(self) -> None:
        """標記取消，並中斷連線上進行中的查詢。"""
        with self._lock:
            self.cancelled = True
            self._event.set()
            conn = self._conn
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.interrupt()

    def wait(self, seconds: float) -> bool:
        """最多等 ``seconds`` 秒（被取消就提早返回）；回傳是否已取消。"""
        return self._event.wait(seconds)

    def raise_if_cancelled(self) -> None:
        """已被取消就丟 ``QueryCancelled``。"""
        if self.cancelled:
            raise QueryCancelled
