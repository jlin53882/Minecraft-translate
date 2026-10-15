"""Bounded, generation-scoped JSON results shared by read-only workers."""

from collections import OrderedDict
from threading import RLock
from typing import Any


class ReadCache:
    """Keep immutable serialized snapshots; each consumer decodes its own value."""

    def __init__(self, max_entries: int = 128, max_bytes: int = 16_000_000):
        self._values: OrderedDict[tuple, str] = OrderedDict()
        self._bytes = 0
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._lock = RLock()

    def exceeds_capacity(self, value: Any) -> bool:
        """估計 JSON 序列化後是否超過單筆上限；超過就不必白做序列化。

        走訪巢狀的 list／tuple／dict，字串依實際位元組數、數字與 null 以固定值估算，
        一超過上限就提早結束，成本遠低於完整序列化。估計刻意不低估字串內容。
        """
        budget = self._max_bytes
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                budget -= len(item) + 2 if item.isascii() else len(item.encode()) + 2
            elif isinstance(item, dict):
                budget -= 2 + len(item)
                for key, child in item.items():
                    stack.append(key)
                    stack.append(child)
            elif isinstance(item, (list, tuple)):
                budget -= 2 + len(item)
                stack.extend(item)
            else:
                budget -= 8
            if budget < 0:
                return True
        return False

    def get(self, key: tuple) -> str | None:
        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._values.move_to_end(key)
            return value

    def put(self, key: tuple, value: str) -> None:
        size = len(value.encode("utf-8"))
        if size > self._max_bytes:
            return
        with self._lock:
            previous = self._values.pop(key, None)
            if previous is not None:
                self._bytes -= len(previous.encode("utf-8"))
            self._values[key] = value
            self._bytes += size
            while (
                len(self._values) > self._max_entries or self._bytes > self._max_bytes
            ):
                _, removed = self._values.popitem(last=False)
                self._bytes -= len(removed.encode("utf-8"))


READ_CACHE = ReadCache()
