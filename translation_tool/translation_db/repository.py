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

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from translation_tool.translation_db.models import (
    EntryDetail,
    EntryRow,
    HistoryRow,
    Impact,
    IngestStats,
    SameKeyRow,
    SameTextRow,
    ScanItem,
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
                self._conn.commit()

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
        """資料庫中出現過的遊戲版本（條目多者在前）。"""
        return [
            r[0]
            for r in self._q(
                "SELECT mc_version FROM entry GROUP BY mc_version ORDER BY COUNT(*) DESC"
            )
        ]

    def mods(self, version: str) -> list[str]:
        return [
            r[0]
            for r in self._q(
                "SELECT DISTINCT mod_id FROM entry WHERE mc_version=? ORDER BY mod_id",
                (version,),
            )
        ]

    def version_stats(self) -> list[VersionStat]:
        rows = self._q(
            """
            SELECT e.mc_version,
                   COUNT(*),
                   SUM(f.source = ?),
                   SUM(f.source IN (?, 3, 4, 5)),
                   SUM(f.source = ?),
                   SUM(f.source = ?),
                   SUM(f.entry_id IS NULL)
            FROM entry e LEFT JOIN effective f ON f.entry_id = e.id
            GROUP BY e.mc_version ORDER BY COUNT(*) DESC
            """,
            (SRC_MANUAL, SRC_JAR_TW, SRC_JAR_CN, SRC_AI),
        )
        return [
            VersionStat(v, t, m or 0, j or 0, c or 0, a or 0, u or 0)
            for v, t, m, j, c, a, u in rows
        ]

    def overview(self) -> dict:
        """總覽頁的全域數字。"""
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
        """缺譯最多的模組。"""
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
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[EntryRow], int]:
        """條目清單（含總筆數）。state：all / none / diff / manual / ok。"""
        where = ["e.mc_version = ?"]
        params: list = [version]
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
        elif state == "manual":
            where.append(f"f.source = {SRC_MANUAL} AND NOT {self._DIFF_SQL}")
        elif state == "ok":
            where.append(
                f"f.entry_id IS NOT NULL AND f.source <> {SRC_MANUAL} AND NOT {self._DIFF_SQL}"
            )
        cond = " AND ".join(where)
        base = f"FROM entry e LEFT JOIN effective f ON f.entry_id = e.id WHERE {cond}"
        total = self._one(f"SELECT COUNT(*) {base}", params)[0]
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
                    "SELECT mc_version FROM entry WHERE kind=? AND mod_id=? AND key=? "
                    "AND en_us=? ORDER BY mc_version",
                    (entry.kind, entry.mod_id, entry.key, entry.en_us),
                )
            ]
        )
        detail.same_key = [
            SameKeyRow(eid, ver, en, en == entry.en_us, tw or "", src)
            for eid, ver, en, tw, src in self._q(
                "SELECT e.id, e.mc_version, e.en_us, f.zh_tw, f.source "
                "FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
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
        """與此條目「相同內容」的條目（含自己）。原文未知的條目沒有可比對的內容，只有自己。"""
        known = conn.execute(
            "SELECT en_us FROM entry WHERE id = ?", (entry_id,)
        ).fetchone()
        if known is None or known[0] == "":
            return conn.execute(
                "SELECT e.id, e.mc_version, f.zh_tw, f.source FROM entry e "
                "LEFT JOIN effective f ON f.entry_id = e.id WHERE e.id = ?",
                (entry_id,),
            ).fetchall()
        return conn.execute(
            "SELECT e.id, e.mc_version, f.zh_tw, f.source FROM entry e "
            "LEFT JOIN effective f ON f.entry_id = e.id "
            "WHERE (e.kind, e.mod_id, e.key, e.en_us) = "
            "(SELECT kind, mod_id, key, en_us FROM entry WHERE id = ?) "
            "ORDER BY e.mc_version",
            (entry_id,),
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

    # --------------------------------------------------------- 批次機翻（資料庫內）
    def _untranslated_where(self, mod_ids: Sequence[str] | None) -> tuple[str, list]:
        where = "e.mc_version = ? AND f.entry_id IS NULL AND e.en_us <> ''"
        params: list = []
        if mod_ids:
            where += f" AND e.mod_id IN ({','.join('?' * len(mod_ids))})"
            params += list(mod_ids)
        return where, params

    def count_untranslated(
        self, version: str, mod_ids: Sequence[str] | None = None
    ) -> int:
        """某版本「有原文、沒有任何譯文」的條目數（可限定模組）。"""
        where, extra = self._untranslated_where(mod_ids)
        return self._one(
            "SELECT COUNT(*) FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
            f"WHERE {where}",
            [version, *extra],
        )[0]

    def untranslated_entries(
        self,
        version: str,
        mod_ids: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[tuple[int, str, str, str, str]]:
        """未翻譯條目 ``(id, 類型, 模組, 鍵值, 原文)``；順序固定，重跑會接續同一批。"""
        where, extra = self._untranslated_where(mod_ids)
        sql = (
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us "
            "FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
            f"WHERE {where} ORDER BY e.mod_id, e.kind, e.key"
        )
        params: list = [version, *extra]
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        return self._q(sql, params)

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
                f"WHERE {where} ORDER BY e.id, f2.rowid",
                [version, *extra],
            ).fetchall()
            seen: set[int] = set()
            touched: list[int] = []
            for eid, source, zh_tw in rows:
                if eid in seen:
                    continue
                seen.add(eid)
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
