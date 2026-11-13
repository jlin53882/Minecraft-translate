"""清理類長時間操作的分階段進度回報。

每個操作的階段數與名稱在開始前就固定（建構 :class:`OperationProgress` 時給 ``labels``），
所以「第 n／N 階段」是真的。每個階段各自從 0 計到它的總數：階段開工時用 :meth:`begin`
給編號與筆數、處理中呼叫 :meth:`tick`、結束時 :meth:`complete`；進度只在階段內前進，
不會因為後面才確定的筆數而倒退，也不編造百分比。

階段以編號（從 1 起）指定。某個階段沒有工作可做時（例如掃描結果命中快取）直接不呼叫
``begin``，下一個階段照自己的編號開始：畫面上就不會出現被略過的那一階段，編號不連續但永遠遞增。

回報物件是 :class:`PhaseProgress`，``progress`` 為 None 時所有方法都是空操作。
callback 只能排程回 UI，不可在裡面直接動畫面：寫入交易持有資料庫鎖時也會呼叫它，必須很快返回。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class PhaseProgress:
    """一次進度回報：第 ``index`` 階段（共 ``count`` 階段）``label`` 已處理 ``done`` / ``total`` 筆。"""

    index: int
    count: int
    label: str
    done: int
    total: int


ProgressCallback = Callable[[PhaseProgress], None]

_REPORT_STEP = 256  # 階段內累積多少筆才回報一次；階段開始與結束一律立刻回報


class OperationProgress:
    """分階段進度：階段內 ``done`` 單調遞增、結束時等於 ``total``，階段編號只增不減。"""

    def __init__(
        self, callback: ProgressCallback | None, labels: Sequence[str] = ()
    ) -> None:
        self._callback = callback
        self._labels = tuple(labels)
        self.index = 0
        self.done = 0
        self.total = 0
        self._reported = -1

    @property
    def enabled(self) -> bool:
        return self._callback is not None

    def begin(self, index: int, total: int) -> None:
        """開始第 ``index`` 階段（從 1 起），預計處理 ``total`` 筆；立刻回報 0 / total。"""
        if self._callback is None:
            return
        self.index = index
        self.done = 0
        self.total = max(0, int(total))
        self._emit()

    def tick(self, count: int = 1) -> None:
        """目前階段已處理 ``count`` 筆；累積到門檻才回報。"""
        if self._callback is None or count <= 0 or self.index <= 0:
            return
        self.done = min(self.done + int(count), self.total)
        if self.done >= self.total or self.done - self._reported >= _REPORT_STEP:
            self._emit()

    def complete(self) -> None:
        """目前階段結束：把沒處理到的筆數（資料已消失、被略過）補滿並回報一次。"""
        if self._callback is None or self.index <= 0:
            return
        if self.done != self.total or self._reported != self.done:
            self.done = self.total
            self._emit()

    @contextmanager
    def phase(self, index: int, total: int) -> Iterator[None]:
        """``begin`` ＋ 區塊結束時 ``complete``（區塊裡出例外時不補滿）。"""
        self.begin(index, total)
        yield
        self.complete()

    def finish(self) -> None:
        """整個操作結束：確保最後回報的是「最後一個有開始的階段」已完成。"""
        self.complete()

    def _emit(self) -> None:
        self._reported = self.done
        label = (
            self._labels[self.index - 1] if 0 < self.index <= len(self._labels) else ""
        )
        if self._callback is not None:
            self._callback(
                PhaseProgress(
                    self.index, len(self._labels), label, self.done, self.total
                )
            )
