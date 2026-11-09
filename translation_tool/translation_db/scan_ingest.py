"""translation_tool/translation_db/scan_ingest.py：掃描結果寫入資料庫時的分類規則。

一個掃描項目是「新增」「補入新來源譯文」「已存在，略過」「原文已變動」還是「補上原文」，
由 ``ingest_groups`` 決定；實際寫入（``DbIngestTarget``）與預覽（``PreviewIngestTarget``）
共用同一套規則，預覽的數字才會和開始掃描後的結果一致。

預覽不寫入資料庫：它把「這次掃描中已新增的條目與譯文」記在記憶體裡，後面的 jar 才會像實際
寫入時一樣看到前面 jar 新增的內容（例如同一個模組分散在多個 jar）。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from translation_tool.core.source_locale import (
    locale_from_source_path,
    locale_from_source_selector,
    normalize_source_locale,
)
from translation_tool.translation_db.identity import canonical_entry_key
from translation_tool.translation_db.models import EntryOrigin, IngestStats, ScanItem
from translation_tool.translation_db.schema import SRC_JAR_CN, SRC_JAR_TW, SRC_MANUAL

_LOG = logging.getLogger(__name__)
_SLOW_INGEST_GROUP_SECONDS = 1.0


class KnownEntries(dict[str, tuple[int, str]]):
    """同一個 (類型, 模組) 的既有條目 ``{key: (entry_id, en_us)}``，附路徑別名索引。

    ``_find_existing`` 要找「鍵值不同、但 ``canonical_entry_key`` 相同」的條目。逐一比對
    所有已知鍵值是平方級（已知鍵值含本次匯入新增的），所以寫入時就維護
    ``canonical 鍵值 → 實際鍵值集合``。索引只存鍵值，原文與條目編號一律從 dict 本身讀取，
    補原文後不會過期。只支援新增與覆寫；刪除類操作沒有用到，明確不支援以免索引失真。
    """

    def __init__(
        self, kind: str, entries: Iterable[tuple[str, tuple[int, str]]] = ()
    ) -> None:
        super().__init__()
        self.kind = kind
        # 以 dict 當有序集合：同一條目有多個別名鍵時，挑選順序與插入順序一致（與逐一比對相同）
        self._by_canonical: dict[str, dict[str, None]] = {}
        for key, value in entries:
            self[key] = value

    def __setitem__(self, key: str, value: tuple[int, str]) -> None:
        if key not in self:
            self._by_canonical.setdefault(canonical_entry_key(self.kind, key), {})[
                key
            ] = None
        super().__setitem__(key, value)

    def aliases_of(self, key: str) -> list[str]:
        """與 ``key`` 的 canonical 鍵值相同、但鍵值不同的已知鍵值。"""
        same = self._by_canonical.get(canonical_entry_key(self.kind, key), ())
        return [other for other in same if other != key]

    def _unsupported(self, *args: object, **kwargs: object) -> None:
        raise TypeError("KnownEntries 只支援新增與覆寫鍵值")

    __delitem__ = pop = popitem = clear = update = setdefault = __ior__ = _unsupported


# (kind, mod_id) → 該模組既有條目與既有譯文 {(entry_id, source)}
Known = KnownEntries
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

    def record_origin(self, item: ScanItem, entry_id: int) -> None: ...

    def remove_origins(self, origins: Iterable[EntryOrigin]) -> None: ...


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
    completed_groups = 0
    completed_items = 0
    groups_started = time.perf_counter()
    for (kind, mod_id), group in groups.items():
        group_started = time.perf_counter()
        known, have = target.existing(kind, mod_id)
        for item_index, item in enumerate(group, 1):
            if len(group) >= 1000 and item_index % 1000 == 0:
                _LOG.info(
                    "[資料庫匯入] 大型群組進度：種類 %s，模組 %s，項目 %d/%d，已耗時 %.3f 秒",
                    kind,
                    mod_id,
                    item_index,
                    len(group),
                    time.perf_counter() - group_started,
                )
            cur = _find_existing(known, kind, item)
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

            target.record_origin(item, eid)

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
        completed_groups += 1
        completed_items += len(group)
        elapsed = time.perf_counter() - group_started
        if elapsed >= _SLOW_INGEST_GROUP_SECONDS:
            _LOG.warning(
                "[資料庫匯入] 群組處理過慢：種類 %s，模組 %s，耗時 %.3f 秒，"
                "項目 %d，既有條目 %d",
                kind,
                mod_id,
                elapsed,
                len(group),
                len(known),
            )
        if completed_groups % 25 == 0 or completed_groups == len(groups):
            _LOG.info(
                "[資料庫匯入] 群組進度：%d/%d，項目 %d/%d，已耗時 %.3f 秒",
                completed_groups,
                len(groups),
                completed_items,
                sum(len(items) for items in groups.values()),
                time.perf_counter() - groups_started,
            )
    return stats


def _find_existing(known: Known, kind: str, item: ScanItem) -> tuple[int, str] | None:
    """先找原鍵，再只在唯一且原文相同的路徑別名中重用條目。

    多個 legacy 別名同時存在時不猜 survivor；讓資料庫清理工具先處理，避免掃描
    再把翻譯寫到錯誤的條目。
    """
    exact = known.get(item.key)
    if exact is not None:
        return exact
    matches_by_id = {
        known[key][0]: known[key]
        for key in known.aliases_of(item.key)
        if not item.en_us or not known[key][1] or _same_text(known[key][1], item.en_us)
    }
    if len(matches_by_id) != 1:
        return None
    match = next(iter(matches_by_id.values()))
    known[item.key] = match
    return match


def _same_text(left: str, right: str) -> bool:
    return left.replace("\r\n", "\n").replace("\r", "\n") == right.replace(
        "\r\n", "\n"
    ).replace("\r", "\n")


def _read_existing(
    query: Query, version: str, kind: str, mod_id: str
) -> tuple[Known, Have]:
    known = KnownEntries(
        kind,
        (
            (key, (eid, en))
            for eid, key, en in query(
                "SELECT id, key, en_us FROM entry "
                "WHERE mc_version=? AND kind=? AND mod_id=?",
                (version, kind, mod_id),
            )
        ),
    )
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
    """寫入交易內的資料庫；``touched`` 收集新增的條目與新增譯文的條目（交易結束前重算生效譯文）。"""

    def __init__(
        self, conn: sqlite3.Connection, version: str, touched: list[int]
    ) -> None:
        self.conn, self.version, self.touched = conn, version, touched

    def existing(self, kind: str, mod_id: str) -> tuple[Known, Have]:
        return _read_existing(self.conn.execute, self.version, kind, mod_id)

    def add_entry(self, kind: str, mod_id: str, key: str, en_us: str) -> int:
        entry_id = self.conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES (?,?,?,?,?)",
            (kind, self.version, mod_id, key, en_us),
        ).lastrowid
        # 新條目（即使沒有譯文）也要回報：匯入後的跨版本沿用只處理本次匯入的條目
        self.touched.append(entry_id)
        return entry_id

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

    def record_origin(self, item: ScanItem, entry_id: int) -> None:
        """保存可回寫來源；傳統 jar 掃描沒有來源 selector，完全不寫入。"""
        if not item.source_path or not item.source_selector:
            return
        source_locale = normalize_source_locale(item.source_locale)
        if source_locale is None:
            source_locale = locale_from_source_selector(item.source_selector)
        if source_locale is None:
            source_locale = locale_from_source_path(item.source_path)
        self.conn.execute(
            "INSERT INTO entry_origin "
            "(entry_id, pack_scope, source_kind, source_path, selector, "
            "source_hash, format_profile, source_locale) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(entry_id, pack_scope, source_kind, source_path, selector) "
            "DO UPDATE SET source_hash=excluded.source_hash, "
            "format_profile=excluded.format_profile, "
            "source_locale=excluded.source_locale",
            (
                entry_id,
                item.pack_scope,
                item.kind,
                item.source_path,
                item.source_selector,
                item.source_hash,
                item.source_format,
                source_locale,
            ),
        )

    def remove_origins(self, origins: Iterable[EntryOrigin]) -> None:
        """批次清除本次完整重掃描確認已失效的精確來源。

        逐筆刪除會對同一版本的 ``entry`` 反覆執行大型子查詢；大型 FTB
        掃描可能因此把數千個 stale origin 放大成數十億次索引探查。把
        origin key 放入連線專屬的暫存表後一次刪除，仍保留來源範圍與版本
        隔離，但只掃描持久化資料表一次。
        """
        origin_keys = {
            (
                origin.pack_scope,
                origin.source_kind,
                origin.source_path,
                origin.selector,
            )
            for origin in origins
        }
        if not origin_keys:
            return

        self.conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _moddb_stale_origins ("
            "pack_scope TEXT NOT NULL, "
            "source_kind TEXT NOT NULL, "
            "source_path TEXT NOT NULL, "
            "selector TEXT NOT NULL, "
            "PRIMARY KEY (pack_scope, source_kind, source_path, selector)"
            ") WITHOUT ROWID"
        )
        self.conn.execute("DELETE FROM temp._moddb_stale_origins")
        self.conn.executemany(
            "INSERT INTO temp._moddb_stale_origins "
            "(pack_scope, source_kind, source_path, selector) VALUES (?,?,?,?)",
            origin_keys,
        )
        self.conn.execute(
            "DELETE FROM entry_origin "
            "WHERE EXISTS ("
            "SELECT 1 FROM temp._moddb_stale_origins AS stale "
            "WHERE stale.pack_scope=entry_origin.pack_scope "
            "AND stale.source_kind=entry_origin.source_kind "
            "AND stale.source_path=entry_origin.source_path "
            "AND stale.selector=entry_origin.selector"
            ") "
            "AND EXISTS ("
            "SELECT 1 FROM entry AS e "
            "WHERE e.id=entry_origin.entry_id AND e.mc_version=?"
            ")",
            (self.version,),
        )
        self.conn.execute("DELETE FROM temp._moddb_stale_origins")


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
                else (KnownEntries(kind), set())
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

    def record_origin(self, item: ScanItem, entry_id: int) -> None:
        return None

    def remove_origins(self, origins: Iterable[EntryOrigin]) -> None:
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
