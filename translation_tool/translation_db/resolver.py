"""resolver.py

翻譯流程使用的資料庫查詢與寫回。

查詢順序（由呼叫端決定整體流程：**資料庫 → 快取 → AI**）：

1. 目標版本自己的譯文（原文必須與資料庫的 en_us 完全一致）；
2. 其他版本中原文相同的譯文（可由設定關閉），取版本最接近者。

同一個條目有多個來源時，已用 ``effective`` 依來源優先序選出最佳譯文。
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.translation_db.identity import classify_file
from translation_tool.translation_db.models import WriteBackItem, WriteBackStats
from translation_tool.translation_db.repository import TranslationDB

_VER = re.compile(r"\d+(?:\.\d+)+")


def version_number(label: str) -> int:
    """把版本標籤（取最後一個 ``1.21.1`` 形式的數字）轉成可比較的整數；找不到回傳 0。"""
    found = _VER.findall(label or "")
    if not found:
        return 0
    parts = [int(p) for p in found[-1].split(".")][:3]
    parts += [0] * (3 - len(parts))
    return parts[0] * 1_000_000 + parts[1] * 1_000 + parts[2]


@dataclass(frozen=True)
class Hit:
    zh_tw: str
    source: int
    mc_version: str
    cross: bool  # 來自其他版本


@dataclass
class ResolverStats:
    hit_target: int = 0
    hit_cross: int = 0
    en_mismatch: int = 0  # 資料庫有同鍵值，但原文不同
    miss: int = 0


class TranslationResolver:
    """以 ``(類型, 模組, 鍵值, 原文)`` 查詢目標版本的既有譯文。"""

    def __init__(
        self, db: TranslationDB, version: str, *, cross_version: bool = True
    ) -> None:
        self.db = db
        self.version = version
        self.cross_version = cross_version
        self.stats = ResolverStats()
        self._lock = threading.Lock()
        self._mods: dict[
            str, dict[tuple[str, str], list[tuple[str, str, str, int]]]
        ] = {}
        self._target = version_number(version)

    def _mod_table(self, mod_id: str):
        with self._lock:
            table = self._mods.get(mod_id)
            if table is None:
                table = {}
                for kind, key, en, ver, tw, src in self.db.load_mod(mod_id):
                    table.setdefault((kind, key), []).append((en, ver, tw, src))
                self._mods[mod_id] = table
            return table

    def lookup(self, kind: str, mod_id: str, key: str, en_us: str) -> Hit | None:
        if not en_us:
            return None
        cands = self._mod_table(mod_id).get((kind, key))
        if not cands:
            self.stats.miss += 1
            return None
        same = [c for c in cands if c[0] == en_us]
        if not same:
            self.stats.en_mismatch += 1
            return None
        own = next((c for c in same if c[1] == self.version), None)
        if own is not None:
            self.stats.hit_target += 1
            return Hit(own[2], own[3], own[1], False)
        if not self.cross_version:
            self.stats.miss += 1
            return None
        best = min(same, key=lambda c: abs(version_number(c[1]) - self._target))
        self.stats.hit_cross += 1
        return Hit(best[2], best[3], best[1], True)


def item_identity(item: dict[str, Any], root: str | Path):
    """翻譯項目（``file`` / ``path`` / ``source_text``）→ ``(類型, 模組, 鍵值, 原文)``；不適用回傳 None。"""
    ident = classify_file(item.get("file") or "", root)
    if ident is None or ident.lang != "en_us":
        return None
    en = item.get("source_text") or item.get("text") or ""
    path = str(item.get("path") or "")
    if not en or not path:
        return None
    return ident.kind, ident.mod_id, ident.item_key(path), en


def split_items_by_db(
    resolver: TranslationResolver | None,
    items: list[dict[str, Any]],
    root: str | Path,
    *,
    is_translated=None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把待翻譯項目拆成「資料庫命中」與「未命中」；命中項目的 ``text`` 換成譯文。"""
    if resolver is None:
        return [], items
    hits: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for item in items:
        ident = item_identity(item, root)
        hit = resolver.lookup(*ident) if ident else None
        if hit and hit.zh_tw and (is_translated is None or is_translated(hit.zh_tw)):
            new_item = dict(item)
            new_item["text"] = hit.zh_tw
            hits.append(new_item)
        else:
            rest.append(item)
    return hits, rest


@dataclass
class WriteBackBuffer:
    """收集翻譯結果並寫回資料庫（快取命中與 AI 翻譯皆可加入；只新增、不覆蓋）。"""

    db: TranslationDB
    version: str
    root: str | Path
    fill_other_versions: bool = True
    stats: WriteBackStats = field(default_factory=WriteBackStats)
    _pending: list[WriteBackItem] = field(default_factory=list)

    def add(self, item: dict[str, Any], translated: str | None = None) -> None:
        ident = item_identity(item, self.root)
        text = (translated if translated is not None else item.get("text")) or ""
        if ident is None or not str(text).strip():  # 只用 strip 判斷空白，內容原樣寫入
            return
        kind, mod_id, key, en = ident
        self._pending.append(WriteBackItem(kind, mod_id, key, en, str(text)))

    def add_many(self, items: Iterable[dict[str, Any]]) -> None:
        for item in items:
            self.add(item)

    def flush(self) -> WriteBackStats:
        """把累積的項目寫入資料庫；回傳本次統計（並累計到 ``self.stats``）。"""
        if not self._pending:
            return WriteBackStats()
        batch, self._pending = self._pending, []
        done = self.db.write_back(
            self.version, batch, fill_other_versions=self.fill_other_versions
        )
        self.stats.written += done.written
        self.stats.filled_other += done.filled_other
        self.stats.skipped += done.skipped
        return done
