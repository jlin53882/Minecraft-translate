"""清理類長時間操作的進度回報：累計「已處理／預計處理」的筆數交給 ``progress(done, total)``。

每個階段在開工前先用 :meth:`expect` 登記自己要處理的筆數、處理中呼叫 :meth:`tick`，
所以 ``total`` 只有在「下一階段的筆數剛剛確定」時才會增加，``done`` 一定單調遞增，
最後 :meth:`finish` 會把 ``done`` 補到 ``total`` 並回報一次（不編造百分比）。

``progress`` 為 None 時所有方法都是空操作。回報 callback 只能排程回 UI，
不可在裡面直接動畫面：寫入交易持有資料庫鎖時也會呼叫它，所以 callback 必須很快就返回。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

ProgressCallback = Callable[[int, int], None]

_REPORT_STEP = 256  # 累積多少筆才回報一次；首尾與新階段登記一律立刻回報


class OperationProgress:
    """累計多階段筆數的進度；階段筆數不變時整體 ``done / total`` 不會倒退。"""

    def __init__(self, callback: ProgressCallback | None) -> None:
        self._callback = callback
        self.done = 0
        self.total = 0
        self._reported = -1

    @property
    def enabled(self) -> bool:
        return self._callback is not None

    def expect(self, count: int) -> None:
        """登記接下來要處理的筆數（階段開工前呼叫）。"""
        if self._callback is None or count <= 0:
            return
        self.total += int(count)
        self._emit()

    def tick(self, count: int = 1) -> None:
        """已處理 ``count`` 筆；累積到門檻才回報。"""
        if self._callback is None or count <= 0 or self.total <= 0:
            return
        self.done = min(self.done + int(count), self.total)
        if self.done >= self.total or self.done - self._reported >= _REPORT_STEP:
            self._emit()

    @contextmanager
    def phase(self, planned: int) -> Iterator[None]:
        """階段結束時把沒處理到的筆數（例如資料已消失、被略過）補算完，整體進度才對得上。"""
        start = self.done
        try:
            yield
        finally:
            self.tick(max(0, planned - (self.done - start)))

    def finish(self) -> None:
        """把 ``done`` 補到 ``total`` 並回報最後一次。"""
        if self._callback is None:
            return
        self.done = self.total
        self._emit()

    def _emit(self) -> None:
        self._reported = self.done
        if self._callback is not None:
            self._callback(self.done, self.total)
