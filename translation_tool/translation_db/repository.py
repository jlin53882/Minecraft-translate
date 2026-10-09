"""repository.py

Mod 翻譯資料庫的 SQLite 存取層：所有 SQL 都在這裡，上層只接觸 ``models`` 的資料結構。

寫入規則（與產品規格一致）：

- **掃描**：條目已存在就跳過（只新增、不覆蓋）；原文已變動者只記錄、不改動；
  既有條目缺少的譯文來源會補入。
- **AI 翻譯寫回**：只新增，已有該來源就不動；其他版本中「原文相同、完全沒有譯文」的
  空白也一併補上（只填空白）。
- **手動更新**：寫入「人工」來源（最高優先），並同步到所有版本中原文相同的條目；
  原本的譯文來源保留，異動記錄可還原。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from translation_tool.translation_db.models import (
    AITranslationReplaceResult,
    BatchReplaceChange,
    BatchReplacePlan,
    BatchReplaceResult,
    BatchReplaceSkipped,
    BatchRevertResult,
    EffectiveSourceStat,
    EntryDetail,
    EntryFilter,
    EntryRow,
    HistoryRow,
    Impact,
    IngestStats,
    QualityIssueDelta,
    ReviewPreviewItem,
    SameKeyRow,
    SameSourceAIEntry,
    SameTextRow,
    ScanItem,
    SrcChangeRow,
    TimeFilter,
    TranslationRow,
    VersionStat,
    WriteBackItem,
    WriteBackStats,
)
from translation_tool.translation_db.quality import (
    matches_quality,
    quality_state,
    token_quality_comparison,
    whitespace_note,
)
from translation_tool.translation_db.schema import (
    DB_EMPTY,
    DB_FOREIGN,
    DB_NEWER,
    DEFAULT_PRIORITY,
    SRC_AI,
    SRC_JAR_CN,
    SRC_JAR_TW,
    SRC_MANUAL,
    classify_database,
    connect,
    init_schema,
    rank_sql,
)
from translation_tool.translation_db.source_catalog import SourceCatalog
from translation_tool.utils.cancellation import (
    TaskCancelled,
    is_cancelled,
    raise_if_cancelled,
)

_ENTRY_COLS = "e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us"
_CHUNK = 400  # SQLite 變數上限相容的批次大小


def _like(text: str) -> str:
    """把使用者輸入轉成 LIKE 的字面比對（跳脫 % _ \\）。"""
    esc = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


class TranslationDB:
    """資料庫存取物件（執行緒安全：單一連線 + 可重入鎖）。"""

    def __init__(
        self,
        path: str | Path,
        *,
        priority: tuple[int, ...] = DEFAULT_PRIORITY,
        readonly: bool = False,
        create: bool = True,
        sync_priority: bool = True,
    ) -> None:
        self.path = Path(path)
        self.priority = tuple(priority)
        self.readonly = readonly
        self._lock = threading.RLock()
        self._rank = rank_sql(self.priority, "t.source")
        existed = self.path.is_file()
        if not existed and (readonly or not create):
            raise FileNotFoundError(f"資料庫檔案不存在：{self.path}")
        kind = DB_EMPTY
        if existed:
            # 開啟前先以唯讀方式驗證：不是本功能的資料庫就拒絕，絕不對其他 SQLite 建立資料表或改設定
            kind = classify_database(self.path)
            if kind == DB_FOREIGN:
                raise ValueError(
                    "不是 Mod 翻譯資料庫（可能是其他用途的 SQLite、不是 SQLite 檔案，"
                    f"或檔案無法讀取；為避免污染其他資料庫，不會初始化）：{self.path}"
                )
            if kind == DB_NEWER:
                raise ValueError(
                    f"資料庫版本較新（schema 比本程式新），請更新程式後再使用：{self.path}"
                )
            if kind == DB_EMPTY and (readonly or not create):
                raise ValueError(
                    f"資料庫檔案存在但尚未初始化（沒有任何資料表），請先到「Mod 資料庫」掃描匯入：{self.path}"
                )
        self._conn = connect(self.path, readonly=readonly)
        try:
            if not readonly:
                init_schema(self._conn)
            self.has_review_state = "review_status" in {
                row[1] for row in self._conn.execute("PRAGMA table_info(translation)")
            }
            translation_columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(translation)")
            }
            self.has_translation_created_at = "created_at" in translation_columns
            self.has_translation_revision = "revision" in translation_columns
            history_columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(history)")
            }
            self.has_history_revision = "new_revision" in history_columns
            if not readonly and sync_priority:
                self._sync_priority()
            self.source_catalog = self._load_source_catalog()
        except BaseException:
            self._conn.close()
            raise

    def set_priority(self, priority: tuple[int, ...]) -> None:
        """改變來源優先序（例如新登錄了自訂來源）；有變動就重建生效譯文。"""
        self.priority = tuple(priority)
        self._rank = rank_sql(self.priority, "t.source")
        self._sync_priority()
        self.source_catalog = self._load_source_catalog()

    def _load_source_catalog(self) -> SourceCatalog:
        row = self._one("SELECT value FROM meta WHERE key='custom_sources'")
        try:
            registry = json.loads(row[0]) if row else {}
        except (TypeError, ValueError):
            registry = {}
        return SourceCatalog.from_registry(registry)

    def _priority_order(self, priority: tuple[int, ...] | None = None) -> str:
        """Return the canonical effective-source ordering for a selected priority."""
        rank = self._rank if priority is None else rank_sql(tuple(priority), "t.source")
        if not self.has_review_state:
            return f"CASE WHEN t.checker <> '' THEN 0 ELSE 1 END, {rank}"
        return (
            "CASE WHEN t.review_status='reviewed' THEN 0 "
            "WHEN t.checker <> '' AND t.review_status IS NOT 'unreviewed' THEN 0 "
            f"ELSE 1 END, {rank}"
        )

    # ------------------------------------------------------------------ 基礎
    def close(self) -> None:
        """關閉連線。"""
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(
        self, *, bump_generation_on_noop: bool = True
    ) -> Iterator[sqlite3.Connection]:
        """單一寫入交易（失敗自動回復）。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            changes_before = self._conn.total_changes
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                # 資料有變動：遞增世代，讓統計快取（stat_cache）失效
                if bump_generation_on_noop or self._conn.total_changes > changes_before:
                    self._conn.execute(
                        "INSERT INTO meta (key, value) VALUES ('data_gen', '1') "
                        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
                    )
                self._conn.commit()

    def _data_gen(self) -> str:
        row = self._one("SELECT value FROM meta WHERE key = 'data_gen'")
        return row[0] if row else "0"

    def _cached(self, key: str, compute: Callable[[], Any]) -> Any:
        """昂貴統計的快取：資料世代沒變就直接回傳上次算好的結果（JSON 存在資料庫內）。

        數十萬筆條目的總覽統計要 1 秒以上；資料沒變時（切換頁籤、翻頁、重開程式）
        直接讀快取。唯讀連線或舊資料庫沒有快取表時，退回每次重算。
        """
        try:
            gen = self._data_gen()
            row = self._one(
                "SELECT value FROM stat_cache WHERE key = ? AND gen = ?", (key, gen)
            )
            if row is not None:
                return json.loads(row[0])
        except sqlite3.Error:
            return compute()
        value = compute()
        if not self.readonly:
            try:
                with self._lock:
                    if self._data_gen() != gen:
                        return value  # 計算期間資料又變了：不寫舊世代，也不清掉新世代的快取
                    self._conn.execute("DELETE FROM stat_cache WHERE gen <> ?", (gen,))
                    self._conn.execute(
                        "INSERT OR REPLACE INTO stat_cache (key, gen, value) "
                        "VALUES (?,?,?)",
                        (key, gen, json.dumps(value, ensure_ascii=False)),
                    )
                    self._conn.commit()
            except sqlite3.Error:
                pass  # 快取寫不進去（被其他連線鎖住等）不影響結果
        return value

    def warm_stats(self) -> None:
        """預先算好總覽統計並存入快取（大量寫入後在背景呼叫，之後切到總覽頁就是即時的）。"""
        stats = self.version_stats()
        self.overview()
        if stats:
            self.missing_by_mod(stats[0].mc_version)

    def _q(self, sql: str, params: Iterable = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _one(self, sql: str, params: Iterable = ()) -> tuple | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def _sync_priority(self) -> None:
        """優先序與上次記錄不同時，重建所有條目的生效譯文。"""
        want = ",".join(str(s) for s in self.priority)
        row = self._one("SELECT value FROM meta WHERE key='priority'")
        if row and row[0] == want:
            return
        with self._tx() as conn:
            self._rebuild_effective(conn)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('priority', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (want,),
            )

    # ------------------------------------------------------------ 生效譯文
    def _rebuild_effective(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM effective")
        conn.execute(
            f"""
            INSERT INTO effective (entry_id, zh_tw, source, checker, review_status)
            SELECT entry_id, zh_tw, source, checker, review_status FROM (
                SELECT t.entry_id, t.zh_tw, t.source, t.checker, t.review_status,
                       ROW_NUMBER() OVER (
                           PARTITION BY t.entry_id
                           ORDER BY {self._priority_order()}
                       ) AS rn
                FROM translation t WHERE t.zh_tw <> ''
            ) WHERE rn = 1
            """
        )

    def _refresh(
        self,
        conn: sqlite3.Connection,
        entry_ids: Iterable[int],
        *,
        priority: tuple[int, ...] | None = None,
    ) -> None:
        """Refresh the shared projection, optionally using an explicit priority."""
        ids = list({int(i) for i in entry_ids})
        priority_order = self._priority_order(priority)
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM effective WHERE entry_id IN ({marks})", chunk)
            conn.execute(
                f"""
                INSERT INTO effective (entry_id, zh_tw, source, checker, review_status)
                SELECT entry_id, zh_tw, source, checker, review_status FROM (
                    SELECT t.entry_id, t.zh_tw, t.source, t.checker, t.review_status,
                       ROW_NUMBER() OVER (
                           PARTITION BY t.entry_id
                               ORDER BY {priority_order}
                       ) AS rn
                    FROM translation t
                    WHERE t.zh_tw <> '' AND t.entry_id IN ({marks})
                ) WHERE rn = 1
                """,
                chunk,
            )

    # ------------------------------------------------------------------ 掃描
    def ingest(
        self,
        version: str,
        items: Iterable[ScanItem],
        convert: Callable[[str], str] | None = None,
    ) -> IngestStats:
        """寫入掃描結果。只新增、不覆蓋；原文已變動者只記錄。

        ``convert`` 把簡中轉成繁中（沒有繁中、只有簡中時用）；None 表示不轉換。
        """
        stats = IngestStats()
        groups: dict[tuple[str, str], list[ScanItem]] = {}
        for item in items:
            groups.setdefault((item.kind, item.mod_id), []).append(item)

        touched: list[int] = []
        with self._tx() as conn:
            for (kind, mod_id), group in groups.items():
                known = {
                    key: (eid, en)
                    for eid, key, en in conn.execute(
                        "SELECT id, key, en_us FROM entry "
                        "WHERE mc_version=? AND kind=? AND mod_id=?",
                        (version, kind, mod_id),
                    )
                }
                have = {
                    (eid, src)
                    for eid, src in conn.execute(
                        "SELECT t.entry_id, t.source FROM translation t "
                        "JOIN entry e ON e.id = t.entry_id "
                        "WHERE e.mc_version=? AND e.kind=? AND e.mod_id=?",
                        (version, kind, mod_id),
                    )
                }
                for item in group:
                    cur = known.get(item.key)
                    is_new = cur is None
                    if cur is None:
                        eid = conn.execute(
                            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
                            "VALUES (?,?,?,?,?)",
                            (kind, version, mod_id, item.key, item.en_us),
                        ).lastrowid
                        known[item.key] = (eid, item.en_us)
                        stats.new_entries += 1
                    elif cur[1] != item.en_us and item.en_us and cur[1]:
                        conn.execute(
                            "INSERT OR IGNORE INTO src_change "
                            "(kind, mc_version, mod_id, key, old_en, new_en) "
                            "VALUES (?,?,?,?,?,?)",
                            (kind, version, mod_id, item.key, cur[1], item.en_us),
                        )
                        stats.en_changed += 1
                        continue
                    else:
                        eid = cur[0]
                        if cur[1] != item.en_us and item.en_us:
                            # 原文原本未知（例如先匯入了只有 zh_tw 的翻譯 ZIP）：補上
                            conn.execute(
                                "UPDATE entry SET en_us=? WHERE id=?", (item.en_us, eid)
                            )
                            known[item.key] = (eid, item.en_us)
                            stats.adopted += 1
                        # 這次沒有原文（item.en_us 為空）時，沿用既有原文，只補譯文

                    adds: list[tuple[int, str, str]] = []
                    if item.zh_tw.strip():
                        src_tw = SRC_JAR_TW if item.source is None else item.source
                        adds.append((src_tw, item.zh_tw, item.zh_cn))
                    elif item.zh_cn.strip() and convert is not None:
                        converted = convert(item.zh_cn)
                        if (
                            converted.strip()
                        ):  # 空白判斷用 strip，但存原樣（前後空白在遊戲內可能有意義）
                            adds.append((SRC_JAR_CN, converted, item.zh_cn))
                    added = False
                    for src, tw, cn in adds:
                        if (eid, src) in have:
                            continue
                        review_status = "unreviewed" if src == SRC_MANUAL else None
                        conn.execute(
                            "INSERT INTO translation "
                            "(entry_id, source, zh_tw, zh_cn, review_status) "
                            "VALUES (?,?,?,?,?)",
                            (eid, src, tw, cn, review_status),
                        )
                        have.add((eid, src))
                        touched.append(eid)
                        added = True
                    if not is_new:
                        if added:
                            stats.added_translations += 1
                        else:
                            stats.existing += 1
            self._refresh(conn, touched)
        return stats

    def record_scan(self, version: str, folder: str, stats: dict) -> None:
        """記錄一次掃描（總覽頁的「最近掃描」）。"""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO scan_run (mc_version, folder, finished_at, stats) "
                "VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
                (version, folder, json.dumps(stats, ensure_ascii=False)),
            )

    def last_scans(self, limit: int = 5) -> list[dict]:
        rows = self._q(
            "SELECT mc_version, folder, finished_at, stats FROM scan_run "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "mc_version": v,
                "folder": f,
                "finished_at": at,
                "stats": json.loads(s or "{}"),
            }
            for v, f, at, s in rows
        ]

    # ---------------------------------------------------------------- 統計
    def versions(self) -> list[str]:
        """資料庫中出現過的遊戲版本（條目多者在前；有快取，見 ``_cached``）。"""
        return self._cached("versions", self._versions_uncached)

    def _versions_uncached(self) -> list[str]:
        return [
            r[0]
            for r in self._q(
                "SELECT mc_version FROM entry GROUP BY mc_version ORDER BY COUNT(*) DESC"
            )
        ]

    def mods(self, version: str) -> list[str]:
        """某版本出現過的模組（有快取，見 ``_cached``）。"""
        return self._cached(f"mods:{version}", lambda: self._mods_uncached(version))

    def kinds(self, version: str) -> list[str]:
        """某版本資料庫裡實際出現過的條目類型（日後新增類型不必改這裡；有快取）。"""
        return self._cached(f"kinds:{version}", lambda: self._kinds_uncached(version))

    def _kinds_uncached(self, version: str) -> list[str]:
        return [
            r[0]
            for r in self._q(
                "SELECT DISTINCT kind FROM entry WHERE mc_version=? ORDER BY kind",
                (version,),
            )
        ]

    def _mods_uncached(self, version: str) -> list[str]:
        return [
            r[0]
            for r in self._q(
                "SELECT DISTINCT mod_id FROM entry WHERE mc_version=? ORDER BY mod_id",
                (version,),
            )
        ]

    def version_stats(self) -> list[VersionStat]:
        rows = self._cached("version_stats", self._version_stats_rows)
        return [VersionStat(*r) for r in rows]

    def effective_source_stats_by_version(self) -> list[EffectiveSourceStat]:
        """Count effective entries once per source and manual review state.

        The query groups all versions and source codes in one pass. For an old
        read-only database, manual rows are reported as ``legacy_unknown``.
        """
        rows = self._cached(
            "effective_source_stats_by_version",
            self._effective_source_stats_by_version_rows,
        )
        return [EffectiveSourceStat(*row) for row in rows]

    def _effective_source_stats_by_version_rows(self) -> list[list]:
        if self.has_review_state:
            review = (
                f"CASE WHEN f.source = {SRC_MANUAL} THEN "
                "CASE WHEN f.review_status IN "
                "('unreviewed', 'reviewed', 'legacy_unknown') "
                "THEN f.review_status ELSE 'legacy_unknown' END END"
            )
        else:
            review = f"CASE WHEN f.source = {SRC_MANUAL} THEN 'legacy_unknown' END"
        return [
            list(row)
            for row in self._q(
                "SELECT e.mc_version, f.source, "
                f"{review}, COUNT(*) "
                "FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
                f"GROUP BY e.mc_version, f.source, {review} "
                "ORDER BY e.mc_version, f.source, 3"
            )
        ]

    def _version_stats_rows(self) -> list[list]:
        rows = self._q(
            """
            SELECT e.mc_version,
                   COUNT(*),
                   SUM(f.source = ?),
                   SUM(f.source IS NOT NULL AND f.source NOT IN (?, ?, ?)),
                   SUM(f.source = ?),
                   SUM(f.source = ?),
                   SUM(f.entry_id IS NULL)
            FROM entry e LEFT JOIN effective f ON f.entry_id = e.id
            GROUP BY e.mc_version ORDER BY COUNT(*) DESC
            """,
            # 自訂來源與「模組自帶／字幕組…」同屬藍色段：凡不是人工、簡中轉繁、AI 的都算
            (SRC_MANUAL, SRC_MANUAL, SRC_JAR_CN, SRC_AI, SRC_JAR_CN, SRC_AI),
        )
        return [
            [v, t, m or 0, j or 0, c or 0, a or 0, u or 0]
            for v, t, m, j, c, a, u in rows
        ]

    def overview(self) -> dict:
        """總覽頁的全域數字（有快取，見 ``_cached``）。"""
        return self._cached("overview", self._overview_uncached)

    def _overview_uncached(self) -> dict:
        mods = self._one("SELECT COUNT(DISTINCT mod_id) FROM entry")[0]
        content = self._one(
            "SELECT COUNT(*) FROM (SELECT 1 FROM entry GROUP BY kind, mod_id, key, en_us)"
        )[0]
        diff = self._one(
            """
            SELECT COUNT(*) FROM (
                SELECT e.kind, e.mod_id, e.key, e.en_us
                FROM entry e JOIN effective f ON f.entry_id = e.id
                WHERE e.en_us <> ''
                GROUP BY e.kind, e.mod_id, e.key, e.en_us
                HAVING COUNT(DISTINCT f.zh_tw) > 1
            )
            """
        )[0]
        changed = self._one("SELECT COUNT(*) FROM src_change")[0]
        no_source = self._one("SELECT COUNT(*) FROM entry WHERE en_us = ''")[0]
        return {
            "no_source": no_source,
            "mods": mods,
            "content": content,
            "diff": diff,
            "src_changed": changed,
        }

    def missing_by_mod(self, version: str, limit: int = 10) -> list[dict]:
        """缺譯最多的模組（有快取，見 ``_cached``）。"""
        return self._cached(
            f"missing:{version}:{limit}", lambda: self._missing_by_mod(version, limit)
        )

    def _missing_by_mod(self, version: str, limit: int) -> list[dict]:
        rows = self._q(
            """
            SELECT e.mod_id, COUNT(*) AS total, SUM(f.entry_id IS NULL) AS missing
            FROM entry e LEFT JOIN effective f ON f.entry_id = e.id
            WHERE e.mc_version = ?
            GROUP BY e.mod_id HAVING missing > 0
            ORDER BY missing DESC LIMIT ?
            """,
            (version, limit),
        )
        return [
            {
                "mod_id": m,
                "total": t,
                "missing": x,
                "progress": round(100 * (t - x) / t) if t else 0,
            }
            for m, t, x in rows
        ]

    def src_changes(self, version: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT kind, mc_version, mod_id, key, old_en, new_en, detected_at FROM src_change"
        params: list = []
        if version:
            sql += " WHERE mc_version=?"
            params.append(version)
        rows = self._q(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
        return [
            dict(zip(("kind", "version", "mod_id", "key", "old_en", "new_en", "at"), r))
            for r in rows
        ]

    # ------------------------------------------------------------ 條目查詢
    def _row(self, r: tuple) -> EntryRow:
        review_status = r[9]
        if r[7] == SRC_MANUAL and review_status is None:
            review_status = "legacy_unknown"
        en_us, zh_tw = r[5] or "", r[6] or ""
        quality, issues, whitespace = quality_state(en_us, zh_tw)
        return EntryRow(
            id=r[0],
            kind=r[1],
            mc_version=r[2],
            mod_id=r[3],
            key=r[4],
            en_us=en_us,
            zh_tw=zh_tw,
            source=r[7],
            checker=r[8] or "",
            diff=bool(r[10]),
            review_status=review_status,
            created_at=r[11] if len(r) > 11 else None,
            translation_created_at=r[12] if len(r) > 12 else None,
            effective_updated_at=r[13] if len(r) > 13 else None,
            last_manual_at=r[14] if len(r) > 14 else None,
            quality_state=quality,
            quality_issues=issues,
            whitespace_note=whitespace,
        )

    @property
    def _effective_review_sql(self) -> str:
        return (
            "f.review_status"
            if self.has_review_state
            else f"CASE WHEN f.source={SRC_MANUAL} THEN 'legacy_unknown' END"
        )

    # 有效譯文（``effective`` 視圖／表，已套用來源優先序）與 en_us 嚴格相等，且兩者都非空
    _SAME_AS_SOURCE_SQL = (
        "(f.entry_id IS NOT NULL AND e.en_us <> '' AND f.zh_tw <> '' "
        "AND f.zh_tw = e.en_us)"
    )
    _DIFF_SQL = """EXISTS (
        SELECT 1 FROM entry e2 JOIN effective f2 ON f2.entry_id = e2.id
        WHERE e2.kind = e.kind AND e2.mod_id = e.mod_id AND e2.key = e.key
          AND e2.en_us = e.en_us AND e.en_us <> '' AND e2.mc_version <> e.mc_version
          AND (f.zh_tw IS NULL OR f2.zh_tw <> f.zh_tw))"""

    def _query_source(self) -> str:
        return (
            "FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
            "LEFT JOIN translation et ON et.entry_id=e.id AND et.source=f.source "
            f"LEFT JOIN translation man ON man.entry_id=e.id AND man.source={SRC_MANUAL}"
        )

    def _filter_sql(self, criteria: EntryFilter) -> tuple[str, list]:
        where = ["e.mc_version = ?"]
        params: list = [criteria.version]
        if criteria.entry_ids is not None:
            where.append("e.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(criteria.entry_ids))
        if criteria.include_ids is not None:
            where.append("e.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(criteria.include_ids))
        if criteria.exclude_ids:
            where.append("e.id NOT IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(criteria.exclude_ids))
        if criteria.source is not None:
            where.append("f.source = ?")
            params.append(int(criteria.source))
        if criteria.review_status is not None:
            where.append(
                f"f.source = {SRC_MANUAL} AND {self._effective_review_sql} = ?"
            )
            params.append(criteria.review_status)
        if criteria.mod_id:
            where.append("e.mod_id = ?")
            params.append(criteria.mod_id)
        if criteria.kind:
            where.append("e.kind = ?")
            params.append(criteria.kind)
        if criteria.query.strip():
            like = _like(criteria.query.strip())
            where.append(
                "(e.en_us LIKE ? ESCAPE '\\' OR e.key LIKE ? ESCAPE '\\' "
                "OR f.zh_tw LIKE ? ESCAPE '\\')"
            )
            params += [like, like, like]

        if criteria.state == "none":
            where.append("f.entry_id IS NULL")
        elif criteria.state == "diff":
            where.append(f"f.entry_id IS NOT NULL AND {self._DIFF_SQL}")
        elif criteria.state == "changed":
            where.append(
                "EXISTS (SELECT 1 FROM src_change sc WHERE sc.kind=e.kind "
                "AND sc.mc_version=e.mc_version AND sc.mod_id=e.mod_id AND sc.key=e.key)"
            )
        elif criteria.state == "manual":
            where.append(f"f.source={SRC_MANUAL} AND NOT {self._DIFF_SQL}")
        elif criteria.state == "ok":
            where.append(
                f"f.entry_id IS NOT NULL AND f.source<>{SRC_MANUAL} AND NOT {self._DIFF_SQL}"
            )
        elif criteria.state == "same":
            where.append(self._SAME_AS_SOURCE_SQL)

        if criteria.time is not None:
            time_where, time_params = self._time_filter_sql(criteria.time)
            where.extend(time_where)
            params.extend(time_params)
        return " AND ".join(where), params

    def _time_filter_sql(self, spec: TimeFilter) -> tuple[list[str], list]:
        where: list[str] = []
        params: list = []
        if spec.kind == "manual_activity":
            actions_by_kind = {
                "all": ("manual", "review", "batch_replace", "revert", "batch_revert"),
                "edit": ("manual",),
                "review": ("review",),
                "batch": ("batch_replace", "batch_revert"),
                "revert": ("revert", "batch_revert"),
            }
            actions = actions_by_kind[spec.action]
            marks = ",".join("?" for _ in actions)
            event = [f"h.entry_id=e.id AND h.action IN ({marks})"]
            params.extend(actions)
            if spec.start_utc is not None:
                event.append("h.at >= ?")
                params.append(spec.start_utc)
            if spec.end_utc is not None:
                event.append("h.at < ?")
                params.append(spec.end_utc)
            where.append(
                "EXISTS (SELECT 1 FROM history h WHERE " + " AND ".join(event) + ")"
            )
            return where, params

        if spec.kind == "entry_created":
            column = "e.created_at"
        elif spec.kind == "translation_created":
            column = "et.created_at" if self.has_translation_created_at else "NULL"
        else:
            column = "et.updated_at"

        if spec.unknown_policy == "only":
            where.append(f"{column} IS NULL")
            return where, params
        has_bounds = spec.start_utc is not None or spec.end_utc is not None
        if has_bounds:
            bounds: list[str] = []
            if spec.start_utc is not None:
                bounds.append(f"{column} >= ?")
                params.append(spec.start_utc)
            if spec.end_utc is not None:
                bounds.append(f"{column} < ?")
                params.append(spec.end_utc)
            in_range = " AND ".join(bounds)
            if spec.unknown_policy == "include":
                where.append(f"({column} IS NULL OR ({in_range}))")
            else:
                where.append(f"{column} IS NOT NULL AND ({in_range})")
        elif spec.unknown_policy == "exclude":
            where.append(f"{column} IS NOT NULL")
        return where, params

    def _sort_sql(self, sort_by: str) -> str:
        if sort_by == "entry_newest":
            return "e.created_at IS NULL, e.created_at DESC, e.id"
        if sort_by == "entry_oldest":
            return "e.created_at IS NULL, e.created_at, e.id"
        if sort_by == "effective_updated_newest":
            return "et.updated_at IS NULL, et.updated_at DESC, e.id"
        if sort_by == "manual_activity_newest":
            activity = (
                "(SELECT MAX(h.at) FROM history h WHERE h.entry_id=e.id AND "
                "h.action IN ('manual','review','batch_replace','revert','batch_revert'))"
            )
            return f"{activity} IS NULL, {activity} DESC, e.id"
        return "e.mod_id, e.kind, e.key, e.id"

    @staticmethod
    def _sort_filtered_rows(rows: list[tuple], sort_by: str) -> list[tuple]:
        index = {
            "entry_newest": 11,
            "entry_oldest": 11,
            "effective_updated_newest": 13,
            "manual_activity_newest": 14,
        }.get(sort_by)
        if index is None:
            return sorted(rows, key=lambda row: (row[3], row[1], row[4], row[0]))
        dated = [row for row in rows if row[index] is not None]
        undated = [row for row in rows if row[index] is None]
        dated.sort(key=lambda row: row[0])
        dated.sort(key=lambda row: row[index], reverse=sort_by != "entry_oldest")
        undated.sort(key=lambda row: row[0])
        return [*dated, *undated]

    def _entry_select_sql(self) -> str:
        translation_created = (
            "et.created_at" if self.has_translation_created_at else "NULL"
        )
        manual_review = (
            "man.review_status"
            if self.has_review_state
            else f"CASE WHEN man.source={SRC_MANUAL} THEN 'legacy_unknown' END"
        )
        manual_revision = "man.revision" if self.has_translation_revision else "NULL"
        return (
            f"SELECT {_ENTRY_COLS}, f.zh_tw, f.source, f.checker, "
            f"{self._effective_review_sql}, "
            f"CASE WHEN f.entry_id IS NULL THEN 0 ELSE {self._DIFF_SQL} END, "
            f"e.created_at, {translation_created}, et.updated_at, "
            "(SELECT MAX(h.at) FROM history h WHERE h.entry_id=e.id AND "
            "h.action IN ('manual','review','batch_replace','revert','batch_revert')), "
            "man.zh_tw, man.checker, "
            f"{manual_review}, {manual_revision}, man.updated_at, "
            f"{('et.revision' if self.has_translation_revision else 'NULL')} "
        )

    def _batch_replace_select_sql(self) -> str:
        """Select only fields used by the batch preview, keeping its row layout stable.

        The normal entry projection also calculates cross-version differences and
        the latest history timestamp for list sorting. Batch replacement does not
        consume those values, and calculating them for every entry in an all-page
        preview can dominate the query on a large database.
        """
        manual_review = (
            "man.review_status"
            if self.has_review_state
            else f"CASE WHEN man.source={SRC_MANUAL} THEN 'legacy_unknown' END"
        )
        manual_revision = "man.revision" if self.has_translation_revision else "NULL"
        effective_revision = "et.revision" if self.has_translation_revision else "NULL"
        return (
            "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
            "f.zh_tw, f.source, f.checker, "
            f"{self._effective_review_sql}, 0, NULL, NULL, et.updated_at, NULL, "
            f"man.zh_tw, man.checker, {manual_review}, {manual_revision}, NULL, "
            f"{effective_revision} "
        )

    def _query_entry_rows(
        self,
        criteria: EntryFilter,
        *,
        conn: sqlite3.Connection | None = None,
        limit: int | None = None,
        offset: int = 0,
        progress_callback: Callable[[str, float], None] | None = None,
        batch_replace_projection: bool = False,
    ) -> list[tuple]:
        cond, params = self._filter_sql(criteria)
        source = self._query_source()
        select = (
            self._batch_replace_select_sql()
            if batch_replace_projection
            else self._entry_select_sql()
        )
        has_quality = criteria.quality.active
        if has_quality:
            sql = f"{select} {source} WHERE {cond}"
        else:
            sql = f"{select} {source} WHERE {cond} ORDER BY {self._sort_sql(criteria.sort_by)}"
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend((max(0, int(limit)), max(0, int(offset))))
        if batch_replace_projection:
            # These previews can cover every entry in a version. Fetch in bounded
            # chunks so cancellation and progress reporting remain responsive
            # while SQLite streams a large result set.
            connection = conn or self._conn
            rows = []
            connection.set_progress_handler(lambda: int(is_cancelled()), 10_000)
            try:
                cursor = connection.execute(sql, tuple(params))
                while chunk := cursor.fetchmany(_CHUNK):
                    raise_if_cancelled()
                    rows.extend(chunk)
                    if progress_callback is not None:
                        progress_callback(
                            f"查詢符合條目（已讀取 {len(rows):,} 筆）", 0.02
                        )
                raise_if_cancelled()
            except sqlite3.OperationalError:
                if is_cancelled():
                    raise TaskCancelled() from None
                raise
            finally:
                connection.set_progress_handler(None, 0)
        elif conn is None:
            rows = self._q(sql, params)
        else:
            rows = conn.execute(sql, tuple(params)).fetchall()
        if has_quality:
            filtered = []
            total = len(rows)
            for index, row in enumerate(rows, 1):
                if index % 100 == 1:
                    raise_if_cancelled()
                    if progress_callback is not None:
                        progress_callback(
                            "檢查品質條件", 0.05 + 0.4 * index / max(1, total)
                        )
                if matches_quality(row[5] or "", row[6] or "", criteria.quality):
                    filtered.append(row)
            rows = filtered
            rows = self._sort_filtered_rows(rows, criteria.sort_by)
            if limit is not None:
                rows = rows[
                    max(0, int(offset)) : max(0, int(offset)) + max(0, int(limit))
                ]
        return rows

    def list_entries(
        self,
        version: str | None = None,
        *,
        criteria: EntryFilter | None = None,
        mod_id: str | None = None,
        kind: str | None = None,
        state: str = "all",
        query: str = "",
        source: int | None = None,
        review_status: str | None = None,
        entry_ids: Sequence[int] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[EntryRow], int]:
        """條目清單（含總筆數）。state：all / none / diff / changed / manual / ok / same（翻譯與原文相同）。

        ``source``：只列出目前 ``effective.source`` 為該來源的條目；較低優先序、
        目前未生效的來源譯文仍會保留並顯示在條目明細中，但不會命中主要來源篩選。
        ``entry_ids``：只列出這些條目（例如批次機翻「特殊字元不一致」的那幾筆）；
        用 ``json_each`` 傳入，數量多也不會超過 SQLite 的參數上限。
        """
        if criteria is None:
            if version is None:
                raise ValueError("條目查詢必須指定遊戲版本")
            criteria = EntryFilter(
                version=version,
                mod_id=mod_id,
                kind=kind,
                state=state,
                query=query,
                source=source,
                review_status=review_status,
                entry_ids=None if entry_ids is None else tuple(entry_ids),
            )
        elif version is not None and version != criteria.version:
            raise ValueError("version 與 criteria.version 不一致")

        if (
            criteria.state == "changed"
            and self._one("SELECT 1 FROM src_change LIMIT 1") is None
        ):
            return [], 0

        if criteria.quality.active:
            all_rows = self._query_entry_rows(criteria)
            total = len(all_rows)
            rows = all_rows[
                max(0, int(offset)) : max(0, int(offset)) + max(0, int(limit))
            ]
        else:
            cond, params = self._filter_sql(criteria)
            total = self._cached_count(
                f"SELECT COUNT(*) {self._query_source()} WHERE {cond}", params
            )
            rows = self._query_entry_rows(criteria, limit=limit, offset=offset)
        return [self._row(row) for row in rows], total

    def _database_identity(self) -> str:
        stat = self.path.stat()
        return f"{self.path.resolve()}|{stat.st_dev}|{stat.st_ino}"

    def _batch_sibling_rows(
        self,
        roots: list[tuple],
        *,
        conn: sqlite3.Connection | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> dict[tuple[str, str, str, str], list[tuple]]:
        keys = list(dict.fromkeys((row[1], row[3], row[4], row[5]) for row in roots))
        result: dict[tuple[str, str, str, str], list[tuple]] = {key: [] for key in keys}
        for offset in range(0, len(keys), 100):
            raise_if_cancelled()
            if progress_callback is not None:
                progress_callback(
                    "查詢跨版本候選", 0.45 + 0.15 * offset / max(1, len(keys))
                )
            chunk = keys[offset : offset + 100]
            tuple_sql = ",".join("(?,?,?,?)" for _ in chunk)
            params = [value for key in chunk for value in key]
            sql = (
                f"{self._batch_replace_select_sql()} {self._query_source()} "
                f"WHERE (e.kind,e.mod_id,e.key,e.en_us) IN ({tuple_sql}) "
                "AND e.en_us<>'' ORDER BY e.kind,e.mod_id,e.key,e.en_us,e.mc_version"
            )
            rows = (
                conn.execute(sql, tuple(params)).fetchall()
                if conn is not None
                else self._q(sql, params)
            )
            for index, row in enumerate(rows, 1):
                if index % 100 == 1:
                    raise_if_cancelled()
                key = (row[1], row[3], row[4], row[5])
                result.setdefault(key, []).append(row)
        return result

    @staticmethod
    def _batch_skipped(
        row: tuple, reason: str, *, extra: bool = False
    ) -> BatchReplaceSkipped:
        return BatchReplaceSkipped(
            entry_id=int(row[0]),
            mc_version=row[2],
            key=row[4],
            reason=reason,
            is_extra_version=extra,
        )

    def _build_batch_replace_plan(
        self,
        criteria: EntryFilter,
        find_text: str,
        replace_text: str,
        *,
        propagate: bool,
        confirmed_quality_worsening: bool,
        conn: sqlite3.Connection | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> BatchReplacePlan:
        raise_if_cancelled()
        if progress_callback is not None:
            progress_callback("查詢符合條目", 0.02)
        roots = self._query_entry_rows(
            criteria,
            conn=conn,
            progress_callback=progress_callback,
            batch_replace_projection=True,
        )
        siblings = (
            self._batch_sibling_rows(
                roots, conn=conn, progress_callback=progress_callback
            )
            if propagate
            else {}
        )
        root_ids = tuple(int(row[0]) for row in roots)
        root_id_set = set(root_ids)
        changes: dict[int, BatchReplaceChange] = {}
        skipped: dict[int, BatchReplaceSkipped] = {}

        @lru_cache(maxsize=4096)
        def quality_comparison(
            source: str, before: str, after: str
        ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[QualityIssueDelta, ...]]:
            old, new, deltas = token_quality_comparison(source, before, after)
            return tuple(old), tuple(new), deltas

        def add_candidate(
            row: tuple, *, extra: bool, root_entry_id: int | None
        ) -> None:
            entry_id = int(row[0])
            old_text = row[6] or ""
            if row[7] is None or not old_text:
                skipped.setdefault(
                    entry_id, self._batch_skipped(row, "沒有目前生效譯文", extra=extra)
                )
                return
            if find_text not in old_text:
                skipped.setdefault(
                    entry_id,
                    self._batch_skipped(row, "找不到符合的原文片段", extra=extra),
                )
                return
            new_text = old_text.replace(find_text, replace_text)
            if not new_text.strip():
                skipped.setdefault(
                    entry_id, self._batch_skipped(row, "替換後譯文為空白", extra=extra)
                )
                return
            if new_text == old_text:
                skipped.setdefault(
                    entry_id, self._batch_skipped(row, "替換後內容未改變", extra=extra)
                )
                return
            source_text = row[5] or ""
            old_issues, new_issues, quality_deltas = quality_comparison(
                source_text, old_text, new_text
            )
            old_space = whitespace_note(old_text)
            new_space = whitespace_note(new_text)
            changes[entry_id] = BatchReplaceChange(
                entry_id=entry_id,
                kind=row[1],
                mc_version=row[2],
                mod_id=row[3],
                key=row[4],
                en_us=row[5],
                old_zh_tw=old_text,
                new_zh_tw=new_text,
                effective_source=int(row[7]),
                effective_checker=row[8] or "",
                effective_review_status=row[9],
                effective_revision=row[20],
                effective_updated_at=row[13],
                manual_zh_tw=row[15],
                manual_checker=row[16],
                manual_review_status=row[17],
                manual_revision=row[18],
                is_extra_version=extra,
                root_entry_id=root_entry_id,
                old_quality_issues=old_issues,
                new_quality_issues=new_issues,
                old_whitespace_note=old_space,
                new_whitespace_note=new_space,
                quality_deltas=quality_deltas,
            )
            skipped.pop(entry_id, None)

        for index, row in enumerate(roots, 1):
            if index % 100 == 1:
                raise_if_cancelled()
                if progress_callback is not None:
                    progress_callback(
                        "建立替換預覽", 0.62 + 0.36 * index / max(1, len(roots))
                    )
            add_candidate(row, extra=False, root_entry_id=None)

        if propagate:
            seen_extra: set[int] = set()
            for index, root in enumerate(roots, 1):
                if index % 100 == 1:
                    raise_if_cancelled()
                root_id = int(root[0])
                old_text = root[6] or ""
                if root[7] is None or not old_text or find_text not in old_text:
                    continue
                key = (root[1], root[3], root[4], root[5])
                if not key[3]:
                    continue
                for sibling in siblings.get(key, []):
                    sibling_id = int(sibling[0])
                    if (
                        sibling_id == root_id
                        or sibling_id in root_id_set
                        or sibling_id in seen_extra
                    ):
                        continue
                    if (
                        (sibling[6] or "") != old_text
                        or sibling[7] != root[7]
                        or sibling[9] != root[9]
                    ):
                        skipped.setdefault(
                            sibling_id,
                            self._batch_skipped(
                                sibling,
                                "其他版本的生效譯文、來源或審核狀態不同",
                                extra=True,
                            ),
                        )
                    else:
                        add_candidate(sibling, extra=True, root_entry_id=root_id)
                    seen_extra.add(sibling_id)

        return BatchReplacePlan(
            database_identity=self._database_identity(),
            criteria=criteria,
            find_text=find_text,
            replace_text=replace_text,
            propagate=propagate,
            root_ids=root_ids,
            changes=tuple(changes[key] for key in sorted(changes)),
            skipped=tuple(skipped[key] for key in sorted(skipped)),
            confirmed_quality_worsening=confirmed_quality_worsening,
        )

    def preview_batch_replace(
        self,
        criteria: EntryFilter,
        find_text: str,
        replace_text: str,
        *,
        propagate: bool = False,
        confirmed_quality_worsening: bool = False,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> BatchReplacePlan:
        """Build a literal, all-page replacement plan without changing the DB."""
        if not find_text:
            raise ValueError("尋找文字不可為空")
        with self._lock:
            return self._build_batch_replace_plan(
                criteria,
                find_text,
                replace_text,
                propagate=propagate,
                confirmed_quality_worsening=confirmed_quality_worsening,
                progress_callback=progress_callback,
            )

    def execute_batch_replace(
        self,
        plan: BatchReplacePlan,
        *,
        actor: str = "",
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> BatchReplaceResult:
        """Apply exactly the previewed replacements in one compare-and-set transaction."""
        if not plan.changes:
            return BatchReplaceResult(
                "", 0, plan.skipped_count, plan.total_unique_entries
            )
        if (
            any(change.quality_worsened for change in plan.changes)
            and not plan.confirmed_quality_worsening
        ):
            raise ValueError("替換會增加特殊字元或空白問題，請明確確認後再執行")
        batch = uuid.uuid4().hex
        if progress_callback is not None:
            progress_callback("重新檢查資料庫快照", 0.05)
        with self._tx(bump_generation_on_noop=False) as conn:
            if self._database_identity() != plan.database_identity:
                raise ValueError("資料庫已切換，請重新預覽批次替換")
            current = self._build_batch_replace_plan(
                plan.criteria,
                plan.find_text,
                plan.replace_text,
                propagate=plan.propagate,
                confirmed_quality_worsening=plan.confirmed_quality_worsening,
                conn=conn,
                progress_callback=progress_callback,
            )
            if current != plan:
                raise ValueError("預覽後條目已變動，請重新產生批次替換預覽")
            if progress_callback is not None:
                progress_callback("提交中（本階段不可取消）", 0.55)
            for index, change in enumerate(plan.changes, 1):
                if progress_callback is not None and index % 100 == 0:
                    progress_callback(
                        "提交中（本階段不可取消）",
                        0.55 + 0.4 * index / len(plan.changes),
                    )
                prev = conn.execute(
                    "SELECT zh_tw, checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (change.entry_id, SRC_MANUAL),
                ).fetchone()
                conn.execute(
                    "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
                    "VALUES (?,?,?,'','unreviewed') ON CONFLICT(entry_id,source) DO UPDATE SET "
                    "zh_tw=excluded.zh_tw, checker='', review_status='unreviewed', "
                    "updated_at=CURRENT_TIMESTAMP",
                    (change.entry_id, SRC_MANUAL, change.new_zh_tw),
                )
                new_revision = conn.execute(
                    "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                    (change.entry_id, SRC_MANUAL),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO history (entry_id,batch,actor,action,old_zh_tw,new_zh_tw,"
                    "prev_manual,note,prev_checker,prev_review_status,new_checker,"
                    "new_review_status,prev_revision,new_revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        change.entry_id,
                        batch,
                        actor,
                        "batch_replace",
                        change.old_zh_tw,
                        change.new_zh_tw,
                        prev[0] if prev else None,
                        "批次字面取代",
                        prev[1] if prev else None,
                        prev[2] if prev else None,
                        "",
                        "unreviewed",
                        prev[3] if prev else None,
                        new_revision,
                    ),
                )
            self._refresh(conn, [change.entry_id for change in plan.changes])
        if progress_callback is not None:
            progress_callback("批次替換已提交", 1.0)
        return BatchReplaceResult(
            batch, len(plan.changes), plan.skipped_count, plan.total_unique_entries
        )

    def revert_batch_replace(self, batch_id: str) -> BatchRevertResult:
        """CAS-revert every still-current row in a batch, leaving newer edits intact."""
        reverted: list[int] = []
        skipped = 0
        with self._tx(bump_generation_on_noop=False) as conn:
            rows = conn.execute(
                "SELECT id,entry_id,new_zh_tw,prev_manual,prev_checker,prev_review_status,"
                "new_checker,new_review_status,prev_revision,new_revision FROM history "
                "WHERE batch=? AND action='batch_replace' ORDER BY id DESC",
                (batch_id,),
            ).fetchall()
            revert_batch = uuid.uuid4().hex
            for (
                hid,
                eid,
                new_text,
                prev_text,
                prev_checker,
                prev_status,
                new_checker,
                new_status,
                prev_revision,
                new_revision,
            ) in rows:
                cur = conn.execute(
                    "SELECT zh_tw,checker,review_status,revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (eid, SRC_MANUAL),
                ).fetchone()
                if (
                    cur is None
                    or cur[0] != new_text
                    or cur[1] != new_checker
                    or cur[2] != new_status
                    or cur[3] != new_revision
                ):
                    skipped += 1
                    continue
                if prev_text is not None and (
                    prev_checker is None or prev_status is None
                ):
                    skipped += 1
                    continue
                if prev_text is None:
                    conn.execute(
                        "DELETE FROM translation WHERE entry_id=? AND source=?",
                        (eid, SRC_MANUAL),
                    )
                    restored = None
                else:
                    conn.execute(
                        "UPDATE translation SET zh_tw=?,checker=?,review_status=?,"
                        "updated_at=CURRENT_TIMESTAMP WHERE entry_id=? AND source=?",
                        (prev_text, prev_checker, prev_status, eid, SRC_MANUAL),
                    )
                    restored = conn.execute(
                        "SELECT checker,review_status,revision FROM translation "
                        "WHERE entry_id=? AND source=?",
                        (eid, SRC_MANUAL),
                    ).fetchone()
                conn.execute(
                    "INSERT INTO history (entry_id,batch,action,old_zh_tw,new_zh_tw,note,"
                    "prev_checker,prev_review_status,new_checker,new_review_status,"
                    "prev_revision,new_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        revert_batch,
                        "batch_revert",
                        new_text,
                        prev_text or "",
                        f"批次還原 #{hid}",
                        cur[1],
                        cur[2],
                        restored[0] if restored else None,
                        restored[1] if restored else None,
                        cur[3],
                        restored[2] if restored else None,
                    ),
                )
                reverted.append(eid)
            self._refresh(conn, reverted)
        return BatchRevertResult(len(reverted), skipped, len(reverted) + skipped)

    def get_entry(self, entry_id: int) -> EntryRow | None:
        effective_created = (
            "et.created_at" if self.has_translation_created_at else "NULL"
        )
        last_activity = (
            "(SELECT MAX(h.at) FROM history h WHERE h.entry_id=e.id AND "
            "h.action IN ('manual','review','batch_replace','revert','batch_revert'))"
        )
        r = self._one(
            f"SELECT {_ENTRY_COLS}, f.zh_tw, f.source, f.checker, "
            f"{self._effective_review_sql}, "
            f"CASE WHEN f.entry_id IS NULL THEN 0 ELSE {self._DIFF_SQL} END, "
            f"e.created_at, {effective_created}, et.updated_at, {last_activity} "
            "FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
            "LEFT JOIN translation et ON et.entry_id=e.id AND et.source=f.source "
            "WHERE e.id=?",
            (entry_id,),
        )
        return self._row(r) if r else None

    def entry_detail(
        self, entry_id: int, *, text_limit: int = 200
    ) -> EntryDetail | None:
        """編輯區需要的全部資料：各來源譯文、同鍵值其他版本、相同原文建議、異動記錄。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        detail = EntryDetail(entry=entry)
        translation_review_sql = (
            "review_status"
            if self.has_review_state
            else f"CASE WHEN source={SRC_MANUAL} THEN 'legacy_unknown' END"
        )
        translation_created_sql = (
            "created_at" if self.has_translation_created_at else "NULL"
        )
        detail.translations = [
            TranslationRow(*r)
            for r in self._q(
                "SELECT source, zh_tw, zh_cn, checker, updated_at, "
                f"{translation_review_sql}, {translation_created_sql} FROM translation "
                "WHERE entry_id=? ORDER BY source",
                (entry_id,),
            )
        ]
        detail.versions = (
            [entry.mc_version]
            if not entry.en_us  # 原文未知：沒有可比對的內容，只有自己
            else [
                r[0]
                for r in self._q(
                    "SELECT mc_version FROM entry INDEXED BY idx_entry_content "
                    "WHERE kind=? AND mod_id=? AND key=? "
                    "AND en_us=? ORDER BY mc_version",
                    (entry.kind, entry.mod_id, entry.key, entry.en_us),
                )
            ]
        )
        detail.same_key = [
            SameKeyRow(eid, ver, en, en == entry.en_us, tw or "", src, review_status)
            for eid, ver, en, tw, src, review_status in self._q(
                "SELECT e.id, e.mc_version, e.en_us, f.zh_tw, f.source, "
                f"{self._effective_review_sql} "
                "FROM entry e INDEXED BY idx_entry_content "
                "LEFT JOIN effective f ON f.entry_id = e.id "
                "WHERE e.kind=? AND e.mod_id=? AND e.key=? AND e.id<>? "
                "ORDER BY e.mc_version",
                (entry.kind, entry.mod_id, entry.key, entry_id),
            )
        ]
        detail.same_text = (
            []
            if not entry.en_us
            else [
                SameTextRow(eid, ver, mod, key, tw or "", src, review_status)
                for eid, ver, mod, key, tw, src, review_status in self._q(
                    "SELECT e.id, e.mc_version, e.mod_id, e.key, f.zh_tw, f.source, "
                    f"{self._effective_review_sql} "
                    "FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
                    "WHERE e.en_us=? AND e.kind=? AND NOT (e.mod_id=? AND e.key=?) "
                    "ORDER BY (f.zh_tw IS NULL), e.mod_id, e.key LIMIT ?",
                    (entry.en_us, entry.kind, entry.mod_id, entry.key, text_limit),
                )
            ]
        )
        detail.src_changes = [
            SrcChangeRow(*r)
            for r in self._q(
                "SELECT old_en, new_en, detected_at FROM src_change "
                "WHERE kind=? AND mc_version=? AND mod_id=? AND key=? ORDER BY id DESC",
                (entry.kind, entry.mc_version, entry.mod_id, entry.key),
            )
        ]
        history_review_sql = (
            "prev_checker, prev_review_status, new_checker, new_review_status"
            if self.has_review_state
            else "NULL, NULL, NULL, NULL"
        )
        history_revision_sql = (
            "prev_revision, new_revision" if self.has_history_revision else "NULL, NULL"
        )
        detail.history = [
            HistoryRow(*r)
            for r in self._q(
                "SELECT id, batch, at, actor, action, old_zh_tw, new_zh_tw, note, "
                f"prev_manual, {history_review_sql}, {history_revision_sql} "
                "FROM history WHERE entry_id=? ORDER BY id DESC LIMIT 50",
                (entry_id,),
            )
        ]
        return detail

    # ------------------------------------------------------------ 手動更新
    def _same_content(self, conn: sqlite3.Connection, entry_id: int) -> list[tuple]:
        """與此條目「相同內容」的條目（含自己）。原文未知的條目沒有可比對的內容，只有自己。

        先取出這筆的 (類型, 模組, 鍵值, 原文)，再用內容索引查同內容的條目。
        不用 ``(a,b,c,d) = (子查詢)`` 的寫法：沒有統計資訊時查詢規劃會只用 ``kind``
        掃描整張表（百萬筆時每次編輯都要 170 毫秒以上）。
        """
        row = conn.execute(
            "SELECT kind, mod_id, key, en_us FROM entry WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None or row[3] == "":
            return conn.execute(
                "SELECT e.id, e.mc_version, f.zh_tw, f.source FROM entry e "
                "LEFT JOIN effective f ON f.entry_id = e.id WHERE e.id = ?",
                (entry_id,),
            ).fetchall()
        return conn.execute(
            "SELECT e.id, e.mc_version, f.zh_tw, f.source "
            "FROM entry e INDEXED BY idx_entry_content "
            "LEFT JOIN effective f ON f.entry_id = e.id "
            "WHERE e.kind = ? AND e.mod_id = ? AND e.key = ? AND e.en_us = ? "
            "ORDER BY e.mc_version",
            row,
        ).fetchall()

    def preview_manual(
        self, entry_id: int, new_zh_tw: str, *, propagate: bool = True
    ) -> list[Impact]:
        """儲存前預覽：哪些條目會被改成 ``new_zh_tw``（目前譯文已相同者不列入）。"""
        with self._lock:
            rows = self._same_content(self._conn, entry_id)
        impacts: list[Impact] = []
        for eid, ver, old, src in rows:
            if eid != entry_id and not propagate:
                continue
            if (old or "") == new_zh_tw and src == SRC_MANUAL:
                continue
            impacts.append(Impact(eid, ver, old or "", src, eid == entry_id))
        return impacts

    def preview_manual_review(
        self,
        entry_id: int,
        *,
        expected_zh_tw: str,
        expected_source: int | None = None,
        expected_review_status: str | None = None,
        expected_checker: str | None = None,
        propagate: bool = True,
    ) -> list[ReviewPreviewItem]:
        """Return a review scope with both effective and existing manual snapshots.

        The returned immutable rows can be supplied back to ``review_manual`` so
        every included and skipped sibling is checked again inside the write
        transaction before any row is changed.
        """
        with self._lock:
            return self._review_preview_rows(
                self._conn,
                entry_id,
                expected_zh_tw=expected_zh_tw,
                expected_source=expected_source,
                expected_review_status=expected_review_status,
                expected_checker=expected_checker,
                propagate=propagate,
            )

    def _review_preview_rows(
        self,
        conn: sqlite3.Connection,
        entry_id: int,
        *,
        expected_zh_tw: str,
        expected_source: int | None,
        expected_review_status: str | None,
        expected_checker: str | None,
        propagate: bool,
    ) -> list[ReviewPreviewItem]:
        selected = conn.execute(
            "SELECT * FROM entry WHERE id=?", (entry_id,)
        ).fetchone()
        if selected is None:
            raise ValueError("目前譯文已變動，請重新整理後再審核")
        siblings = self._same_content(conn, entry_id)
        ids = [int(row[0]) for row in siblings]
        records: dict[int, tuple] = {}
        for offset in range(0, len(ids), _CHUNK):
            chunk = ids[offset : offset + _CHUNK]
            marks = ",".join("?" for _ in chunk)
            revision_col = "t.revision" if self.has_translation_revision else "NULL"
            manual_revision_col = (
                "m.revision" if self.has_translation_revision else "NULL"
            )
            query = (
                "SELECT e.id, e.mc_version, f.zh_tw, f.source, f.checker, "
                f"{self._effective_review_sql}, {revision_col}, m.zh_tw, m.checker, "
                f"m.review_status, {manual_revision_col} "
                "FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
                "LEFT JOIN translation t ON t.entry_id=e.id AND t.source=f.source "
                "LEFT JOIN translation m ON m.entry_id=e.id AND m.source=? "
                f"WHERE e.id IN ({marks}) ORDER BY e.mc_version"
            )
            for row in conn.execute(query, (SRC_MANUAL, *chunk)).fetchall():
                records[int(row[0])] = row
        current = records.get(entry_id)
        if (
            current is None
            or (current[2] or "") != expected_zh_tw
            or (expected_source is not None and current[3] != expected_source)
            or (
                expected_review_status is not None
                and current[5] != expected_review_status
            )
            or (expected_checker is not None and current[4] != expected_checker)
        ):
            raise ValueError("目前譯文已變動，請重新整理後再審核")

        result: list[ReviewPreviewItem] = []
        for eid, version, *_ in siblings:
            row = records[eid]
            text = row[2] or ""
            source = row[3]
            review_status = row[5]
            checker = row[4]
            if eid == entry_id:
                included = review_status != "reviewed" or source != SRC_MANUAL
                reason = "目前版本" if included else "目前版本已審核"
            elif not propagate:
                included, reason = False, "已選擇僅審核目前版本"
            elif text != expected_zh_tw:
                included, reason = False, "目前生效譯文不同"
            elif row[9] == "reviewed" and (row[7] or "") != expected_zh_tw:
                included, reason = False, "已有不同的已審核人工譯文"
            elif row[9] == "reviewed":
                included, reason = False, "已審核相同譯文"
            else:
                included, reason = True, "原文與目前譯文相同"
            result.append(
                ReviewPreviewItem(
                    entry_id=eid,
                    mc_version=version,
                    text=text,
                    source=source,
                    review_status=review_status,
                    checker=checker,
                    effective_revision=row[6],
                    manual_text=row[7],
                    manual_checker=row[8],
                    manual_review_status=row[9],
                    manual_revision=row[10],
                    included=included,
                    reason=reason,
                )
            )
        return result

    def save_manual(
        self,
        entry_id: int,
        new_zh_tw: str,
        *,
        actor: str = "",
        propagate: bool = True,
    ) -> list[Impact]:
        """儲存新譯文為人工未審核；文字未變時保留原 review 狀態。"""
        text = new_zh_tw  # 原樣儲存：前後空白、換行、格式碼都不改動
        if not text.strip():
            raise ValueError("譯文不可為空，未儲存")
        batch = uuid.uuid4().hex
        done: list[Impact] = []
        with self._tx() as conn:
            rows = self._same_content(conn, entry_id)
            ver_self = next((v for eid, v, *_ in rows if eid == entry_id), "")
            candidates = [row for row in rows if propagate or int(row[0]) == entry_id]
            candidate_ids = [int(row[0]) for row in candidates]
            previous: dict[int, tuple] = {}
            for start in range(0, len(candidate_ids), _CHUNK):
                chunk = candidate_ids[start : start + _CHUNK]
                marks = ",".join("?" for _ in chunk)
                previous.update(
                    (int(row[0]), row[1:])
                    for row in conn.execute(
                        "SELECT entry_id, zh_tw, checker, review_status, revision "
                        "FROM translation WHERE source=? "
                        f"AND entry_id IN ({marks})",
                        (SRC_MANUAL, *chunk),
                    ).fetchall()
                )

            changed = [
                (row, previous.get(int(row[0])))
                for row in candidates
                if previous.get(int(row[0])) is None or previous[int(row[0])][0] != text
            ]
            if changed:
                conn.executemany(
                    "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
                    "VALUES (?,?,?,'','unreviewed') "
                    "ON CONFLICT(entry_id, source) DO UPDATE SET "
                    "zh_tw = excluded.zh_tw, checker = '', "
                    "review_status = 'unreviewed', "
                    "updated_at = CURRENT_TIMESTAMP",
                    ((int(row[0]), SRC_MANUAL, text) for row, _prev in changed),
                )

                changed_ids = [int(row[0]) for row, _prev in changed]
                revisions: dict[int, int] = {}
                for start in range(0, len(changed_ids), _CHUNK):
                    chunk = changed_ids[start : start + _CHUNK]
                    marks = ",".join("?" for _ in chunk)
                    revisions.update(
                        (int(row[0]), int(row[1]))
                        for row in conn.execute(
                            "SELECT entry_id, revision FROM translation "
                            f"WHERE source=? AND entry_id IN ({marks})",
                            (SRC_MANUAL, *chunk),
                        ).fetchall()
                    )

                history_rows = []
                for row, prev in changed:
                    eid, ver, old, src = row
                    prev_manual = prev[0] if prev else None
                    history_rows.append(
                        (
                            eid,
                            batch,
                            actor,
                            "manual",
                            old or "",
                            text,
                            prev_manual,
                            "" if eid == entry_id else f"同步自 {ver_self}",
                            prev[1] if prev else None,
                            prev[2] if prev else None,
                            "",
                            "unreviewed",
                            prev[3] if prev else None,
                            revisions[int(eid)],
                        )
                    )
                    done.append(Impact(eid, ver, old or "", src, eid == entry_id))
                conn.executemany(
                    "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, "
                    "new_zh_tw, prev_manual, note, prev_checker, prev_review_status, "
                    "new_checker, new_review_status, prev_revision, new_revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    history_rows,
                )
            self._refresh(conn, [i.entry_id for i in done])
        return done

    def review_manual(
        self,
        entry_id: int,
        *,
        expected_zh_tw: str,
        expected_source: int | None = None,
        expected_review_status: str | None = None,
        expected_checker: str | None = None,
        expected_preview: Sequence[ReviewPreviewItem] | None = None,
        actor: str = "",
        propagate: bool = True,
    ) -> list[Impact]:
        """Confirm the current effective text as reviewed manual translation.

        ``expected_zh_tw`` is a compare-and-set guard from the editor snapshot.
        When propagation is enabled, only same-content versions currently showing
        the same text are reviewed; a different sibling translation is preserved.
        """
        if not expected_zh_tw.strip():
            raise ValueError("沒有可審核的譯文，請先輸入並儲存")
        batch = uuid.uuid4().hex
        done: list[Impact] = []
        with self._tx(bump_generation_on_noop=False) as conn:
            preview = self._review_preview_rows(
                conn,
                entry_id,
                expected_zh_tw=expected_zh_tw,
                expected_source=expected_source,
                expected_review_status=expected_review_status,
                expected_checker=expected_checker,
                propagate=propagate,
            )
            if expected_preview is not None and tuple(preview) != tuple(
                expected_preview
            ):
                raise ValueError("審核影響範圍已變動，請重新整理後再確認")
            for item in preview:
                if not item.included:
                    continue
                eid, ver = item.entry_id, item.mc_version
                prev = conn.execute(
                    "SELECT zh_tw, checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (eid, SRC_MANUAL),
                ).fetchone()
                if prev and prev[2] == "reviewed" and prev[0] == expected_zh_tw:
                    continue
                conn.execute(
                    "INSERT INTO translation "
                    "(entry_id, source, zh_tw, checker, review_status) "
                    "VALUES (?,?,?,?, 'reviewed') "
                    "ON CONFLICT(entry_id, source) DO UPDATE SET "
                    "zh_tw=excluded.zh_tw, checker=excluded.checker, "
                    "review_status='reviewed', updated_at=CURRENT_TIMESTAMP",
                    (eid, SRC_MANUAL, expected_zh_tw, actor or "人工"),
                )
                new_revision = conn.execute(
                    "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                    (eid, SRC_MANUAL),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO history "
                    "(entry_id, batch, actor, action, old_zh_tw, new_zh_tw, "
                    "prev_manual, note, prev_checker, prev_review_status, "
                    "new_checker, new_review_status, prev_revision, new_revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        batch,
                        actor,
                        "review",
                        item.text,
                        expected_zh_tw,
                        prev[0] if prev else None,
                        ""
                        if eid == entry_id
                        else f"同步審核自 {next((x.mc_version for x in preview if x.entry_id == entry_id), '')}",
                        prev[1] if prev else None,
                        prev[2] if prev else None,
                        actor or "人工",
                        "reviewed",
                        prev[3] if prev else None,
                        new_revision,
                    ),
                )
                done.append(Impact(eid, ver, item.text, item.source, eid == entry_id))
            if not any(change.is_self for change in done):
                manual = conn.execute(
                    "SELECT zh_tw, review_status FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (entry_id, SRC_MANUAL),
                ).fetchone()
                if (
                    manual is None
                    or manual[0] != expected_zh_tw
                    or manual[1] != "reviewed"
                ):
                    raise ValueError("目前譯文已變動，請重新整理後再審核")
            self._refresh(conn, [change.entry_id for change in done])
        return done

    def revert(self, history_id: int, *, whole_batch: bool = True) -> int:
        """還原一次手動更新（預設連同步的其他版本一起）。回傳還原的條目數。"""
        row = self._one("SELECT batch FROM history WHERE id=?", (history_id,))
        if row is None:
            return 0
        where, params = (
            ("batch = ?", [row[0]]) if whole_batch else ("id = ?", [history_id])
        )
        reverted: list[int] = []
        new_batch = uuid.uuid4().hex
        with self._tx(bump_generation_on_noop=False) as conn:
            for (
                hid,
                eid,
                new_tw,
                prev_manual,
                prev_checker,
                prev_review_status,
                new_checker,
                new_review_status,
                prev_revision,
                new_revision,
            ) in conn.execute(
                f"SELECT id, entry_id, new_zh_tw, prev_manual, prev_checker, "
                f"prev_review_status, new_checker, new_review_status, "
                f"prev_revision, new_revision FROM history "
                f"WHERE action IN ('manual','review') AND {where} ORDER BY id DESC",
                params,
            ).fetchall():
                cur = conn.execute(
                    "SELECT zh_tw, checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (eid, SRC_MANUAL),
                ).fetchone()
                if cur is None or cur[0] != new_tw:
                    continue  # 之後又被改過，不覆蓋
                if new_revision is not None:
                    # Revision is globally monotonic, so A→B→C→B cannot make an
                    # old history record appear current again.
                    if cur[3] != new_revision:
                        continue
                else:
                    # Legacy histories have no revision. Require no later known
                    # manual mutation and refuse to invent missing old metadata.
                    later = conn.execute(
                        "SELECT 1 FROM history WHERE entry_id=? AND id>? "
                        "AND action IN ('manual','review','revert','batch_replace',"
                        "'batch_revert') LIMIT 1",
                        (eid, hid),
                    ).fetchone()
                    if later:
                        continue
                if new_review_status is not None:
                    if cur[1] != new_checker or cur[2] != new_review_status:
                        continue
                elif cur[2] not in (None, "legacy_unknown"):
                    continue  # 舊 history 沒有狀態快照，只能還原未被新版操作改動的舊列
                if prev_manual is not None and (
                    prev_checker is None or prev_review_status is None
                ):
                    continue  # 不虛構舊 checker／審核狀態，避免改變有效譯文排序
                if prev_manual is None:
                    conn.execute(
                        "DELETE FROM translation WHERE entry_id=? AND source=?",
                        (eid, SRC_MANUAL),
                    )
                else:
                    conn.execute(
                        "UPDATE translation SET zh_tw=?, checker=?, review_status=?, "
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE entry_id=? AND source=?",
                        (
                            prev_manual,
                            prev_checker,
                            prev_review_status,
                            eid,
                            SRC_MANUAL,
                        ),
                    )
                restored = conn.execute(
                    "SELECT checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (eid, SRC_MANUAL),
                ).fetchone()
                conn.execute(
                    "INSERT INTO history (entry_id, batch, action, old_zh_tw, new_zh_tw, "
                    "note, prev_checker, prev_review_status, new_checker, new_review_status, "
                    "prev_revision, new_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        new_batch,
                        "revert",
                        new_tw,
                        prev_manual or "",
                        f"還原 #{hid}",
                        cur[1],
                        cur[2],
                        restored[0] if restored else None,
                        restored[1] if restored else None,
                        cur[3],
                        restored[2] if restored else None,
                    ),
                )
                reverted.append(eid)
            self._refresh(conn, reverted)
        return len(reverted)

    # ------------------------------------------------------------ AI 寫回
    def write_back(
        self,
        version: str,
        items: Iterable[WriteBackItem],
        *,
        fill_other_versions: bool = True,
        source: int = SRC_AI,
    ) -> WriteBackStats:
        """把翻譯結果寫回資料庫（只新增、不覆蓋）。

        目標版本沒有該條目時直接建立；其他版本中「原文相同、完全沒有譯文」的條目
        也補上同一筆譯文（只填空白）。
        """
        stats = WriteBackStats()
        touched: list[int] = []
        with self._tx() as conn:
            effective_priority = self._stored_priority(conn)
            for item in items:
                text = item.zh_tw
                if not text.strip() or not item.en_us:
                    continue
                row = conn.execute(
                    "SELECT id, en_us FROM entry WHERE kind=? AND mc_version=? "
                    "AND mod_id=? AND key=?",
                    (item.kind, version, item.mod_id, item.key),
                ).fetchone()
                if row is None:
                    eid = conn.execute(
                        "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
                        "VALUES (?,?,?,?,?)",
                        (item.kind, version, item.mod_id, item.key, item.en_us),
                    ).lastrowid
                elif row[1] != item.en_us and row[1]:
                    conn.execute(
                        "INSERT OR IGNORE INTO src_change "
                        "(kind, mc_version, mod_id, key, old_en, new_en) "
                        "VALUES (?,?,?,?,?,?)",
                        (item.kind, version, item.mod_id, item.key, row[1], item.en_us),
                    )
                    stats.skipped += 1
                    continue
                else:
                    eid = row[0]
                    if not row[1]:  # 原文原本未知：補上
                        conn.execute(
                            "UPDATE entry SET en_us=? WHERE id=?", (item.en_us, eid)
                        )
                cur = conn.execute(
                    "INSERT OR IGNORE INTO translation (entry_id, source, zh_tw) "
                    "VALUES (?,?,?)",
                    (eid, source, text),
                )
                if cur.rowcount:
                    stats.written += 1
                    touched.append(eid)
                else:
                    stats.skipped += 1
                if fill_other_versions:
                    blanks = conn.execute(
                        "SELECT e.id FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
                        "WHERE e.kind=? AND e.mod_id=? AND e.key=? AND e.en_us=? "
                        "AND e.mc_version<>? AND f.entry_id IS NULL",
                        (item.kind, item.mod_id, item.key, item.en_us, version),
                    ).fetchall()
                    for (bid,) in blanks:
                        done = conn.execute(
                            "INSERT OR IGNORE INTO translation (entry_id, source, zh_tw) "
                            "VALUES (?,?,?)",
                            (bid, source, text),
                        )
                        if done.rowcount:
                            stats.filled_other += 1
                            touched.append(bid)
            # A task's DbSettings priority is a read snapshot. Keep writes intact, but
            # refresh the shared projection using the database's current global policy.
            self._refresh(conn, touched, priority=effective_priority)
        return stats

    def _stored_priority(self, conn: sqlite3.Connection) -> tuple[int, ...]:
        """Read the shared effective-source priority inside the active transaction."""
        row = conn.execute("SELECT value FROM meta WHERE key='priority'").fetchone()
        if row is None or not row[0]:
            return self.priority
        try:
            priority = tuple(int(value) for value in row[0].split(","))
        except (TypeError, ValueError):
            return self.priority
        return priority or self.priority

    def replace_ai_translation(
        self,
        entry_id: int,
        expected_old_zh_tw: str,
        new_zh_tw: str,
        *,
        expected_version: str,
        expected_kind: str,
        expected_mod_id: str,
        expected_key: str,
        expected_en_us: str,
        actor: str = "AI 重翻",
    ) -> AITranslationReplaceResult:
        """Compare-and-set 一筆仍由 AI 生效且仍與原文相同的 AI 譯文。

        一般 ``write_back`` 保持只新增契約；此 API 僅供使用者明確執行的舊 AI
        同原文修復。來源優先序或舊值在翻譯期間變動時，回傳 ``skipped_changed``。
        """
        if not isinstance(new_zh_tw, str) or not new_zh_tw.strip():
            raise ValueError("AI 重翻譯文不可為空")

        eligible = (
            "e.id = ? AND e.mc_version = ? AND e.kind = ? AND e.mod_id = ? "
            "AND e.key = ? AND e.en_us = ? AND t.entry_id = e.id "
            "AND t.source = ? AND t.zh_tw = ? "
            "AND f.entry_id = e.id AND f.source = ? AND e.en_us <> '' "
            "AND f.zh_tw <> '' AND f.zh_tw = e.en_us"
        )
        with self._tx() as conn:
            current = conn.execute(
                "SELECT t.zh_tw FROM translation t "
                "JOIN entry e ON e.id = t.entry_id "
                "JOIN effective f ON f.entry_id = e.id "
                f"WHERE {eligible}",
                (
                    entry_id,
                    expected_version,
                    expected_kind,
                    expected_mod_id,
                    expected_key,
                    expected_en_us,
                    SRC_AI,
                    expected_old_zh_tw,
                    SRC_AI,
                ),
            ).fetchone()
            if current is None:
                return AITranslationReplaceResult("skipped_changed")
            if new_zh_tw == current[0]:
                return AITranslationReplaceResult("unchanged")

            result = conn.execute(
                "UPDATE translation SET zh_tw = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE entry_id = ? AND source = ? AND zh_tw = ? "
                "AND EXISTS (SELECT 1 FROM entry e "
                "JOIN effective f ON f.entry_id = e.id "
                "WHERE e.id = translation.entry_id AND e.mc_version = ? "
                "AND e.kind = ? AND e.mod_id = ? AND e.key = ? AND e.en_us = ? "
                "AND e.en_us <> '' "
                "AND f.source = ? AND f.zh_tw <> '' AND f.zh_tw = e.en_us)",
                (
                    new_zh_tw,
                    entry_id,
                    SRC_AI,
                    expected_old_zh_tw,
                    expected_version,
                    expected_kind,
                    expected_mod_id,
                    expected_key,
                    expected_en_us,
                    SRC_AI,
                ),
            )
            if result.rowcount != 1:
                return AITranslationReplaceResult("skipped_changed")

            conn.execute(
                "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, new_zh_tw) "
                "VALUES (?,?,?,?,?,?)",
                (
                    entry_id,
                    uuid.uuid4().hex,
                    actor,
                    "ai_retranslate",
                    current[0],
                    new_zh_tw,
                ),
            )
            self._refresh(conn, [entry_id])
        return AITranslationReplaceResult("updated")

    # --------------------------------------------------------- 批次機翻（資料庫內）
    def _untranslated_where(self, mod_ids: Sequence[str] | None) -> tuple[str, list]:
        where = "e.mc_version = ? AND f.entry_id IS NULL AND e.en_us <> ''"
        params: list = []
        if mod_ids:
            where += f" AND e.mod_id IN ({','.join('?' * len(mod_ids))})"
            params += list(mod_ids)
        return where, params

    # 其他版本已有「類型／模組／鍵值／原文都相同」的生效譯文（可直接沿用，不必呼叫 AI）
    # 其他版本的譯文必須一致才算（有衝突時不自動沿用，交給 AI 或人工，避免任意挑一版）
    _REUSABLE_SQL = """(
        SELECT COUNT(DISTINCT f2.zh_tw) FROM entry e2 JOIN effective f2 ON f2.entry_id = e2.id
        WHERE e2.kind = e.kind AND e2.mod_id = e.mod_id AND e2.key = e.key
          AND e2.en_us = e.en_us AND e2.mc_version <> e.mc_version) = 1"""

    def count_reusable(self, version: str, mod_ids: Sequence[str] | None = None) -> int:
        """未翻譯條目中，其他版本已有相同內容譯文、開始機翻時會直接沿用的筆數。"""
        where, extra = self._untranslated_where(mod_ids)
        sql = (
            "SELECT COUNT(*) FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
            f"WHERE {where} AND {self._REUSABLE_SQL}"
        )
        return self._cached_count(sql, [version, *extra])

    def count_untranslated(
        self, version: str, mod_ids: Sequence[str] | None = None
    ) -> int:
        """某版本「有原文、沒有任何譯文」的條目數（可限定模組）。"""
        where, extra = self._untranslated_where(mod_ids)
        sql = (
            "SELECT COUNT(*) FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
            f"WHERE {where}"
        )
        params = [version, *extra]
        return self._cached_count(sql, params)

    def _cached_count(self, sql: str, params: list) -> int:
        """筆數查詢的快取（同樣的條件在資料沒變時不重算；翻頁不必每頁重數）。"""
        key = (
            "count:"
            + hashlib.sha1(
                json.dumps([sql, params], ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        )
        return self._cached(key, lambda: self._one(sql, params)[0])

    def untranslated_entries(
        self,
        version: str,
        mod_ids: Sequence[str] | None = None,
        limit: int | None = None,
        *,
        exclude_reusable: bool = False,
    ) -> list[tuple[int, str, str, str, str]]:
        """未翻譯條目 ``(id, 類型, 模組, 鍵值, 原文)``；順序固定，重跑會接續同一批。

        ``exclude_reusable``：排除「其他版本已有相同內容譯文」的條目（那些會直接沿用，不送 AI）。
        """
        where, extra = self._untranslated_where(mod_ids)
        if exclude_reusable:
            where += f" AND NOT {self._REUSABLE_SQL}"
        sql = (
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us "
            "FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
            f"WHERE {where} ORDER BY e.kind, e.mod_id, e.key"
        )
        params: list = [version, *extra]
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        return self._q(sql, params)

    def _same_as_source_ai_where(
        self, version: str, mod_ids: Sequence[str] | None
    ) -> tuple[str, list]:
        where = [
            "e.mc_version = ?",
            self._SAME_AS_SOURCE_SQL,
            "f.source = ?",
        ]
        params: list = [version, SRC_AI]
        if mod_ids:
            where.append(f"e.mod_id IN ({','.join('?' * len(mod_ids))})")
            params.extend(mod_ids)
        return " AND ".join(where), params

    def count_same_as_source_ai(
        self, version: str, mod_ids: Sequence[str] | None = None
    ) -> int:
        """計算目前 effective.source 為 AI 且 effective 譯文等於非空原文的筆數。"""
        where, params = self._same_as_source_ai_where(version, mod_ids)
        sql = (
            "SELECT COUNT(*) FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
            f"WHERE {where}"
        )
        return self._cached_count(sql, params)

    def same_as_source_ai_entries(
        self,
        version: str,
        mod_ids: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[SameSourceAIEntry]:
        """列出目前生效 AI 譯文與原文相同的條目；limit<=0 表示不限。"""
        where, params = self._same_as_source_ai_where(version, mod_ids)
        sql = (
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, f.zh_tw, e.mc_version "
            "FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
            f"WHERE {where} ORDER BY e.mod_id, e.kind, e.key"
        )
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        return [SameSourceAIEntry(*row) for row in self._q(sql, params)]

    def reuse_from_other_versions(
        self, version: str, mod_ids: Sequence[str] | None = None
    ) -> int:
        """其他版本已有「類型／模組／鍵值／原文都相同」的譯文時直接沿用（不呼叫 AI）。

        只填沒有任何譯文的條目；沿用時保留原本的來源標記。回傳補上的條目數。
        """
        where, extra = self._untranslated_where(mod_ids)
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT e.id, f2.source, f2.zh_tw FROM entry e "
                "LEFT JOIN effective f ON f.entry_id = e.id "
                "JOIN entry e2 ON e2.kind = e.kind AND e2.mod_id = e.mod_id "
                "AND e2.key = e.key AND e2.en_us = e.en_us "
                "AND e2.mc_version <> e.mc_version "
                "JOIN effective f2 ON f2.entry_id = e2.id "
                f"WHERE {where} AND {self._REUSABLE_SQL} ORDER BY e.id, f2.rowid",
                [version, *extra],
            ).fetchall()
            # 譯文已確認一致；來源標記取目前優先序最高者（不可用代碼大小，人工不該被降成 AI）
            order = {s: i for i, s in enumerate(self.priority)}
            best: dict[int, tuple[int, int, str]] = {}
            for eid, source, zh_tw in rows:
                rank = order.get(source, 99)
                if eid not in best or rank < best[eid][0]:
                    best[eid] = (rank, source, zh_tw)
            touched: list[int] = []
            for eid, (_rank, source, zh_tw) in best.items():
                cur = conn.execute(
                    "INSERT OR IGNORE INTO translation (entry_id, source, zh_tw) "
                    "VALUES (?,?,?)",
                    (eid, source, zh_tw),
                )
                if cur.rowcount:
                    touched.append(eid)
            self._refresh(conn, touched)
        return len(touched)

    # ------------------------------------------------------- 翻譯流程查詢
    def load_mod(self, mod_id: str) -> list[tuple[str, str, str, str, str, int]]:
        """一個模組在所有版本的生效譯文：``(類型, 鍵值, 原文, 版本, 譯文, 來源)``。"""
        return self._q(
            "SELECT e.kind, e.key, e.en_us, e.mc_version, f.zh_tw, f.source "
            "FROM entry e JOIN effective f ON f.entry_id = e.id WHERE e.mod_id = ?",
            (mod_id,),
        )

    def load_mod_for_priority(
        self, mod_id: str, priority: tuple[int, ...]
    ) -> list[tuple[str, str, str, str, str, int]]:
        """以呼叫端快照優先序唯讀選出模組生效譯文，不依賴共享 effective 表。"""
        return self._q(self._load_mod_for_priority_sql(priority), (mod_id,))

    def _load_mod_for_priority_sql(self, priority: tuple[int, ...]) -> str:
        """Build the module-scoped ranking query used by the merge resolver."""
        # CROSS JOIN is intentional: SQLite must start from the selected module's
        # entries, then probe translations by entry_id. Otherwise it may rank the
        # entire translation table before applying the mod_id filter.
        return f"""
            SELECT ranked.kind, ranked.key, ranked.en_us, ranked.mc_version,
                   ranked.zh_tw, ranked.source
            FROM (
                SELECT e.id, e.kind, e.key, e.en_us, e.mc_version,
                       t.zh_tw, t.source,
                       ROW_NUMBER() OVER (
                           PARTITION BY t.entry_id
                           ORDER BY {self._priority_order(priority)}
                       ) AS rn
                FROM entry e
                CROSS JOIN translation t ON t.entry_id = e.id
                WHERE e.mod_id = ? AND t.zh_tw <> ''
            ) AS ranked
            WHERE ranked.rn = 1
            ORDER BY ranked.id
        """

    def count_entries(self) -> int:
        return self._one("SELECT COUNT(*) FROM entry")[0]
