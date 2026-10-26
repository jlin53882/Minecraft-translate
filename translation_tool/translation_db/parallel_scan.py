"""全版本逐列比對（搜尋的 ``LIKE '%字%'``）的平行掃描。

這類條件無法用索引，只能逐列比對；28 萬筆約 190 ms，而且幾乎全是 CPU 時間
（SQLite 在查詢期間釋放 GIL）。所以把 ``entry.id`` 切成連續區間，每個區間用自己的
唯讀連線同時掃，結果合併；區間內仍沿 ``idx_entry_version`` 的 rowid 範圍順序讀取。

- 不建立任何衍生資料，不影響寫入。
- 取消沿用 ``translation_tool.utils.cancellation``：區間查詢用 progress handler 檢查
  ``is_cancelled()``，任務被較新的請求取代時各區間幾毫秒內就會停下。
- 區間太小（資料量不足以抵消開連線與切換執行緒的成本）或只有一顆 CPU 時不平行。
"""

from __future__ import annotations

import contextvars
import os
import sqlite3
import threading
from collections.abc import Sequence
from concurrent.futures import Future
from pathlib import Path

from translation_tool.utils.cancellation import is_cancelled
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor

MAX_WORKERS = 4
MIN_SPAN_PER_WORKER = 60_000  # 每個區間至少涵蓋的編號數（約 60 ms 以上的掃描量）
_PROGRESS_STEP = 20_000  # 每執行這麼多 VM 指令檢查一次取消

_pool_lock = threading.Lock()
_pool: ContextThreadPoolExecutor | None = None


def _executor() -> ContextThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ContextThreadPoolExecutor(
                max_workers=MAX_WORKERS, thread_name_prefix="moddb-scan"
            )
        return _pool


def worker_count(span: int) -> int:
    """編號範圍 ``span`` 值得用幾條連線同時掃（1 表示不平行）。

    留一顆 CPU 給畫面與事件迴圈：4 核時用 3 條連線（95 ms，4 條 79 ms），掃描期間輸入與繪製不被擠掉。
    """
    cpus = os.cpu_count() or 1
    return max(1, min(MAX_WORKERS, cpus - 1, span // MIN_SPAN_PER_WORKER))


def split_ranges(low: int, high: int, parts: int) -> list[tuple[int, int]]:
    """把 ``[low, high]`` 切成 ``parts`` 個連續、不重疊的區間（含兩端）。"""
    span = high - low + 1
    if parts <= 1 or span <= 1:
        return [(low, high)]
    step = -(-span // parts)
    ranges = []
    start = low
    while start <= high:
        ranges.append((start, min(high, start + step - 1)))
        start += step
    return ranges


def _open_reader(path: Path) -> sqlite3.Connection:
    """區間掃描專用的唯讀連線。

    刻意不沿用一般連線的 ``temp_store = MEMORY`` 與大型頁面快取：多條連線同時用記憶體
    暫存區做 ``GROUP BY`` 時會爭用 SQLite 的全域配置鎖，符合筆數很多的搜尋（例如單一字母）
    反而比單條連線更慢。
    """
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
    )
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _scan_range(
    path: Path, sql: str, params: Sequence, low: int, high: int
) -> list[tuple]:
    conn = _open_reader(path)
    try:
        conn.set_progress_handler(lambda: int(is_cancelled()), _PROGRESS_STEP)
        return conn.execute(sql, (*params, low, high)).fetchall()
    finally:
        conn.close()


def scan(
    path: Path, sql: str, params: Sequence, ranges: Sequence[tuple[int, int]]
) -> list[tuple]:
    """各區間跑同一個 ``sql``（結尾多兩個 ``?``：編號下限與上限），回傳合併後的所有列。

    任一區間失敗（包含被取消）就丟出該例外，其餘區間不再等待結果。
    """
    pool = _executor()
    futures: list[Future] = []
    for low, high in ranges:
        # 帶著呼叫端的 context，讓取消檢查看得到同一個任務
        context = contextvars.copy_context()
        futures.append(
            pool.submit(context.run, _scan_range, path, sql, params, low, high)
        )
    rows: list[tuple] = []
    try:
        for future in futures:
            rows.extend(future.result())
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    return rows
