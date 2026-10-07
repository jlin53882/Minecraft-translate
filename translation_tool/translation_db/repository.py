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
from pathlib import Path
from typing import Any

from translation_tool.translation_db.models import (
    AITranslationReplaceResult,
    EntryDetail,
    EntryRow,
    HistoryRow,
    Impact,
    IngestStats,
    SameKeyRow,
    SameSourceAIEntry,
    SameTextRow,
    ScanItem,
    SrcChangeRow,
    TranslationRow,
    VersionStat,
    WriteBackItem,
    WriteBackStats,
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
                self._sync_priority()
        except BaseException:
            self._conn.close()
            raise

    def set_priority(self, priority: tuple[int, ...]) -> None:
        """改變來源優先序（例如新登錄了自訂來源）；有變動就重建生效譯文。"""
        self.priority = tuple(priority)
        self._rank = rank_sql(self.priority, "t.source")
        self._sync_priority()

    # ------------------------------------------------------------------ 基礎
    def close(self) -> None:
        """關閉連線。"""
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """單一寫入交易（失敗自動回復）。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                # 資料有變動：遞增世代，讓統計快取（stat_cache）失效
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
            INSERT INTO effective (entry_id, zh_tw, source, checker)
            SELECT entry_id, zh_tw, source, checker FROM (
                SELECT t.entry_id, t.zh_tw, t.source, t.checker,
                       ROW_NUMBER() OVER (
                           PARTITION BY t.entry_id
                           ORDER BY CASE WHEN t.checker <> '' THEN 0 ELSE 1 END,
                                    {self._rank}
                       ) AS rn
                FROM translation t WHERE t.zh_tw <> ''
            ) WHERE rn = 1
            """
        )

    def _refresh(self, conn: sqlite3.Connection, entry_ids: Iterable[int]) -> None:
        ids = list({int(i) for i in entry_ids})
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM effective WHERE entry_id IN ({marks})", chunk)
            conn.execute(
                f"""
                INSERT INTO effective (entry_id, zh_tw, source, checker)
                SELECT entry_id, zh_tw, source, checker FROM (
                    SELECT t.entry_id, t.zh_tw, t.source, t.checker,
                           ROW_NUMBER() OVER (
                               PARTITION BY t.entry_id
                               ORDER BY CASE WHEN t.checker <> '' THEN 0 ELSE 1 END,
                                        {self._rank}
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
                        conn.execute(
                            "INSERT INTO translation (entry_id, source, zh_tw, zh_cn) "
                            "VALUES (?,?,?,?)",
                            (eid, src, tw, cn),
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
        return EntryRow(
            id=r[0],
            kind=r[1],
            mc_version=r[2],
            mod_id=r[3],
            key=r[4],
            en_us=r[5],
            zh_tw=r[6] or "",
            source=r[7],
            checker=r[8] or "",
            diff=bool(r[9]),
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

    def list_entries(
        self,
        version: str,
        *,
        mod_id: str | None = None,
        kind: str | None = None,
        state: str = "all",
        query: str = "",
        source: int | None = None,
        entry_ids: Sequence[int] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[EntryRow], int]:
        """條目清單（含總筆數）。state：all / none / diff / changed / manual / ok / same（翻譯與原文相同）。

        ``source``：只列出「有該來源譯文」的條目（不論最後採用的是哪個來源，
        所以新匯入的來源即使排在較低優先序、沒被採用，也找得到）。
        ``entry_ids``：只列出這些條目（例如批次機翻「特殊字元不一致」的那幾筆）；
        用 ``json_each`` 傳入，數量多也不會超過 SQLite 的參數上限。
        """
        where = ["e.mc_version = ?"]
        params: list = [version]
        if entry_ids is not None:
            where.append("e.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps([int(i) for i in entry_ids]))
        if source is not None:
            where.append(
                "EXISTS (SELECT 1 FROM translation ts WHERE ts.entry_id = e.id "
                "AND ts.source = ? AND ts.zh_tw <> '')"
            )
            params.append(int(source))
        if mod_id:
            where.append("e.mod_id = ?")
            params.append(mod_id)
        if kind:
            where.append("e.kind = ?")
            params.append(kind)
        if query.strip():
            like = _like(query.strip())
            where.append(
                "(e.en_us LIKE ? ESCAPE '\\' OR e.key LIKE ? ESCAPE '\\' "
                "OR f.zh_tw LIKE ? ESCAPE '\\')"
            )
            params += [like, like, like]
        if state == "none":
            where.append("f.entry_id IS NULL")
        elif state == "diff":
            where.append(f"f.entry_id IS NOT NULL AND {self._DIFF_SQL}")
        elif state == "changed":  # 掃描時發現原文改了（資料庫仍保留舊原文）
            where.append(
                "EXISTS (SELECT 1 FROM src_change sc WHERE sc.kind = e.kind "
                "AND sc.mc_version = e.mc_version AND sc.mod_id = e.mod_id "
                "AND sc.key = e.key)"
            )
        elif state == "manual":
            where.append(f"f.source = {SRC_MANUAL} AND NOT {self._DIFF_SQL}")
        elif state == "ok":
            where.append(
                f"f.entry_id IS NOT NULL AND f.source <> {SRC_MANUAL} AND NOT {self._DIFF_SQL}"
            )
        elif state == "same":  # 目前有效譯文與原文完全相同（派生條件，不是儲存的狀態）
            where.append(self._SAME_AS_SOURCE_SQL)
        if state == "changed" and self._one("SELECT 1 FROM src_change LIMIT 1") is None:
            return [], 0  # 沒有任何原文變動記錄：不必掃描整個版本
        cond = " AND ".join(where)
        base = f"FROM entry e LEFT JOIN effective f ON f.entry_id = e.id WHERE {cond}"
        total = self._cached_count(f"SELECT COUNT(*) {base}", params)
        rows = self._q(
            f"SELECT {_ENTRY_COLS}, f.zh_tw, f.source, f.checker, "
            f"CASE WHEN f.entry_id IS NULL THEN 0 ELSE {self._DIFF_SQL} END "
            f"{base} ORDER BY e.mod_id, e.kind, e.key LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return [self._row(r) for r in rows], total

    def get_entry(self, entry_id: int) -> EntryRow | None:
        r = self._one(
            f"SELECT {_ENTRY_COLS}, f.zh_tw, f.source, f.checker, "
            f"CASE WHEN f.entry_id IS NULL THEN 0 ELSE {self._DIFF_SQL} END "
            "FROM entry e LEFT JOIN effective f ON f.entry_id = e.id WHERE e.id = ?",
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
        detail.translations = [
            TranslationRow(*r)
            for r in self._q(
                "SELECT source, zh_tw, zh_cn, checker, updated_at FROM translation "
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
            SameKeyRow(eid, ver, en, en == entry.en_us, tw or "", src)
            for eid, ver, en, tw, src in self._q(
                "SELECT e.id, e.mc_version, e.en_us, f.zh_tw, f.source "
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
                SameTextRow(eid, ver, mod, key, tw or "", src)
                for eid, ver, mod, key, tw, src in self._q(
                    "SELECT e.id, e.mc_version, e.mod_id, e.key, f.zh_tw, f.source "
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
        detail.history = [
            HistoryRow(*r)
            for r in self._q(
                "SELECT id, batch, at, actor, action, old_zh_tw, new_zh_tw, note "
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

    def save_manual(
        self,
        entry_id: int,
        new_zh_tw: str,
        *,
        actor: str = "",
        propagate: bool = True,
    ) -> list[Impact]:
        """手動儲存譯文：寫入「人工」來源，並同步原文相同的其他版本。回傳受影響的條目。"""
        text = new_zh_tw  # 原樣儲存：前後空白、換行、格式碼都不改動
        if not text.strip():
            raise ValueError("譯文不可為空，未儲存")
        batch = uuid.uuid4().hex
        done: list[Impact] = []
        with self._tx() as conn:
            rows = self._same_content(conn, entry_id)
            ver_self = next((v for eid, v, *_ in rows if eid == entry_id), "")
            for eid, ver, old, src in rows:
                if eid != entry_id and not propagate:
                    continue
                prev = conn.execute(
                    "SELECT zh_tw FROM translation WHERE entry_id=? AND source=?",
                    (eid, SRC_MANUAL),
                ).fetchone()
                if prev and prev[0] == text:
                    continue
                conn.execute(
                    "INSERT INTO translation (entry_id, source, zh_tw, checker) "
                    "VALUES (?,?,?,?) ON CONFLICT(entry_id, source) DO UPDATE SET "
                    "zh_tw = excluded.zh_tw, checker = excluded.checker, "
                    "updated_at = CURRENT_TIMESTAMP",
                    (eid, SRC_MANUAL, text, actor or "人工"),
                )
                conn.execute(
                    "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, "
                    "new_zh_tw, prev_manual, note) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        batch,
                        actor,
                        "manual",
                        old or "",
                        text,
                        prev[0] if prev else None,
                        "" if eid == entry_id else f"同步自 {ver_self}",
                    ),
                )
                done.append(Impact(eid, ver, old or "", src, eid == entry_id))
            self._refresh(conn, [i.entry_id for i in done])
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
        with self._tx() as conn:
            for hid, eid, new_tw, prev_manual in conn.execute(
                f"SELECT id, entry_id, new_zh_tw, prev_manual FROM history "
                f"WHERE action='manual' AND {where}",
                params,
            ).fetchall():
                cur = conn.execute(
                    "SELECT zh_tw FROM translation WHERE entry_id=? AND source=?",
                    (eid, SRC_MANUAL),
                ).fetchone()
                if cur is None or cur[0] != new_tw:
                    continue  # 之後又被改過，不覆蓋
                if prev_manual is None:
                    conn.execute(
                        "DELETE FROM translation WHERE entry_id=? AND source=?",
                        (eid, SRC_MANUAL),
                    )
                else:
                    conn.execute(
                        "UPDATE translation SET zh_tw=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE entry_id=? AND source=?",
                        (prev_manual, eid, SRC_MANUAL),
                    )
                conn.execute(
                    "INSERT INTO history (entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        eid,
                        new_batch,
                        "revert",
                        new_tw,
                        prev_manual or "",
                        f"還原 #{hid}",
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
            self._refresh(conn, touched)
        return stats

    def replace_ai_translation(
        self,
        entry_id: int,
        expected_old_zh_tw: str,
        new_zh_tw: str,
        *,
        actor: str = "AI 重翻",
    ) -> AITranslationReplaceResult:
        """Compare-and-set 一筆仍由 AI 生效且仍與原文相同的 AI 譯文。

        一般 ``write_back`` 保持只新增契約；此 API 僅供使用者明確執行的舊 AI
        同原文修復。來源優先序或舊值在翻譯期間變動時，回傳 ``skipped_changed``。
        """
        if not isinstance(new_zh_tw, str) or not new_zh_tw.strip():
            raise ValueError("AI 重翻譯文不可為空")

        eligible = (
            "e.id = ? AND t.entry_id = e.id AND t.source = ? AND t.zh_tw = ? "
            "AND f.entry_id = e.id AND f.source = ? AND e.en_us <> '' "
            "AND f.zh_tw <> '' AND f.zh_tw = e.en_us"
        )
        with self._tx() as conn:
            current = conn.execute(
                "SELECT t.zh_tw FROM translation t "
                "JOIN entry e ON e.id = t.entry_id "
                "JOIN effective f ON f.entry_id = e.id "
                f"WHERE {eligible}",
                (entry_id, SRC_AI, expected_old_zh_tw, SRC_AI),
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
                "WHERE e.id = translation.entry_id AND e.en_us <> '' "
                "AND f.source = ? AND f.zh_tw <> '' AND f.zh_tw = e.en_us)",
                (new_zh_tw, entry_id, SRC_AI, expected_old_zh_tw, SRC_AI),
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
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, f.zh_tw "
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

    def count_entries(self) -> int:
        return self._one("SELECT COUNT(*) FROM entry")[0]
