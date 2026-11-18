"""資料庫清理視窗共用的進度回報：背景工作的分階段進度轉成排回畫面的更新。

清理、重複鍵合併這類長時間操作在背景執行緒回報 ``PhaseProgress``（第幾／共幾階段、階段名稱、
已處理、總數）；每個階段各自從 0 計到 100%，進度條不會倒退。畫面不能被大量更新淹沒，
所以背景端節流（同一階段內最多每 ``PROGRESS_INTERVAL`` 秒一次；階段開始、階段結束與換階段一律回報），
畫面端再判斷這次回報是否仍屬於目前的操作。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.services_impl.moddb_cleanup_service import PhaseProgress
from app.views.moddb.formatting import format_count

PROGRESS_INTERVAL = 0.1  # 秒；每秒最多約 10 次畫面更新


def throttled_progress(
    emit: Callable[[PhaseProgress], None],
    *,
    interval: float = PROGRESS_INTERVAL,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[PhaseProgress], None]:
    """包成節流後的 ``progress``：階段第一次回報、已達總數、或換了階段一律放行。"""
    last = 0.0
    last_index = -1

    def report(progress: PhaseProgress) -> None:
        nonlocal last, last_index
        now = clock()
        steady = (
            progress.index == last_index
            and 0 < progress.done < progress.total
            and now - last < interval
        )
        if steady:
            return
        last, last_index = now, progress.index
        emit(progress)

    return report


def progress_value(progress: PhaseProgress) -> float:
    """目前階段的進度條值（0～1）；這個階段沒有筆數要處理時視為已完成。"""
    if progress.total <= 0:
        return 1.0
    return min(1.0, progress.done / progress.total)


def progress_text(progress: PhaseProgress) -> str:
    """例如「第 2／3 階段：讀取條目 1,200 / 8,000 筆」；只有一個階段時不加「第 n／N 階段」。"""
    prefix = (
        f"第 {progress.index}／{progress.count} 階段：" if progress.count > 1 else ""
    )
    return (
        f"{prefix}{progress.label} "
        f"{format_count(progress.done)} / {format_count(progress.total)} 筆"
    )
