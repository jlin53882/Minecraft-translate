"""translation_tool/translation_db/scan_ingest.py：掃描結果寫入資料庫時的分類規則。

一個掃描項目是「新增」「補入新來源譯文」「已存在，略過」「原文已變動」還是「補上原文」，
由 ``ingest_groups`` 決定；實際寫入（``DbIngestTarget``）與預覽（``PreviewIngestTarget``）
共用同一套規則，預覽的數字才會和開始掃描後的結果一致。

預覽不寫入資料庫：它把「這次掃描中已新增的條目與譯文」記在記憶體裡，後面的 jar 才會像實際
寫入時一樣看到前面 jar 新增的內容（例如同一個模組分散在多個 jar）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from translation_tool.translation_db.models import IngestStats, ScanItem
from translation_tool.translation_db.schema import SRC_JAR_CN, SRC_JAR_TW, SRC_MANUAL

# (kind, mod_id) → 該模組既有條目 {key: (entry_id, en_us)} 與既有譯文 {(entry_id, source)}
Known = dict[str, tuple[int, str]]
Have = set[tuple[int, int]]
# 執行一個查詢並回傳所有列：``(sql, params) -> rows``
Query = Callable[[str, tuple], Iterable[tuple[Any, ...]]]


class IngestTarget(Protocol):
    """掃描項目要寫到哪裡：資料庫，或只計數的預覽。"""

    def existing(self, kind: str, mod_id: str) -> tuple[Known, Have]: ...

    def add_entry(self, kind: str, mod_id: str, key: str, en_us: str) -> int: ...

    def record_src_change(
        self, kind: str, mod_id: str, key: str, old_en: str, new_en: str
    ) -> None: ...

    def adopt_en(self, entry_id: int, en_us: str) -> None: ...

    def add_translation(
        self, entry_id: int, source: int, zh_tw: str, zh_cn: str
    ) -> None: ...


def group_scan_items(
    items: Iterable[ScanItem],
) -> dict[tuple[str, str], list[ScanItem]]:
    """依 (類型, 模組) 分組：每組只查一次既有條目。"""
    groups: dict[tuple[str, str], list[ScanItem]] = {}
    for item in items:
        groups.setdefault((item.kind, item.mod_id), []).append(item)
    return groups


def scan_translations(
    item: ScanItem, convert: Callable[[str], str] | None
) -> list[tuple[int, str, str]]:
    """掃描項目要寫入的譯文：``(來源, 繁中, 簡中)``；繁中優先，只有簡中時轉繁。"""
    if item.zh_tw.strip():
        src_tw = SRC_JAR_TW if item.source is None else item.source
        return [(src_tw, item.zh_tw, item.zh_cn)]
    if item.zh_cn.strip() and convert is not None:
        converted = convert(item.zh_cn)
        # 空白判斷用 strip，但存原樣（前後空白在遊戲內可能有意義）
        if converted.strip():
            # 翻譯 ZIP 指定了來源（提供者）時，轉繁後仍歸在該來源
            src_cn = SRC_JAR_CN if item.source is None else item.source
            return [(src_cn, converted, item.zh_cn)]
    return []


def ingest_groups(
    target: IngestTarget,
    groups: dict[tuple[str, str], list[ScanItem]],
    convert: Callable[[str], str] | None,
) -> IngestStats:
    """只新增、不覆蓋；原文已變動者只記錄。回傳這一份掃描結果的統計。"""
    stats = IngestStats()
    for (kind, mod_id), group in groups.items():
        known, have = target.existing(kind, mod_id)
        for item in group:
            cur = known.get(item.key)
            is_new = cur is None
            if cur is None:
                eid = target.add_entry(kind, mod_id, item.key, item.en_us)
                known[item.key] = (eid, item.en_us)
                stats.new_entries += 1
            elif cur[1] != item.en_us and item.en_us and cur[1]:
                target.record_src_change(kind, mod_id, item.key, cur[1], item.en_us)
                stats.en_changed += 1
                continue
            else:
                eid = cur[0]
                if cur[1] != item.en_us and item.en_us:
                    # 原文原本未知（例如先匯入了只有 zh_tw 的翻譯 ZIP）：補上
                    target.adopt_en(eid, item.en_us)
                    known[item.key] = (eid, item.en_us)
                    stats.adopted += 1
                # 這次沒有原文（item.en_us 為空）時，沿用既有原文，只補譯文

            added = False
            for src, tw, cn in scan_translations(item, convert):
                if (eid, src) in have:
                    continue
                target.add_translation(eid, src, tw, cn)
                have.add((eid, src))
                added = True
            if not is_new:
                if added:
                    stats.added_translations += 1
                else:
                    stats.existing += 1
    return stats


def _read_existing(
    query: Query, version: str, kind: str, mod_id: str
) -> tuple[Known, Have]:
    known = {
        key: (eid, en)
        for eid, key, en in query(
            "SELECT id, key, en_us FROM entry "
            "WHERE mc_version=? AND kind=? AND mod_id=?",
            (version, kind, mod_id),
        )
    }
    have = {
        (eid, src)
        for eid, src in query(
            "SELECT t.entry_id, t.source FROM translation t "
            "JOIN entry e ON e.id = t.entry_id "
            "WHERE e.mc_version=? AND e.kind=? AND e.mod_id=?",
            (version, kind, mod_id),
        )
    }
    return known, have


class DbIngestTarget:
    """寫入交易內的資料庫；``touched`` 收集新增譯文的條目（交易結束前重算生效譯文）。"""

    def __init__(
        self, conn: sqlite3.Connection, version: str, touched: list[int]
    ) -> None:
        self.conn, self.version, self.touched = conn, version, touched

    def existing(self, kind: str, mod_id: str) -> tuple[Known, Have]:
        return _read_existing(self.conn.execute, self.version, kind, mod_id)

    def add_entry(self, kind: str, mod_id: str, key: str, en_us: str) -> int:
        return self.conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES (?,?,?,?,?)",
            (kind, self.version, mod_id, key, en_us),
        ).lastrowid

    def record_src_change(
        self, kind: str, mod_id: str, key: str, old_en: str, new_en: str
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO src_change "
            "(kind, mc_version, mod_id, key, old_en, new_en) "
            "VALUES (?,?,?,?,?,?)",
            (kind, self.version, mod_id, key, old_en, new_en),
        )

    def adopt_en(self, entry_id: int, en_us: str) -> None:
        self.conn.execute("UPDATE entry SET en_us=? WHERE id=?", (en_us, entry_id))

    def add_translation(
        self, entry_id: int, source: int, zh_tw: str, zh_cn: str
    ) -> None:
        review_status = "unreviewed" if source == SRC_MANUAL else None
        self.conn.execute(
            "INSERT INTO translation "
            "(entry_id, source, zh_tw, zh_cn, review_status) "
            "VALUES (?,?,?,?,?)",
            (entry_id, source, zh_tw, zh_cn, review_status),
        )
        self.touched.append(entry_id)


class PreviewIngestTarget:
    """預覽：只讀資料庫（``query`` 為 None 表示還沒有資料庫），新增的內容只記在記憶體。

    同一個 (類型, 模組) 第二次出現時沿用記憶體裡的狀態，與實際寫入時「後面的 jar 看得到
    前面 jar 新增的條目」一致。新增條目給負數 id，不會和資料庫裡的 id 衝突。
    """

    def __init__(self, query: Query | None, version: str) -> None:
        self.query, self.version = query, version
        self._state: dict[tuple[str, str], tuple[Known, Have]] = {}
        self._next_id = -1

    def existing(self, kind: str, mod_id: str) -> tuple[Known, Have]:
        state = self._state.get((kind, mod_id))
        if state is None:
            state = (
                _read_existing(self.query, self.version, kind, mod_id)
                if self.query is not None
                else ({}, set())
            )
            self._state[(kind, mod_id)] = state
        return state

    def add_entry(self, kind: str, mod_id: str, key: str, en_us: str) -> int:
        entry_id, self._next_id = self._next_id, self._next_id - 1
        return entry_id

    def record_src_change(
        self, kind: str, mod_id: str, key: str, old_en: str, new_en: str
    ) -> None:
        return None

    def adopt_en(self, entry_id: int, en_us: str) -> None:
        return None

    def add_translation(
        self, entry_id: int, source: int, zh_tw: str, zh_cn: str
    ) -> None:
        return None


def preview_ingest_many(
    target: PreviewIngestTarget,
    batches: Iterable[Iterable[ScanItem]],
    convert: Callable[[str], str] | None,
) -> list[IngestStats]:
    """預覽多份掃描結果；``target`` 在整次掃描共用（見 ``PreviewIngestTarget``）。"""
    return [
        ingest_groups(target, group_scan_items(items), convert) for items in batches
    ]
