"""Bounded, generation-scoped JSON results shared by read-only workers."""

from collections import OrderedDict
from threading import RLock


class ReadCache:
    """Keep immutable serialized snapshots; each consumer decodes its own value."""

    def __init__(self, max_entries: int = 128, max_bytes: int = 16_000_000):
        self._values: OrderedDict[tuple, str] = OrderedDict()
        self._bytes = 0
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._lock = RLock()

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
