"""圖示預覽的模組 → 項目對照：依需要才載入單一模組的來源項目並補上譯文。

模組清單只需要 modid 與每頁 50 個模組的筆數；數十萬筆項目不必在載入時全部讀進記憶體。
``ModEntryStore`` 對外是唯讀的 ``modid -> [entry]`` Mapping，第一次存取某個模組時才向
來源（記憶體或 L2 磁碟分片）取該模組的項目，並以目前選取的審查資料夾譯文補上 ``zh_tw``。
"""

import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from types import SimpleNamespace
from typing import Protocol

# 同時保留的已補譯文模組數：翻頁與搜尋會碰到的模組數遠小於此，超出的最久未用者釋放。
_HYDRATED_CAPACITY = 512


class EntrySource(Protocol):
    """某個來源（JAR 目錄）所有項目的存取介面，只含來源資料、不含譯文。"""

    identity: dict | None
    modids: Sequence[str]
    entry_count: int

    def load(self, modid: str) -> list[dict]:
        """回傳該模組的來源項目（modid／key／en／source_jar／icon_path）。"""


class MemorySource:
    """已在記憶體中的來源項目（掃描完成後、或解包資料夾模式）。"""

    def __init__(self, by_mod: dict[str, list[dict]], identity: dict | None = None):
        self.identity = identity
        self._by_mod = by_mod
        self.modids = sorted(by_mod)
        self.entry_count = sum(len(rows) for rows in by_mod.values())

    @classmethod
    def from_entries(cls, entries, identity: dict | None = None) -> "MemorySource":
        """把來源項目 dict 依 modid 分組。"""
        by_mod: dict[str, list[dict]] = {}
        for entry in entries:
            by_mod.setdefault(str(entry.get("modid", "")), []).append(entry)
        return cls(by_mod, identity)

    def load(self, modid: str) -> list[dict]:
        return self._by_mod.get(modid, [])


class ModEntryStore(Mapping):
    """``modid -> [SimpleNamespace]`` 的唯讀 Mapping；譯文來自 ``zh_lookup``（key → 譯文）。"""

    def __init__(
        self,
        source: EntrySource,
        zh_lookup: Mapping[str, object] | None = None,
        *,
        capacity: int = _HYDRATED_CAPACITY,
    ):
        self.source = source
        self._zh_lookup = zh_lookup or {}
        self._capacity = capacity
        self._hydrated: OrderedDict[str, list] = OrderedDict()
        self._lock = threading.Lock()
        self._known = frozenset(source.modids)

    @property
    def total_entries(self) -> int:
        return self.source.entry_count

    def with_translations(self, zh_lookup: Mapping[str, object]) -> "ModEntryStore":
        """同一份來源、換一份譯文（審查資料夾改變時）。"""
        return ModEntryStore(self.source, zh_lookup, capacity=self._capacity)

    def warm(self, modids) -> None:
        """預先載入指定模組（可在背景執行緒呼叫），讓第一次顯示不必讀檔。"""
        for modid in modids:
            self.get(modid)

    def __getitem__(self, modid: str) -> list:
        with self._lock:
            cached = self._hydrated.get(modid)
            if cached is not None:
                self._hydrated.move_to_end(modid)
                return cached
        if modid not in self._known:
            raise KeyError(modid)
        entries = self._hydrate(modid)
        with self._lock:
            self._hydrated[modid] = entries
            while len(self._hydrated) > self._capacity:
                self._hydrated.popitem(last=False)
        return entries

    def _hydrate(self, modid: str) -> list:
        lookup = self._zh_lookup
        entries = []
        for row in self.source.load(modid):
            zh_tw = lookup.get(row.get("key"), "")
            if not isinstance(zh_tw, str):
                zh_tw = ""
            entries.append(SimpleNamespace(**row, zh_tw=zh_tw.strip()))
        return entries

    def __iter__(self) -> Iterator[str]:
        return iter(self.source.modids)

    def __len__(self) -> int:
        return len(self.source.modids)

    def __contains__(self, modid: object) -> bool:
        return modid in self._known
