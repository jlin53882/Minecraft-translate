"""圖示預覽的模組 → 項目對照：依需要才載入單一模組的來源項目並補上譯文。

模組清單只需要 modid 與每頁 50 個模組的筆數；數十萬筆項目不必在載入時全部讀進記憶體。
``ModEntryStore`` 對外是唯讀的 ``modid -> [entry]`` Mapping，第一次存取某個模組時才向
來源（記憶體或 L2 磁碟分片）取該模組的項目，並以目前選取的審查資料夾譯文補上 ``zh_tw``。
"""

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
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


class ScannedSource:
    """JAR 掃描結果的來源：依 JAR 分塊累積，項目在第一次被載入時才轉成 dict。

    ``add`` 只在掃描執行緒呼叫；給畫面用的是 ``snapshot()``（淺複製，之後的 ``add`` 不影響它）。
    同一模組的項目依 JAR 名稱排序，與完成順序無關。
    """

    def __init__(self, identity: dict | None = None):
        self.identity = identity
        self._chunks: dict[str, list[tuple[str, list]]] = {}
        self.modids: list[str] = []
        self.entry_count = 0

    def add(self, scan, icon_index=None) -> None:
        """加入一個 JAR 的掃描結果；預建索引必須按來源 JAR 提供圖示。"""
        for modid, rows in scan.mods.items():
            jar_index = None
            if icon_index is not None:
                for_jar = getattr(icon_index, "for_jar", None)
                if callable(for_jar):
                    jar_index = for_jar(scan.jar_name)
            if jar_index is not None:
                rows = [(k, en, jar_index.get(k)) for k, en, _icon in rows]
            self._chunks.setdefault(modid, []).append((scan.jar_name, rows))
            self.entry_count += len(rows)

    def snapshot(self) -> "ScannedSource":
        copy = ScannedSource(self.identity)
        copy._chunks = {modid: list(chunks) for modid, chunks in self._chunks.items()}
        copy.modids = sorted(copy._chunks)
        copy.entry_count = self.entry_count
        return copy

    def load(self, modid: str) -> list[dict]:
        entries = []
        for jar_name, rows in sorted(
            self._chunks.get(modid, ()), key=lambda chunk: chunk[0].casefold()
        ):
            for key, en, icon in rows:
                row = {"modid": modid, "key": key, "en": en, "source_jar": jar_name}
                if icon:
                    row["icon_path"] = icon
                entries.append(row)
        return entries


class ModEntryStore(Mapping):
    """``modid -> [SimpleNamespace]`` 的唯讀 Mapping；譯文來自 ``zh_lookup``（key → 譯文）。"""

    def __init__(
        self,
        source: EntrySource,
        zh_lookup: Mapping[str, object] | None = None,
        *,
        capacity: int = _HYDRATED_CAPACITY,
        on_source_error: Callable[[BaseException], None] | None = None,
    ):
        self.source = source
        self._zh_lookup = zh_lookup or {}
        self._capacity = capacity
        self._on_source_error = on_source_error
        self._hydrated: OrderedDict[str, list] = OrderedDict()
        self._lock = threading.Lock()
        self._known = frozenset(source.modids)

    @property
    def total_entries(self) -> int:
        return self.source.entry_count

    def with_translations(self, zh_lookup: Mapping[str, object]) -> "ModEntryStore":
        """同一份來源、換一份譯文（審查資料夾改變時）。"""
        return ModEntryStore(
            self.source,
            zh_lookup,
            capacity=self._capacity,
            on_source_error=self._on_source_error,
        )

    def set_source_error_handler(
        self, handler: Callable[[BaseException], None] | None
    ) -> None:
        """Set the callback used when a deferred source load fails."""
        self._on_source_error = handler

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
        try:
            rows = self.source.load(modid)
        except Exception as exc:
            if self._on_source_error is not None:
                self._on_source_error(exc)
            raise
        for row in rows:
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
