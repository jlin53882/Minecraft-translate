"""資料庫清理視窗共用的進度回報：背景工作的 ``progress(已處理, 總數)`` 轉成排回畫面的更新。

清理、重複鍵合併這類長時間操作在背景執行緒回報進度；畫面不能被大量更新淹沒，
所以背景端節流（最多每 ``PROGRESS_INTERVAL`` 秒一次、第一次與最後一次一定回報），
畫面端再判斷這次回報是否仍屬於目前的操作。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.views.moddb.formatting import format_count

PROGRESS_INTERVAL = 0.1  # 秒；每秒最多約 10 次畫面更新


def throttled_progress(
    emit: Callable[[int, int], None],
    *,
    interval: float = PROGRESS_INTERVAL,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[int, int], None]:
    """包成節流後的 ``progress(done, total)``：``done`` 為 0 或已達 ``total`` 一律放行。"""
    last = 0.0

    def report(done: int, total: int) -> None:
        nonlocal last
        now = clock()
        if 0 < done < total and now - last < interval:
            return
        last = now
        emit(done, total)

    return report


def progress_value(done: int, total: int) -> float | None:
    """進度條的值；總數未知（0）時回傳 None（不定長度）。"""
    return min(1.0, done / total) if total > 0 else None


def progress_text(label: str, done: int, total: int) -> str:
    """例如「預覽中…已處理 12,000 / 184,000 筆」；總數未知時只顯示標籤。"""
    if total <= 0:
        return f"{label}…"
    return f"{label}…已處理 {format_count(done)} / {format_count(total)} 筆"
