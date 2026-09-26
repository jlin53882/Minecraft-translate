"""UiBatcher：節流、背壓、最後一批一定送出。"""

import asyncio

from app.ui.ui_batcher import UiBatcher


class _Page:
    """run_task 只排入佇列，模擬 event loop 尚未執行。"""

    def __init__(self):
        self.tasks = []

    def run_task(self, handler, *args):
        self.tasks.append(handler)

    def drain(self):
        n = 0
        while self.tasks:
            asyncio.run(self.tasks.pop(0)())
            n += 1
        return n


def test_backpressure_keeps_single_task_in_flight_and_loses_nothing():
    page = _Page()
    applied = []
    b = UiBatcher(page, lambda lines, state: applied.append((lines, state)), interval=0)

    for i in range(1000):
        b.add_lines([(f"line {i}", "info")])
        b.set_state(progress=i / 1000)
        b.flush()
    # 第一批還在 event loop 佇列中，其餘呼叫只累積資料
    assert len(page.tasks) == 1

    b.set_state(done=True)
    b.flush(force=True)
    assert page.drain() == 2

    lines = [t for batch, _ in applied for t, _ in batch]
    assert lines == [f"line {i}" for i in range(1000)]
    assert applied[-1][1]["done"] is True


def test_throttle_interval():
    page = _Page()
    b = UiBatcher(page, lambda lines, state: None, interval=60)
    b.add_lines([("a", "info")])
    b.flush()
    page.drain()
    b.add_lines([("b", "info")])
    b.flush()
    assert page.tasks == []  # 間隔未到
    b.flush(force=True)
    assert len(page.tasks) == 1


def test_without_run_task_applies_synchronously():
    applied = []
    b = UiBatcher(object(), lambda lines, state: applied.append(lines))
    b.add_lines([("x", "info")])
    b.flush(force=True)
    assert applied == [[("x", "info")]]
