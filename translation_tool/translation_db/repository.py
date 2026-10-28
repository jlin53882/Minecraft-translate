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
import heapq
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from translation_tool.translation_db import (
    change_tracker,
    effective_columns,
    entry_activity,
    option_facets,
    parallel_scan,
    quality_flags,
    stat_summary,
)
from translation_tool.translation_db.bulk_review import BulkReviewMixin
from translation_tool.translation_db.cleanup import CleanupMixin
from translation_tool.translation_db.duplicate_cleanup import DuplicateCleanupMixin
from translation_tool.translation_db.history_retention import (
    HISTORY_RETENTION_LIMIT,
    prune_history_for_entries,
)
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
    QualityFilter,
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
    format_quality_comparison,
    format_tokens,
    matches_quality,
    quality_state,
    repair_input_issues_for_kind,
    repair_output_issues_for_kind,
    whitespace_note,
)
from translation_tool.translation_db.read_cache import READ_CACHE
from translation_tool.translation_db.scan_ingest import (
    DbIngestTarget,
    PreviewIngestTarget,
    group_scan_items,
    ingest_groups,
)
from translation_tool.translation_db.schema import (
    DB_EMPTY,
    DB_FOREIGN,
    DB_NEWER,
    DEFAULT_PRIORITY,
    KEEP_REVIEW_FLAG,
    KIND_GUIDE,
    KIND_PATCHOULI,
    SRC_AI,
    SRC_AI_REPAIR,
    SRC_JAR_CN,
    SRC_MANUAL,
    classify_database,
    connect,
    init_schema,
    rank_sql,
)
from translation_tool.translation_db.smart_review import SmartReviewMixin
from translation_tool.translation_db.source_catalog import SourceCatalog
from translation_tool.translation_db.sql_helpers import (
    effective_review_sql,
    like_pattern,
    translation_review_sql,
)
from translation_tool.utils.cache_registry import register_cache
from translation_tool.utils.cancellation import (
    TaskCancelled,
    is_cancelled,
    raise_if_cancelled,
)
from translation_tool.utils.species_names import SPECIES_KEY_HINTS, TAXONOMIC_MARKERS
from translation_tool.utils.text_matching import (
    matching_terms,
    term_canonical_key,
)

_ENTRY_COLS = "e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us"
_CHUNK = 400  # SQLite 變數上限相容的批次大小
# 條目最近一次人工活動的時間（逐筆彙總歷史；entry_activity 是它的觸發器維護版本）
_VERSION_CONDITION = "e.mc_version = ?"  # _filter_sql 的第一個條件
_ENTRY_COLUMN_RE = re.compile(r"\be\.")  # 條件是否用到條目欄位
_LAST_ACTIVITY_SQL = (
    "(SELECT MAX(h.at) FROM history h WHERE h.entry_id=e.id AND "
    f"h.action IN {entry_activity.ACTIONS_SQL})"
)
_EFFECTIVE_PRIORITY_VERSION = "2"
_QUALITY_FETCH_BATCH_SIZE = 4_000
# quality.py 的 token 掃描只會在含 § % $ { \ 或換行的文字出現；GLOB 字元集是其超集，
# 用來在 SQL 先排除「必定沒有格式 token」的列（Python 仍負責最終判斷）。
_TOKEN_CHAR_GLOB_SQL = r"'*[§%${\]*'"

_QUALITY_PAGE_BUFFER_LIMIT = 10_000
_BATCH_REPLACE_STRIP_CHARS = "".join(
    chr(codepoint)
    for codepoint in (
        *range(0x09, 0x0E),
        *range(0x1C, 0x21),
        0x85,
        0xA0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)


class _WorstFirstQualityRow:
    """Heap wrapper that keeps the last row in the requested display order first."""

    __slots__ = ("date", "date_index", "entry_id", "key", "row", "sort_by")

    def __init__(self, row: tuple, sort_by: str) -> None:
        self.row = row
        self.sort_by = sort_by
        self.date_index = {
            "entry_newest": 11,
            "entry_oldest": 11,
            "effective_updated_newest": 13,
            "effective_updated_oldest": 13,
            "manual_activity_newest": 14,
            "manual_activity_oldest": 14,
        }.get(sort_by)
        if self.date_index is None:
            self.date = None
            self.entry_id = row[0]
            self.key = (row[3], row[1], row[4], row[0])
        else:
            self.date = row[self.date_index]
            self.entry_id = row[0]
            self.key = None

    def __lt__(self, other: _WorstFirstQualityRow) -> bool:
        if self.date_index is None:
            if self.sort_by == "default_desc":
                return self.key < other.key
            return self.key > other.key
        if (self.date is None) != (other.date is None):
            return self.date is None
        ascending = self.sort_by.endswith("_oldest")
        if self.date != other.date:
            return self.date > other.date if ascending else self.date < other.date
        # 由舊到新的新排序同時間時編號由大到小（與衍生表索引反向掃描的順序相同）；
        # 既有的 entry_oldest 維持編號由小到大。
        if ascending and self.sort_by != "entry_oldest":
            return self.entry_id < other.entry_id
        return self.entry_id > other.entry_id


@lru_cache(maxsize=4096)
def _cached_quality_comparison(
    source: str, before: str, after: str, kind: str | None
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[QualityIssueDelta, ...]]:
    """批次取代預覽用的格式品質比對（純函式，依輸入文字快取）。"""
    old, new, deltas = format_quality_comparison(source, before, after, kind=kind)
    return tuple(old), tuple(new), deltas


def clear_quality_comparison_cache() -> None:
    """清除批次取代預覽的品質比對 lru_cache。"""
    _cached_quality_comparison.cache_clear()


register_cache(
    "translation_db.repository.quality_comparison", clear_quality_comparison_cache
)


def _like(text: str) -> str:
    """把使用者輸入轉成 LIKE 的字面比對（跳脫 % _ \\）。"""
    return like_pattern(text)


_REVERSIBLE_SORTS = {"default": "default_desc", "default_desc": "default"}


class TranslationDB(
    DuplicateCleanupMixin, SmartReviewMixin, BulkReviewMixin, CleanupMixin
):
    """資料庫存取物件（執行緒安全：單一連線 + 可重入鎖）。"""

    # 品質篩選翻頁用：(條件鍵, 已排序的符合列, 符合總數)，整個程式只留最近一份。
    # 篩選在背景 worker 的唯讀連線掃描、翻頁卻在主連線，所以不能放在連線物件上；
    # 條件鍵含資料庫路徑與資料世代，資料一變就自然失效。
    _quality_page_cache: tuple[tuple, list[tuple], int] | None = None
    # 連線上已有品質旗標欄位：寫入交易提交前要重算待重算的條目（見 ``quality_flags``）
    _has_quality_flags = False

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
        self._probe_lock = threading.Lock()
        self._generation_probe: sqlite3.Connection | None = None
        self._write_transaction = False
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
                self._has_quality_flags = quality_flags.has_column(self._conn)
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
            self.has_history_source = "source_id" in history_columns
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
        """回傳指定優先序使用的標準生效來源排序。"""
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
        with self._probe_lock:
            if self._generation_probe is not None:
                self._generation_probe.close()
                self._generation_probe = None
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(
        self, *, bump_generation_on_noop: bool = True, keep_review: bool = False
    ) -> Iterator[sqlite3.Connection]:
        """單一寫入交易（失敗自動回復）。

        ``keep_review`` 用在審核、修復與還原這類自己決定審核狀態與歷史快照的流程：
        交易內改寫非人工來源的譯文時，不讓觸發器依「文字變了」自動清掉審核標記。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                history_id_floor = self._conn.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM history"
                ).fetchone()[0]
                # BEGIN 之後的任何失敗（含旗標初始化）都要回復，不留開啟中的交易
                if keep_review:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, '1')",
                        (KEEP_REVIEW_FLAG,),
                    )
                # 交易內的寫入由 data_gen 負責；標記讓變更序號觸發器略過（隨交易一起消失）
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, '1')",
                    (change_tracker.TX_ACTIVE,),
                )
                changes_before = self._conn.total_changes
                self._write_transaction = True
                yield self._conn
                if self._has_quality_flags:
                    # 觸發器只把受影響的條目標成待重算；提交前依 quality.py 重算
                    quality_flags.settle(self._conn)
                prune_history_for_entries(self._conn, history_id_floor)
                if keep_review:
                    self._conn.execute(
                        "DELETE FROM meta WHERE key=?", (KEEP_REVIEW_FLAG,)
                    )
                    changes_before += 1  # 旗標的寫入與刪除不算資料變動
                changed = self._conn.total_changes > changes_before
                self._conn.execute(
                    "DELETE FROM meta WHERE key=?", (change_tracker.TX_ACTIVE,)
                )
                # 資料有變動：遞增世代，讓統計快取（stat_cache）失效
                if bump_generation_on_noop or changed:
                    self._conn.execute(
                        "INSERT INTO meta (key, value) VALUES ('data_gen', '1') "
                        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
                    )
                self._conn.commit()
            except BaseException:
                # 提交前的任何失敗（含品質旗標重算）都回復，不留開啟中的交易
                self._conn.rollback()
                raise
            finally:
                self._write_transaction = False

    def _data_gen(self) -> str:
        row = self._one("SELECT value FROM meta WHERE key = 'data_gen'")
        return row[0] if row else "0"

    def _cache_generation(self) -> str:
        """快取世代：``data_gen`` 加上外部寫入的變更序號。

        外部 SQL 直接寫入不會遞增 ``data_gen``，但 ``change_tracker`` 的觸發器會遞增
        ``meta.ext_change_seq``，所以序號不同就不會沿用舊的持久化統計。
        沒有安裝觸發器的舊資料庫只能依 ``data_gen``。
        """
        gen = self._data_gen()
        row = self._one(
            "SELECT value FROM meta WHERE key = ?", (change_tracker.SEQ_KEY,)
        )
        return gen if row is None else f"{gen}.{row[0]}"

    def cache_generation_nowait(self) -> str:
        """與 :meth:`_cache_generation` 相同的世代值，但不等主連線的鎖，供畫面執行緒使用。

        主連線正在寫入交易或跑長查詢時，``_cache_generation`` 要等鎖，畫面執行緒就會卡住；
        這裡另用一條唯讀連線讀已提交的 ``data_gen`` 與外部寫入序號（WAL 讀者不被寫入擋住），
        只在第一次呼叫時開啟，隨 :meth:`close` 關閉。
        """
        with self._probe_lock:
            probe = self._generation_probe
            if probe is None:
                probe = self._generation_probe = sqlite3.connect(
                    f"{self.path.resolve().as_uri()}?mode=ro",
                    uri=True,
                    check_same_thread=False,
                    timeout=0.05,
                )
            rows = dict(
                probe.execute(
                    "SELECT key, value FROM meta WHERE key IN (?, ?)",
                    ("data_gen", change_tracker.SEQ_KEY),
                ).fetchall()
            )
        gen = rows.get("data_gen", "0")
        seq = rows.get(change_tracker.SEQ_KEY)
        return gen if seq is None else f"{gen}.{seq}"

    def _read_cache_identity(self) -> tuple:
        """納入檔案生命週期與 WAL 變化，包含外部 SQL 寫入。"""
        path = self.path.resolve()
        try:
            stat = path.stat()
        except OSError:
            # 檔案被改名或取代：用唯一值讓共用快取不命中，開著的連線照常查詢
            return (str(path), object())
        try:
            wal = Path(f"{path}-wal").stat()
            wal_signature = (wal.st_size, wal.st_mtime_ns)
        except FileNotFoundError:
            wal_signature = None
        return (
            str(path),
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            wal_signature,
        )

    def _cached(
        self, key: str, compute: Callable[[], Any], *, persist: bool = True
    ) -> Any:
        """昂貴統計的快取：資料世代沒變就直接回傳上次算好的結果（JSON 存在資料庫內）。

        數十萬筆條目的總覽統計要 1 秒以上；資料沒變時（切換頁籤、翻頁、重開程式）
        直接讀快取。唯讀 worker 共用有容量上限的程序內快取，不需寫入資料庫；
        資料世代、檔案身分或 WAL 改變就不重用舊的程序內結果。交易中的計算不共用。
        """
        if self._write_transaction:
            return compute()
        try:
            gen = self._cache_generation()
            cache_key = (self._read_cache_identity(), gen, key)
            shared = READ_CACHE.get(cache_key)
            if shared is not None:
                return json.loads(shared)
            row = (
                self._one(
                    "SELECT value FROM stat_cache WHERE key = ? AND gen = ?",
                    (key, gen),
                )
                if persist
                else None
            )
            if row is not None:
                READ_CACHE.put(cache_key, row[0])
                return json.loads(row[0])
        except (sqlite3.Error, OSError):
            return compute()
        value = compute()
        if READ_CACHE.exceeds_capacity(value):
            return value  # 結果超過快取容量（含巢狀清單、超長文字）：不白做序列化，下次重算
        encoded = json.dumps(value, ensure_ascii=False)
        if self._cache_generation() == gen:
            READ_CACHE.put(cache_key, encoded)
        if persist and not self.readonly:
            self._persist_stat_cache(key, gen, encoded)
        return value

    def _cached_peek(self, key: str) -> Any | None:
        """只讀取已快取的結果（記憶體或 ``stat_cache``），沒有就回傳 ``None``，不計算。"""
        if self._write_transaction:
            return None
        try:
            gen = self._cache_generation()
            cache_key = (self._read_cache_identity(), gen, key)
            shared = READ_CACHE.get(cache_key)
            if shared is not None:
                return json.loads(shared)
            row = self._one(
                "SELECT value FROM stat_cache WHERE key = ? AND gen = ?", (key, gen)
            )
            if row is not None:
                READ_CACHE.put(cache_key, row[0])
                return json.loads(row[0])
        except (sqlite3.Error, OSError):
            return None
        return None

    def _persist_stat_cache(self, key: str, gen: str, encoded: str) -> None:
        """把統計結果寫進 ``stat_cache``；失敗時回復本次寫入，不影響呼叫端取得的結果。

        所有權：這裡只提交／回復「自己開的」交易。連線上已有別人未提交的交易
        （``in_transaction``）時不替它 commit 或 rollback，直接放棄這次持久化，
        資料世代照常由程序內快取負責。
        """
        with self._lock:
            if self._conn.in_transaction:
                return
            # 快取寫不進去就算了：不等其他連線（掃描、批次寫入）持有的寫入鎖。
            # 預設的等待時間是數秒，這個函式常在畫面執行緒的連線上被呼叫，等鎖會凍結整個畫面。
            wait_ms = self._conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self._conn.execute("PRAGMA busy_timeout = 0")
            try:
                if self._cache_generation() != gen:
                    return  # 計算期間資料又變了：不寫舊世代，也不清掉新世代的快取
                self._conn.execute("DELETE FROM stat_cache WHERE gen <> ?", (gen,))
                self._conn.execute(
                    "INSERT OR REPLACE INTO stat_cache (key, gen, value) "
                    "VALUES (?,?,?)",
                    (key, gen, encoded),
                )
                self._conn.commit()
            except sqlite3.Error:
                # 快取寫不進去（被其他連線鎖住等）不影響結果；但 DELETE 可能已開了寫入交易，
                # 不回復會一直持有寫鎖，擋住之後所有寫入。
                if self._conn.in_transaction:
                    self._conn.rollback()
            finally:
                self._conn.execute(f"PRAGMA busy_timeout = {int(wait_ms)}")

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
        rank_version = self._one(
            "SELECT value FROM meta WHERE key='effective_priority_version'"
        )
        if (
            row
            and row[0] == want
            and rank_version
            and rank_version[0] == _EFFECTIVE_PRIORITY_VERSION
        ):
            return
        with self._tx() as conn:
            self._rebuild_effective(conn)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('priority', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (want,),
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('effective_priority_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_EFFECTIVE_PRIORITY_VERSION,),
            )

    # ------------------------------------------------------------ 生效譯文
    def _rebuild_effective(self, conn: sqlite3.Connection) -> None:
        """依目前優先序重選所有條目的生效譯文。

        整批改寫：暫停統計摘要與生效列衍生欄位的逐列觸發器，最後各重算一次。只改寫
        結果不同的列（沒有譯文的條目刪除、其餘以 upsert 比對後更新），結果與清空後
        全部重新插入相同，但優先序只影響部分條目時寫入量小得多。
        """
        derived = effective_columns.has_columns(conn)
        columns = "entry_id, zh_tw, source, checker, review_status"
        values = columns
        compared = ["zh_tw", "source", "checker", "review_status"]
        if derived:
            # 一併填入版本與更新時間，暫停結束的重算就只剩比對
            columns += ", mc_version, upd"
            values += ", (SELECT mc_version FROM entry WHERE id=entry_id), updated_at"
            compared.append("upd")
        changed = (
            f"({', '.join(compared)}) IS NOT "
            f"({', '.join('excluded.' + c for c in compared)})"
        )
        assignments = ", ".join(f"{c}=excluded.{c}" for c in compared)
        with stat_summary.frozen(conn), effective_columns.frozen(conn):
            conn.execute(
                "DELETE FROM effective WHERE NOT EXISTS (SELECT 1 FROM translation t "
                "WHERE t.entry_id=effective.entry_id AND t.zh_tw <> '')"
            )
            conn.execute(
                f"""
                INSERT INTO effective ({columns})
                SELECT {values} FROM (
                    SELECT t.entry_id, t.zh_tw, t.source, t.checker, t.review_status,
                           t.updated_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY t.entry_id
                               ORDER BY {self._priority_order()}
                           ) AS rn
                    FROM translation t WHERE t.zh_tw <> ''
                ) WHERE rn = 1
                ON CONFLICT(entry_id) DO UPDATE SET {assignments} WHERE {changed}
                """
            )

    def _refresh(
        self,
        conn: sqlite3.Connection,
        entry_ids: Iterable[int],
        *,
        priority: tuple[int, ...] | None = None,
    ) -> None:
        """重新整理共用生效投影，必要時使用明確優先序。"""
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
        return self.ingest_many(version, [items], convert)[0]

    def ingest_many(
        self,
        version: str,
        batches: Sequence[Iterable[ScanItem]],
        convert: Callable[[str], str] | None = None,
    ) -> list[IngestStats]:
        """在單一交易內寫入多份掃描結果（例如多個 jar），回傳每一份各自的統計。

        一個交易只提交一次（每次提交的 WAL 寫入與檢查點是掃描的固定成本）。結果與逐份呼叫 :meth:`ingest` 相同；任何一份失敗整個交易回復。
        """
        grouped = [group_scan_items(items) for items in batches]
        touched: list[int] = []
        with self._tx() as conn:
            target = DbIngestTarget(conn, version, touched)
            results = [ingest_groups(target, groups, convert) for groups in grouped]
            self._refresh(conn, touched)
        return results

    def scan_preview_target(self, version: str) -> PreviewIngestTarget:
        """掃描預覽用的分類目標：只讀這個資料庫，分類規則與 :meth:`ingest_many` 相同。"""
        return PreviewIngestTarget(lambda sql, params: self._q(sql, params), version)

    @staticmethod
    def _content_issue_row(
        issue: dict[str, Any],
        *,
        origin: str,
        version: str,
        context: str,
        scan_run_id: int | None,
    ) -> tuple:
        """把掃描／流水線的處置資料正規化成資料庫列。"""
        return (
            scan_run_id,
            origin,
            version,
            context,
            str(issue.get("source_archive") or issue.get("source") or ""),
            str(issue.get("source_member") or issue.get("relative_path") or ""),
            str(issue.get("kind") or "content"),
            str(issue.get("status") or "pending"),
            str(issue.get("reason") or "unclassified_content"),
            str(issue.get("detail") or issue.get("output") or ""),
        )

    @classmethod
    def _insert_content_issues(
        cls,
        conn: sqlite3.Connection,
        issues: Sequence[dict[str, Any]],
        *,
        origin: str,
        version: str,
        context: str,
        scan_run_id: int | None,
    ) -> int:
        """在目前交易內寫入處置紀錄，回傳實際提供的紀錄數。"""
        rows = [
            cls._content_issue_row(
                issue,
                origin=origin,
                version=version,
                context=context,
                scan_run_id=scan_run_id,
            )
            for issue in issues
            if isinstance(issue, dict)
        ]
        if not rows:
            return 0
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO content_issue ("
            "scan_run_id, origin, mc_version, context, source_archive, "
            "source_member, kind, status, reason, detail"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        # ``INSERT OR IGNORE`` can discard duplicate rows inside the same
        # manifest/snapshot.  Return the number actually inserted so callers
        # do not report more records than the database contains.
        return conn.total_changes - before

    def record_scan(
        self,
        version: str,
        folder: str,
        stats: dict,
        *,
        issues: Sequence[dict[str, Any]] | None = None,
        replace_issues: bool = True,
    ) -> None:
        """記錄一次掃描與其內容處置（總覽頁的「最近掃描」）。

        A cancelled scan still records its run statistics, but must not replace
        a previously complete content-issue snapshot with a partial one.
        """
        with self._tx() as conn:
            # content_issue 是目前來源的處置 snapshot；scan_run 仍保留歷史統計，
            # 但同一版本／資料夾的舊問題不能在下一次成功掃描後繼續被查到。
            if replace_issues:
                conn.execute(
                    "DELETE FROM content_issue WHERE origin='scan' "
                    "AND mc_version=? AND context=?",
                    (version, folder),
                )
            cursor = conn.execute(
                "INSERT INTO scan_run (mc_version, folder, finished_at, stats) "
                "VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
                (version, folder, json.dumps(stats, ensure_ascii=False)),
            )
            if replace_issues:
                self._insert_content_issues(
                    conn,
                    issues or (),
                    origin="scan",
                    version=version,
                    context=folder,
                    scan_run_id=int(cursor.lastrowid),
                )

    def record_content_issues(
        self,
        version: str,
        context: str,
        issues: Sequence[dict[str, Any]],
        *,
        origin: str = "pipeline",
    ) -> int:
        """保存非 entry 內容的處置結果，讓流水線與掃描共用同一個查詢面。"""
        with self._tx() as conn:
            # 同一個流水線 context 代表目前這次輸出；移除舊 snapshot，避免
            # 修復後仍把已不存在的 pending／parse_failed 誤報成目前問題。
            conn.execute(
                "DELETE FROM content_issue WHERE origin=? AND mc_version=? AND context=?",
                (origin, version, context),
            )
            return self._insert_content_issues(
                conn,
                issues,
                origin=origin,
                version=version,
                context=context,
                scan_run_id=None,
            )

    def content_issues(
        self,
        version: str | None = None,
        *,
        origin: str | None = None,
        context: str | None = None,
        statuses: Sequence[str] | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """查詢掃描／流水線留下的非 entry 內容處置紀錄。"""
        conditions: list[str] = []
        params: list[Any] = []
        if version is not None:
            conditions.append("mc_version=?")
            params.append(version)
        if origin is not None:
            conditions.append("origin=?")
            params.append(origin)
        if context is not None:
            conditions.append("context=?")
            params.append(context)
        if statuses:
            marks = ",".join("?" for _ in statuses)
            conditions.append(f"status IN ({marks})")
            params.extend(statuses)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(max(1, int(limit)))
        rows = self._q(
            "SELECT origin, mc_version, context, source_archive, source_member, "
            "kind, status, reason, detail, created_at "
            f"FROM content_issue{where} ORDER BY id DESC LIMIT ?",
            params,
        )
        keys = (
            "origin",
            "mc_version",
            "context",
            "source_archive",
            "source_member",
            "kind",
            "status",
            "reason",
            "detail",
            "created_at",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

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
        if self._stat_summary_ready():
            with self._lock:
                return [row[0] for row in stat_summary.version_totals(self._conn)]
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
        if self._stat_summary_ready():
            with self._lock:
                return stat_summary.kinds(self._conn, version)
        return [
            r[0]
            for r in self._q(
                "WITH RECURSIVE k(kind) AS (SELECT MIN(kind) FROM entry "
                "UNION ALL SELECT (SELECT MIN(kind) FROM entry WHERE kind > k.kind) "
                "FROM k WHERE kind IS NOT NULL) "
                "SELECT kind FROM k WHERE kind IS NOT NULL AND EXISTS("
                "SELECT 1 FROM entry e WHERE e.kind = k.kind AND e.mc_version = ?) "
                "ORDER BY kind",
                (version,),
            )
        ]

    def _mods_uncached(self, version: str) -> list[str]:
        if self._stat_summary_ready():
            with self._lock:
                return stat_summary.mods(self._conn, version)
        # 沿 idx_entry_list_order (mc_version, mod_id, …) 逐個跳到下一個模組：
        # 成本隨模組數而不是條目數成長，也不必暫存排序。
        rows = self._q(
            "WITH RECURSIVE m(mod_id) AS ("
            "SELECT MIN(mod_id) FROM entry WHERE mc_version=? "
            "UNION ALL SELECT (SELECT MIN(mod_id) FROM entry "
            "WHERE mc_version=? AND mod_id > m.mod_id) FROM m "
            "WHERE mod_id IS NOT NULL) "
            "SELECT mod_id FROM m WHERE mod_id IS NOT NULL",
            (version, version),
        )
        return sorted(row[0] for row in rows)

    def version_stats(self) -> list[VersionStat]:
        rows = self._cached("version_stats", self._version_stats_rows)
        return [VersionStat(*r) for r in rows]

    def _stat_summary_ready(self) -> bool:
        """全庫統計摘要可用（見 ``stat_summary``）；否則讀取端改用全庫重算。"""
        if not self.has_review_state:
            return False
        with self._lock:
            return stat_summary.is_ready(self._conn)

    def rebuild_stat_summary(self) -> None:
        """以全庫重算重建統計摘要（摘要缺少或失準時的維護操作）。"""
        if self.readonly:
            raise PermissionError("唯讀連線不能重建統計摘要")
        # 經由寫入交易遞增資料世代，讓依舊摘要算出的統計快取一併失效
        with self._tx() as conn:
            stat_summary.rebuild(conn)

    def verify_stat_summary(self) -> list[tuple]:
        """比對統計摘要與全庫重算；空清單表示一致（只供測試與維護，需要全庫掃描）。"""
        with self._lock:
            return stat_summary.verify(self._conn)

    def version_stats_from_effective_sources(
        self, source_stats: Sequence[EffectiveSourceStat]
    ) -> list[VersionStat]:
        """從已載入的來源分解資料建立版本統計。"""
        rows = self._cached(
            "version_stats",
            lambda: self._version_stats_from_source_stats(source_stats),
        )
        return [VersionStat(*row) for row in rows]

    @staticmethod
    def _version_stats_from_source_stats(
        source_stats: Sequence[EffectiveSourceStat],
    ) -> list[list]:
        totals: dict[str, list[int]] = {}
        for row in source_stats:
            counts = totals.setdefault(row.mc_version, [0, 0, 0, 0, 0, 0])
            counts[0] += row.count
            if row.source is None:
                counts[5] += row.count
            elif row.source == SRC_MANUAL:
                counts[1] += row.count
            elif row.source == SRC_JAR_CN:
                counts[3] += row.count
            elif row.source == SRC_AI:
                counts[4] += row.count
            else:
                counts[2] += row.count
        return [
            [version, *totals[version]]
            for version in sorted(totals, key=lambda key: (-totals[key][0], key))
        ]

    def effective_source_stats_by_version(self) -> list[EffectiveSourceStat]:
        """依來源與人工審核狀態各計算一次生效條目。"""
        rows = self._cached(
            "effective_source_stats_by_version",
            self._effective_source_stats_by_version_rows,
        )
        return [EffectiveSourceStat(*row) for row in rows]

    def effective_source_codes(self, version: str | None = None) -> tuple[int, ...]:
        """取得目前至少贏得一筆生效條目的來源代碼。"""
        rows = self._cached(
            f"effective_source_codes:{version!r}",
            lambda: self._effective_source_codes_uncached(version),
        )
        return tuple(int(row[0]) for row in rows)

    def _effective_source_codes_uncached(self, version: str | None) -> list[list]:
        if self._stat_summary_ready():
            with self._lock:
                return [
                    [code] for code in stat_summary.source_codes(self._conn, version)
                ]
        if version is None:
            sql, params = "SELECT DISTINCT source FROM effective ORDER BY source", ()
        else:
            sql = (
                "SELECT DISTINCT f.source FROM effective f "
                "JOIN entry e ON e.id=f.entry_id "
                "WHERE e.mc_version=? ORDER BY f.source"
            )
            params = (version,)
        return [list(row) for row in self._q(sql, params)]

    def _effective_source_stats_by_version_rows(self) -> list[list]:
        if self._stat_summary_ready():
            with self._lock:
                return stat_summary.source_rows(self._conn)
        return self._effective_source_stats_by_version_full()

    def _effective_source_stats_by_version_full(self) -> list[list]:
        # 審核狀態與生效來源各自獨立；非人工來源沒有狀態就是「未審核」
        review = effective_review_sql("f", self.has_review_state)
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
        if self._stat_summary_ready():
            with self._lock:
                source_rows = stat_summary.source_rows(self._conn)
            return self._version_stats_from_source_stats(
                [EffectiveSourceStat(*row) for row in source_rows]
            )
        return self._version_stats_full()

    def _version_stats_full(self) -> list[list]:
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
        if self._stat_summary_ready():
            with self._lock:
                counts = stat_summary.counters(self._conn)
            changed = self._one("SELECT COUNT(*) FROM src_change")[0]
            return {
                "no_source": counts["no_source"],
                "mods": counts["mods"],
                "content": counts["content"],
                "diff": counts["diff"],
                "src_changed": changed,
            }
        return self._overview_full()

    def _overview_full(self) -> dict:
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
        quality, issues, whitespace = quality_state(en_us, zh_tw, kind=r[1])
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
        """生效譯文的審核狀態：與來源各自獨立，非人工來源沒有狀態就是未審核。"""
        return effective_review_sql("f", self.has_review_state)

    # 有效譯文（``effective`` 視圖／表，已套用來源優先序）與 en_us 嚴格相等，且兩者都非空
    _SAME_AS_SOURCE_SQL = (
        "(f.entry_id IS NOT NULL AND e.en_us <> '' AND f.zh_tw <> '' "
        "AND f.zh_tw = e.en_us)"
    )

    def _same_as_source_sql(self) -> str:
        """生效譯文與原文相同的條件；品質旗標可用時讀部分索引（與原條件結果相同）。"""
        if self._quality_flags_ready():
            return f"e.qflags & {quality_flags.SAME_AS_SOURCE}"
        return self._SAME_AS_SOURCE_SQL

    def _same_as_source_entry_table(self) -> str:
        """「同原文」查詢的條目表：旗標可用時指定部分索引（規劃器不會自己選它）。"""
        if self._quality_flags_ready():
            return "entry e INDEXED BY idx_qflags_same_as_source"
        return "entry e"

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

    @staticmethod
    def _candidate_source(expressions: str) -> str:
        """只連接篩選或排序需要的資料表，再投影候選頁面。"""
        source = "FROM entry e"
        if "f." in expressions or "et." in expressions:
            source += " LEFT JOIN effective f ON f.entry_id=e.id"
        if "et." in expressions:
            source += (
                " LEFT JOIN translation et ON et.entry_id=e.id AND et.source=f.source"
            )
        return source

    def _paged_entry_rows(
        self,
        criteria: EntryFilter,
        condition: str,
        params: list,
        *,
        limit: int,
        offset: int,
        conn: sqlite3.Connection | None,
    ) -> list[tuple]:
        """先對識別碼套用篩選、排序與位移，再載入編輯欄位。"""
        phases = self._two_phase_sort_sql(criteria.sort_by, condition)
        if phases is not None:
            return self._two_phase_page_rows(
                phases, params, limit=limit, offset=offset, conn=conn
            )
        order = self._sort_sql(criteria.sort_by)
        candidates = self._candidate_source(f"{condition} {order}")
        values = [*params, max(0, int(limit)), max(0, int(offset))]
        if conn is None:
            candidate_sql = (
                f"SELECT e.id {candidates} WHERE {condition} "
                f"ORDER BY {order} LIMIT ? OFFSET ?"
            )
            cache_key = (
                "entry_page:"
                + hashlib.sha1(
                    json.dumps([candidate_sql, values]).encode("utf-8")
                ).hexdigest()
            )
            with self._lock:
                owns_transaction = not self._conn.in_transaction
                if owns_transaction:
                    self._conn.execute("BEGIN")
                try:
                    ids = self._cached(
                        cache_key,
                        lambda: self._q(candidate_sql, values),
                        persist=False,
                    )
                    if not ids:
                        return []
                    return self._q(
                        f"{self._entry_select_sql()} {self._query_source()} "
                        "WHERE e.id IN (SELECT value FROM json_each(?)) "
                        f"ORDER BY {order}",
                        (json.dumps([row[0] for row in ids]),),
                    )
                finally:
                    if owns_transaction:
                        self._conn.rollback()
        sql = (
            f"WITH page AS MATERIALIZED (SELECT e.id {candidates} "
            f"WHERE {condition} ORDER BY {order} LIMIT ? OFFSET ?) "
            f"{self._entry_select_sql()} {self._query_source()} "
            f"JOIN page ON page.id=e.id ORDER BY {order}"
        )
        return (
            self._q(sql, values)
            if conn is None
            else conn.execute(sql, values).fetchall()
        )

    def _two_phase_sort_sql(
        self, sort_by: str, condition: str
    ) -> tuple[str, str, str] | None:
        """觸發器維護的排序欄位可用時，排序拆成兩段的 SQL；否則 None（用原本的排序）。

        第一段依欄位索引取「有時間」的條目（時間新到舊、同時間依編號），第二段是
        其餘條目依編號；與原本 ``時間 IS NULL, 時間 DESC, e.id`` 的順序相同，但兩段都
        直接沿索引讀取，不必把整個版本排序。回傳（第一段分頁、第一段筆數、第二段分頁），
        三者的參數都是條件參數（分頁再加上筆數與起點）。
        """
        if not condition.startswith(_VERSION_CONDITION):
            return None
        if (
            sort_by in ("manual_activity_newest", "manual_activity_oldest")
            and self._activity_ready()
        ):
            dated, dated_count = self._indexed_page_sql(
                condition,
                "entry_activity act",
                "",
                "act.last_at DESC, act.entry_id"
                if sort_by == "manual_activity_newest"
                else "act.last_at, act.entry_id DESC",
            )
            undated = (
                f"{condition} AND NOT EXISTS "
                "(SELECT 1 FROM entry_activity act WHERE act.entry_id=e.id)"
            )
            return (
                dated,
                dated_count,
                (
                    f"SELECT e.id {self._candidate_source(undated)} WHERE {undated} "
                    "ORDER BY e.id LIMIT ? OFFSET ?"
                ),
            )
        if (
            sort_by in ("effective_updated_newest", "effective_updated_oldest")
            and self._effective_columns_ready()
        ):
            # f.upd 就是生效來源那一筆的 et.updated_at；沒有生效譯文或時間的條目在第二段
            dated, dated_count = self._indexed_page_sql(
                condition,
                "effective f",
                "f.upd IS NOT NULL",
                "f.upd DESC, f.entry_id"
                if sort_by == "effective_updated_newest"
                else "f.upd, f.entry_id DESC",
            )
            undated = f"{condition} AND f.upd IS NULL"
            return (
                dated,
                dated_count,
                (
                    f"SELECT e.id {self._candidate_source(undated)} WHERE {undated} "
                    "ORDER BY e.id LIMIT ? OFFSET ?"
                ),
            )
        return None

    @staticmethod
    def _indexed_page_sql(
        condition: str, table: str, extra: str, order: str
    ) -> tuple[str, str]:
        """從帶版本欄的衍生表（別名 ``act`` 或 ``f``）沿索引取一頁條目編號的 SQL 與筆數 SQL。

        條件開頭的條目版本改用衍生表的版本欄（觸發器維護成與條目相同），其餘條件
        需要條目或譯文欄位時才連接；條目以 ``CROSS JOIN`` 接在衍生表之後，讓查詢
        沿衍生表的排序索引讀取。
        """
        alias = table.rsplit(" ", 1)[1]
        cond = f"{alias}.mc_version = ?{condition[len(_VERSION_CONDITION) :]}"
        if extra:
            cond += f" AND {extra}"
        source = f"FROM {table}"
        if _ENTRY_COLUMN_RE.search(cond):
            source += f" CROSS JOIN entry e ON e.id={alias}.entry_id"
        if alias != "f" and ("f." in cond or "et." in cond):
            source += f" LEFT JOIN effective f ON f.entry_id={alias}.entry_id"
        if "et." in cond:
            source += (
                f" LEFT JOIN translation et ON et.entry_id={alias}.entry_id "
                "AND et.source=f.source"
            )
        return (
            (
                f"SELECT {alias}.entry_id {source} WHERE {cond} "
                f"ORDER BY {order} LIMIT ? OFFSET ?"
            ),
            f"SELECT COUNT(*) {source} WHERE {cond}",
        )

    def _two_phase_page_rows(
        self,
        phases: tuple[str, str, str],
        params: list,
        *,
        limit: int,
        offset: int,
        conn: sqlite3.Connection | None,
    ) -> list[tuple]:
        """依 :meth:`_two_phase_sort_sql` 取一頁條目編號，再讀取這些條目的完整欄位。"""
        dated_sql, dated_count_sql, undated_sql = phases
        limit, offset = max(0, int(limit)), max(0, int(offset))

        def page_ids(connection: sqlite3.Connection) -> list[int]:
            ids = [
                row[0]
                for row in connection.execute(dated_sql, (*params, limit, offset))
            ]
            if len(ids) >= limit:
                return ids
            # 第一段不足一頁：第二段從頭接上；第一段整頁落空時扣掉第一段的總筆數
            undated_offset = 0
            if not ids:
                dated_total = connection.execute(dated_count_sql, params).fetchone()[0]
                undated_offset = max(0, offset - dated_total)
            return ids + [
                row[0]
                for row in connection.execute(
                    undated_sql, (*params, limit - len(ids), undated_offset)
                )
            ]

        def load(connection: sqlite3.Connection, ids: list[int]) -> list[tuple]:
            if not ids:
                return []
            rows = connection.execute(
                f"{self._entry_select_sql()} {self._query_source()} "
                "WHERE e.id IN (SELECT value FROM json_each(?))",
                (json.dumps(ids),),
            ).fetchall()
            by_id = {row[0]: row for row in rows}
            return [by_id[entry_id] for entry_id in ids if entry_id in by_id]

        if conn is not None:
            return load(conn, page_ids(conn))
        cache_key = (
            "entry_page:"
            + hashlib.sha1(
                json.dumps([*phases, params, limit, offset]).encode("utf-8")
            ).hexdigest()
        )
        with self._lock:
            owns_transaction = not self._conn.in_transaction
            if owns_transaction:
                self._conn.execute("BEGIN")
            try:
                ids = self._cached(
                    cache_key, lambda: page_ids(self._conn), persist=False
                )
                return load(self._conn, ids)
            finally:
                if owns_transaction:
                    self._conn.rollback()

    def _effective_columns_ready(self) -> bool:
        """``effective`` 的衍生欄位可用；否則狀態篩選與排序改用原本的查詢。"""
        with self._lock:
            return effective_columns.is_ready(self._conn)

    def rebuild_effective_columns(self) -> None:
        """全庫重算 ``effective`` 的衍生欄位（缺少或失準時的維護操作）。"""
        if self.readonly:
            raise PermissionError("唯讀連線不能重建生效譯文的衍生欄位")
        with self._tx() as conn:
            effective_columns.rebuild(conn)

    def verify_effective_columns(self) -> list[tuple]:
        """比對 ``effective`` 的衍生欄位與原本定義的重算；空清單表示一致（只供測試與維護）。"""
        with self._lock:
            return effective_columns.verify(self._conn)

    def _count_sql(self, criteria: EntryFilter, condition: str) -> str:
        """條目清單的筆數查詢。

        狀態篩選要求有生效譯文且衍生欄位可用時，從 ``effective`` 的版本索引計數，
        條件用到條目欄位時才連接條目；結果與從條目出發計數相同。
        """
        if (
            criteria.state in ("diff", "manual", "ok")
            and condition.startswith(_VERSION_CONDITION)
            and self._effective_columns_ready()
        ):
            return self._indexed_page_sql(condition, "effective f", "", "f.entry_id")[1]
        return f"SELECT COUNT(*) {self._candidate_source(condition)} WHERE {condition}"

    def _activity_ready(self) -> bool:
        """``entry_activity`` 可用於排序；否則改用逐筆彙總歷史的原本排序。"""
        with self._lock:
            return entry_activity.is_ready(self._conn)

    def rebuild_entry_activity(self) -> None:
        """由歷史重建 ``entry_activity``（缺少或失準時的維護操作）。"""
        if self.readonly:
            raise PermissionError("唯讀連線不能重建最近人工活動")
        with self._tx() as conn:
            entry_activity.rebuild(conn)

    def verify_entry_activity(self) -> list[tuple]:
        """比對 ``entry_activity`` 與由歷史重算的結果；空清單表示一致（只供測試與維護）。"""
        with self._lock:
            return entry_activity.verify(self._conn)

    def _quality_flags_ready(self, conn: sqlite3.Connection | None = None) -> bool:
        """品質旗標可直接篩選（見 ``quality_flags.is_ready``）；否則改用逐列判斷。"""
        with self._lock:
            return quality_flags.is_ready(conn or self._conn)

    def rebuild_quality_flags(self) -> None:
        """全部重算條目品質旗標（缺少或失準時的維護操作）。"""
        if self.readonly:
            raise PermissionError("唯讀連線不能重建品質旗標")
        with self._tx() as conn:
            quality_flags.rebuild(conn)
        self._has_quality_flags = True

    def verify_quality_flags(self) -> list[tuple]:
        """比對品質旗標與逐一判斷的重算；空清單表示一致（只供測試與維護）。"""
        with self._lock:
            return quality_flags.verify(self._conn)

    def _filter_sql(self, criteria: EntryFilter) -> tuple[str, list]:
        where = [_VERSION_CONDITION]
        params: list = [criteria.version]
        if criteria.entry_ids is not None:
            where.append("e.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(criteria.entry_ids))
        if criteria.include_ids is not None:
            where.append("e.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(criteria.include_ids))
        if criteria.smart_review is not None:
            where.append("e.id IN (SELECT value FROM json_each(?))")
            params.append(
                json.dumps(self.smart_review_entry_ids(criteria.smart_review))
            )
        if criteria.exclude_ids:
            where.append("e.id NOT IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(criteria.exclude_ids))
        if criteria.source is not None:
            where.append("f.source = ?")
            params.append(int(criteria.source))
        if criteria.review_status is not None:
            if criteria.review_status == "reviewed":
                # 舊資料（歷史狀態待確認）畫面上同樣顯示「人工已審核」，篩選一併納入
                where.append(
                    f"{self._effective_review_sql} IN ('reviewed', 'legacy_unknown')"
                )
            else:
                where.append(f"{self._effective_review_sql} = ?")
                params.append(criteria.review_status)
        if criteria.mod_id:
            where.append("e.mod_id = ?")
            params.append(criteria.mod_id)
        if criteria.kind:
            where.append("e.kind = ?")
            params.append(criteria.kind)
        if criteria.query.strip():
            like = _like(criteria.query.strip())
            query_columns = {
                "source": "e.en_us",
                "translation": "f.zh_tw",
                "key": "e.key",
            }
            column = query_columns.get(criteria.query_field)
            if column is None:
                where.append(
                    "(e.en_us LIKE ? ESCAPE '\\' OR e.key LIKE ? ESCAPE '\\' "
                    "OR f.zh_tw LIKE ? ESCAPE '\\')"
                )
                params += [like, like, like]
            else:
                where.append(f"{column} LIKE ? ESCAPE '\\'")
                params.append(like)

        if criteria.state == "none":
            where.append("f.entry_id IS NULL")
        elif (
            criteria.state in ("diff", "manual", "ok")
            and self._effective_columns_ready()
        ):
            # 觸發器維護的 f.diff 與下方的關聯子查詢定義相同
            where.append(
                {
                    "diff": "f.diff = 1",
                    "manual": f"f.source={SRC_MANUAL} AND f.diff = 0",
                    "ok": f"f.source<>{SRC_MANUAL} AND f.diff = 0",
                }[criteria.state]
            )
        elif criteria.state == "diff":
            where.append(f"f.entry_id IS NOT NULL AND {self._DIFF_SQL}")
        elif criteria.state == "changed":
            # 從幾百筆的 src_change 出發逐筆查 entry 唯一索引；寫成 EXISTS 會先掃過
            # 整個版本的 entry（約 0.3 秒），所以固定連接順序與索引。
            where.append(
                "e.id IN (SELECT e2.id FROM src_change sc CROSS JOIN entry e2 "
                "INDEXED BY sqlite_autoindex_entry_1 "
                "ON e2.kind=sc.kind AND e2.mc_version=sc.mc_version "
                "AND e2.mod_id=sc.mod_id AND e2.key=sc.key "
                "WHERE sc.mc_version=?)"
            )
            params.append(criteria.version)
        elif criteria.state == "manual":
            where.append(f"f.source={SRC_MANUAL} AND NOT {self._DIFF_SQL}")
        elif criteria.state == "ok":
            where.append(
                f"f.entry_id IS NOT NULL AND f.source<>{SRC_MANUAL} AND NOT {self._DIFF_SQL}"
            )
        elif criteria.state == "same":
            where.append(self._same_as_source_sql())

        if criteria.time is not None:
            time_where, time_params = self._time_filter_sql(criteria.time)
            where.extend(time_where)
            params.extend(time_params)
        return " AND ".join(where), params

    def filter_option_values(
        self, criteria: EntryFilter
    ) -> tuple[list[str], tuple[int, ...]]:
        """取得目前條件下可用的模組與生效來源選項。

        模組選項會套用目前選取的來源，來源選項會套用目前選取的模組；
        兩者本身的條件則會暫時移除，避免下拉選單因為目前選值而把自己
        隱藏。品質篩選沒有等價的純 SQL 結果時，沿用既有品質掃描器，
        因此選項與條目清單使用完全相同的篩選語意。
        """
        option_criteria = replace(criteria, mod_id=None, source=None)
        facets = self._option_facets(option_criteria)
        if facets is not None:
            return option_facets.options(facets, criteria.mod_id, criteria.source)
        if option_criteria.quality.active:
            condition, params = self._filter_sql(option_criteria)
            flags_ready = self._quality_flags_ready()
            exact = (
                flags_ready and quality_flags.is_exact(option_criteria.quality)
            ) or option_criteria.quality.status in {
                "unknown_source",
                "missing_translation",
                "whitespace",
            }
            if exact:
                prefilter = self._quality_prefilter_sql(
                    option_criteria.quality, flags_ready
                )
                condition += f" AND ({prefilter})"
                pair_rows = self._scope_pairs(
                    option_criteria.version,
                    self._candidate_source(f"{condition} f.source"),
                    condition,
                    params,
                )
            else:
                pair_rows = self._cached_quality_option_pairs(option_criteria)
        else:
            condition, params = self._filter_sql(option_criteria)
            source = self._candidate_source(f"{condition} f.source")
            pair_rows = self._q(
                f"SELECT DISTINCT e.mod_id, f.source {source} WHERE {condition}",
                params,
            )

        selected_source = criteria.source
        selected_mod = criteria.mod_id
        mods = sorted(
            {
                str(mod_id)
                for mod_id, source_code in pair_rows
                if selected_source is None or source_code == selected_source
            }
        )

        sources = tuple(
            sorted(
                {
                    int(source_code)
                    for mod_id, source_code in pair_rows
                    if source_code is not None
                    and (selected_mod is None or mod_id == selected_mod)
                }
            )
        )
        return mods, sources

    def _facet_key(self, kind: str, *parts: Any) -> str:
        digest = hashlib.sha1(
            json.dumps(parts, ensure_ascii=False, default=repr).encode("utf-8")
        ).hexdigest()
        return f"{kind}:{digest}"

    def _facet_query(
        self, criteria: EntryFilter
    ) -> tuple[str, str, list, list[int], str | None] | None:
        """分面的（快取鍵, SQL, 參數, 來源代碼, 切段掃描用 SQL）；篩選含品質條件時沒有分面（``None``）。

        ``criteria`` 的模組與來源選擇不影響分面，所以這兩個欄位一律清掉。
        切段 SQL 只在搜尋條件（逐列 ``LIKE``，無法用索引）的掃描分面才有，其餘為 ``None``。
        """
        if criteria.quality.active:
            return None
        base = replace(criteria, mod_id=None, source=None, sort_by="default")
        codes = list(self.effective_source_codes(base.version))
        if option_facets.summary_eligible(base) and self._stat_summary_ready():
            sql, params = option_facets.summary_sql(base, codes)
            return (
                self._facet_key("facets-summary", sql, params),
                sql,
                params,
                codes,
                None,
            )
        condition, params = self._filter_sql(base)
        source = self._candidate_source(f"{condition} f.source")
        sql = option_facets.scan_sql(source, condition, codes)
        ranged = (
            option_facets.scan_sql(source, condition, codes, ranged=True)
            if base.query.strip()
            else None
        )
        params = [*codes, *params]
        return (
            self._facet_key("facets-scan", sql, params),
            sql,
            params,
            codes,
            ranged,
        )

    def _facet_rows(
        self,
        criteria: EntryFilter,
        sql: str,
        params: list,
        ranged_sql: str | None,
    ) -> list[tuple]:
        """分面的原始列；搜尋的整個版本逐列比對切成編號區間，用多條唯讀連線同時掃。"""
        # 寫入交易中的變更其他連線看不到：交易內一律在本連線掃描
        if ranged_sql is not None and not self._write_transaction:
            bounds = self._one(
                "SELECT MIN(e.id), MAX(e.id) FROM entry e WHERE e.mc_version = ?",
                (criteria.version,),
            )
            if bounds and bounds[0] is not None:
                workers = parallel_scan.worker_count(bounds[1] - bounds[0] + 1)
                if workers > 1:
                    ranges = parallel_scan.split_ranges(bounds[0], bounds[1], workers)
                    rows = parallel_scan.scan(self.path, ranged_sql, params, ranges)
                    return option_facets.merge_rows(rows)
        return self._q(sql, params)

    def _option_facets(
        self,
        criteria: EntryFilter,
        *,
        compute: bool = True,
    ) -> option_facets.Facets | None:
        """目前篩選條件的「模組 × 來源」分面（依資料世代快取）；不適用時回傳 ``None``。

        ``compute=False`` 時，掃描版分面只在已快取時回傳（摘要版本來就是毫秒級，照常計算）。
        """
        query = self._facet_query(criteria)
        if query is None:
            return None
        key, sql, params, codes, ranged_sql = query
        with self._lock:
            if compute or key.startswith("facets-summary"):
                facets = self._cached(
                    key,
                    lambda: option_facets.build(
                        self._facet_rows(criteria, sql, params, ranged_sql), codes
                    ),
                    persist=False,
                )
            else:
                facets = self._cached_peek(key)
        return facets if facets is not None and facets["complete"] else None

    def _scope_pairs(
        self,
        version: str | None,
        candidate_source: str,
        condition: str,
        params: Sequence[Any],
    ) -> list[tuple[str, int | None]]:
        """符合條件的條目所涉及的（模組, 生效來源）配對，依模組、來源排序（無生效譯文者來源為 ``None``）。

        供範圍下拉選單使用；以一次串流彙總取代 ``SELECT DISTINCT`` 加排序，並依資料世代快取。
        ``candidate_source`` 必須已連接 ``condition`` 用到的資料表，且別名為 ``e``、``f``。
        """
        codes = list(self.effective_source_codes(version))
        sql = option_facets.scan_sql(candidate_source, condition, codes)
        args = [*codes, *params]
        with self._lock:
            facets = self._cached(
                self._facet_key("scope-pairs", sql, args),
                lambda: option_facets.build(self._q(sql, args), codes),
                persist=False,
            )
        return option_facets.pairs(facets)

    def _facet_total(
        self, criteria: EntryFilter, *, compute: bool = False
    ) -> int | None:
        """分面已算過（或由摘要即時算出）時的符合總筆數；否則 ``None``，由呼叫端另行計數。

        ``compute=True`` 時沒有快取就掃描計算（結果同時是下拉選單的選項，之後直接命中快取）。
        """
        facets = self._option_facets(criteria, compute=compute)
        if facets is None:
            return None
        return option_facets.total(facets, criteria.mod_id, criteria.source)

    def _cached_quality_option_pairs(
        self, criteria: EntryFilter
    ) -> set[tuple[str, int | None]]:
        """:meth:`_quality_option_pairs` 的結果依資料世代快取（逐列判斷品質的掃描很貴）。"""
        condition, params = self._filter_sql(criteria)
        key = self._facet_key(
            "quality-option-pairs", condition, params, criteria.quality
        )
        pairs = self._cached(
            key,
            lambda: [list(pair) for pair in self._quality_option_pairs(criteria)],
            persist=False,
        )
        return {(str(mod_id), source) for mod_id, source in pairs}

    def _quality_option_pairs(
        self, criteria: EntryFilter
    ) -> set[tuple[str, int | None]]:
        """以窄投影掃描非精確品質條件，只保留選項所需的唯一配對。

        品質條件的方向或 token 類別不是「全部」時，不能完全交給 qflags
        索引判斷，仍須逐列呼叫 ``matches_quality``。這裡只讀取判斷所需欄位，
        並直接累積模組／來源配對，避免把完整條目列全部緩衝到記憶體。
        """
        condition, params = self._filter_sql(criteria)
        prefilter = self._quality_prefilter_sql(
            criteria.quality, self._quality_flags_ready()
        )
        pairs: set[tuple[str, int | None]] = set()
        with self._lock:
            owns_transaction = not self._conn.in_transaction
            if owns_transaction:
                self._conn.execute("BEGIN")
            self._conn.set_progress_handler(lambda: int(is_cancelled()), 10_000)
            cursor = None
            try:
                cursor = self._conn.execute(
                    "SELECT e.kind, e.mod_id, e.key, e.en_us, f.zh_tw, f.source "
                    f"{self._query_source()} WHERE ({condition}) AND {prefilter}",
                    tuple(params),
                )
                while chunk := cursor.fetchmany(_QUALITY_FETCH_BATCH_SIZE):
                    raise_if_cancelled()
                    for kind, mod_id, key, en_us, zh_tw, source in chunk:
                        if matches_quality(
                            en_us or "",
                            zh_tw or "",
                            criteria.quality,
                            key,
                            kind=kind,
                        ):
                            pairs.add((str(mod_id), source))
            except sqlite3.OperationalError:
                if is_cancelled():
                    raise TaskCancelled() from None
                raise
            finally:
                if cursor is not None:
                    cursor.close()
                self._conn.set_progress_handler(None, 0)
                if owns_transaction and self._conn.in_transaction:
                    self._conn.rollback()
        return pairs

    def _time_filter_sql(self, spec: TimeFilter) -> tuple[list[str], list]:
        where: list[str] = []
        params: list = []
        if spec.kind == "manual_activity":
            actions_by_kind = {
                "all": entry_activity.ACTIVITY_ACTIONS,
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

    def time_data_range(self, criteria: EntryFilter) -> tuple[str | None, str | None]:
        """其他條件不變、只拿掉時間條件後，所選時間依據實際有資料的最早／最晚時間（UTC）。

        只在時間篩選查無資料時，用來告訴使用者資料實際落在哪段期間；沒有任何時間資料回傳 (None, None)。
        """
        spec = criteria.time
        if spec is None:
            return None, None
        without_time = replace(criteria, time=None)
        condition, params = self._filter_sql(without_time)
        key = self._facet_key("time_range", spec, condition, params)
        low, high = self._cached(
            key,
            lambda: self._time_data_range_uncached(spec, condition, params),
            persist=False,
        )
        return low, high

    def _time_data_range_uncached(
        self, spec: TimeFilter, condition: str, params: list
    ) -> list[str | None]:
        if (
            spec.kind == "effective_updated"
            and condition == _VERSION_CONDITION
            and self._effective_columns_ready()
        ):
            # 沒有其他條件時，f.upd（與 et.updated_at 相同）由 idx_eff_ver_upd 直接給最早／最晚
            row = self._one(
                "SELECT MIN(upd), MAX(upd) FROM effective WHERE mc_version=?",
                params,
            )
            return [row[0], row[1]] if row else [None, None]
        if spec.kind == "manual_activity":
            actions = {
                "all": entry_activity.ACTIVITY_ACTIONS,
                "edit": ("manual",),
                "review": ("review",),
                "batch": ("batch_replace", "batch_revert"),
                "revert": ("revert", "batch_revert"),
            }[spec.action]
            marks = ",".join("?" for _ in actions)
            # 從歷史紀錄（筆數遠少於條目）出發，再用主鍵找條目；反過來會逐條目探測整個版本
            source = self._candidate_source(condition).replace(
                "FROM entry e",
                "FROM history h CROSS JOIN entry e ON e.id=h.entry_id",
                1,
            )
            sql = (
                f"SELECT MIN(h.at), MAX(h.at) {source} "
                f"WHERE h.action IN ({marks}) AND {condition}"
            )
            params = [*actions, *params]
        else:
            column = {
                "entry_created": "e.created_at",
                "translation_created": (
                    "et.created_at" if self.has_translation_created_at else "NULL"
                ),
            }.get(spec.kind, "et.updated_at")
            sql = (
                f"SELECT MIN({column}), MAX({column}) "
                f"{self._candidate_source(condition + ' ' + column)} WHERE {condition}"
            )
        row = self._one(sql, params)
        return [row[0], row[1]] if row else [None, None]

    def _sort_sql(self, sort_by: str) -> str:
        if sort_by == "entry_newest":
            return "e.created_at IS NULL, e.created_at DESC, e.id"
        if sort_by == "entry_oldest":
            return "e.created_at IS NULL, e.created_at, e.id"
        if sort_by == "effective_updated_newest":
            return "et.updated_at IS NULL, et.updated_at DESC, e.id"
        if sort_by == "effective_updated_oldest":
            return (
                "et.updated_at IS NULL, et.updated_at, "
                "CASE WHEN et.updated_at IS NULL THEN e.id ELSE -e.id END"
            )
        if sort_by == "manual_activity_newest":
            activity = _LAST_ACTIVITY_SQL
            return f"{activity} IS NULL, {activity} DESC, e.id"
        if sort_by == "manual_activity_oldest":
            activity = _LAST_ACTIVITY_SQL
            return (
                f"{activity} IS NULL, {activity}, "
                f"CASE WHEN {activity} IS NULL THEN e.id ELSE -e.id END"
            )
        if sort_by == "default_desc":
            # idx_entry_list_order 反向掃描，不需要暫存排序
            return "e.mod_id DESC, e.kind DESC, e.key DESC, e.id DESC"
        return "e.mod_id, e.kind, e.key, e.id"

    @staticmethod
    def _sort_filtered_rows(rows: list[tuple], sort_by: str) -> list[tuple]:
        index = {
            "entry_newest": 11,
            "entry_oldest": 11,
            "effective_updated_newest": 13,
            "effective_updated_oldest": 13,
            "manual_activity_newest": 14,
            "manual_activity_oldest": 14,
        }.get(sort_by)
        if index is None:
            return sorted(
                rows,
                key=lambda row: (row[3], row[1], row[4], row[0]),
                reverse=sort_by == "default_desc",
            )
        dated = [row for row in rows if row[index] is not None]
        undated = [row for row in rows if row[index] is None]
        newest_first = not sort_by.endswith("_oldest")
        # entry_oldest 同時間編號由小到大；其餘由舊到新同時間編號由大到小。
        dated.sort(
            key=lambda row: row[0],
            reverse=sort_by.endswith("_oldest") and sort_by != "entry_oldest",
        )
        dated.sort(key=lambda row: row[index], reverse=newest_first)
        undated.sort(key=lambda row: row[0])
        return [*dated, *undated]

    def _quality_candidate_select_sql(self, sort_by: str) -> str:
        activity = "NULL"
        if sort_by in ("manual_activity_newest", "manual_activity_oldest"):
            activity = _LAST_ACTIVITY_SQL
        return (
            "SELECT e.id, e.kind, NULL, e.mod_id, e.key, e.en_us, f.zh_tw, "
            "NULL, NULL, NULL, NULL, e.created_at, NULL, et.updated_at, "
            f"{activity} "
        )

    @staticmethod
    def _quality_prefilter_sql(
        quality: QualityFilter, flags_ready: bool = False
    ) -> str:
        """SQL 預篩：只縮小交給 ``matches_quality`` 的候選列，不改變最終結果。

        ``flags_ready`` 時用品質旗標（``quality_flags``）：方向與類別都是「全部」時
        即為完整結果，否則是「token 不一致」的超集。
        """
        flag = quality_flags.filter_sql(quality) if flags_ready else None
        if flag is not None:
            return flag
        status = "mismatch" if quality.status == "all" else quality.status
        has_text = "e.en_us <> '' AND f.zh_tw <> ''"
        if status == "unknown_source":
            return "(e.en_us IS NULL OR e.en_us = '')"
        if status == "species_name":
            # 超集預篩：key 含學名提示詞，或值含雜交／亞種／變種記號。
            key_hint = " OR ".join(
                f"lower(e.key) LIKE '%{hint}%'" for hint in sorted(SPECIES_KEY_HINTS)
            )
            marker = " OR ".join(
                f"instr(e.en_us, '{mark}') > 0" for mark in TAXONOMIC_MARKERS
            )
            return f"e.en_us <> '' AND ({key_hint} OR {marker})"
        if status == "species_format":
            # 超集預篩：去掉前後空白後首字大寫、第二字是小寫或連字號，且中間有任何
            # 空白字元（Python 會把各種空白正規化成單一空格）；正規表示式由 Python 判斷。
            ws = _BATCH_REPLACE_STRIP_CHARS
            return f"trim(e.en_us, '{ws}') GLOB '[A-Z][a-z-]*[{ws}]*'"
        if status == "missing_translation":
            return "e.en_us <> '' AND (f.zh_tw IS NULL OR f.zh_tw = '')"
        if status == "whitespace":
            # 與 Python str.strip() 相同的空白字元集：前後有任何空白即與 trim 後不同。
            return (
                "f.zh_tw <> '' AND "
                f"f.zh_tw <> trim(f.zh_tw, '{_BATCH_REPLACE_STRIP_CHARS}')"
            )
        if status == "consistent":
            return has_text
        if status == "mismatch":
            token_like = " OR ".join(
                f"{column} GLOB {_TOKEN_CHAR_GLOB_SQL} OR instr({column}, char(10)) > 0"
                for column in ("e.en_us", "f.zh_tw")
            )
            # Guide／Patchouli 的結構標記不一定含格式符號，所以整批列入候選
            structured_kinds = f"('{KIND_GUIDE}', '{KIND_PATCHOULI}')"
            return f"{has_text} AND (({token_like}) OR e.kind IN {structured_kinds})"
        return "1"

    def _quality_filtered_page(
        self, criteria: EntryFilter, *, limit: int | None, offset: int
    ) -> tuple[list[tuple], int]:
        """篩選精簡候選資料列後，再載入要求頁面的完整資料。"""
        condition, params = self._filter_sql(criteria)
        raise_if_cancelled()
        offset = max(0, int(offset))
        page_limit = None if limit is None else max(0, int(limit))
        flags_ready = self._quality_flags_ready()
        prefilter = self._quality_prefilter_sql(criteria.quality, flags_ready)
        # 這些條件就是完整結果：直接沿索引分頁，不必逐列判斷
        exact = (
            flags_ready and quality_flags.is_exact(criteria.quality)
        ) or criteria.quality.status in {
            "unknown_source",
            "missing_translation",
            "whitespace",
        }
        if exact and page_limit is not None:
            condition += f" AND ({prefilter})"
            total = self._cached_count(
                f"SELECT COUNT(*) {self._candidate_source(condition)} WHERE {condition}",
                params,
            )
            return self._paged_entry_rows(
                criteria, condition, params, limit=page_limit, offset=offset, conn=None
            ), total
        with self._lock:
            cursor = None
            owns_transaction = not self._conn.in_transaction
            if owns_transaction:
                self._conn.execute("BEGIN")
            self._conn.set_progress_handler(lambda: int(is_cancelled()), 10_000)
            try:
                cache_key = (
                    self._read_cache_identity(),
                    self._cache_generation(),
                    condition,
                    tuple(params),
                    repr(criteria.quality),
                    criteria.sort_by,
                )
                page_cache_key = (*cache_key, "quality-page", page_limit, offset)
                shared = (
                    READ_CACHE.get(page_cache_key)
                    if not self._write_transaction and page_limit is not None
                    else None
                )
                cached = TranslationDB._quality_page_cache
                if shared is not None:
                    saved = json.loads(shared)
                    selected_ids, total = saved["ids"], saved["total"]
                    selected = [(entry_id,) for entry_id in selected_ids]
                elif (
                    not self._write_transaction
                    and page_limit is not None
                    and cached
                    and cached[0] == cache_key
                ):
                    # 翻頁：條件與資料都沒變，直接用上次排好的符合清單，不重掃整個版本
                    ordered, total = cached[1], cached[2]
                    selected = ordered[offset : offset + page_limit]
                else:
                    ordered, total, complete = self._scan_quality_candidates(
                        criteria, condition, params, page_limit, offset, prefilter
                    )
                    if (
                        not self._write_transaction
                        and page_limit is not None
                        and complete
                    ):
                        TranslationDB._quality_page_cache = (
                            cache_key,
                            ordered,
                            total,
                        )
                    selected = (
                        ordered[offset : offset + page_limit]
                        if page_limit is not None
                        else ordered[offset:]
                    )
                if page_limit is None:
                    return ordered[offset:], total

                if not self._write_transaction and shared is None:
                    READ_CACHE.put(
                        page_cache_key,
                        json.dumps(
                            {"ids": [row[0] for row in selected], "total": total}
                        ),
                    )
                if not selected:
                    return [], total

                selected_ids = [row[0] for row in selected]
                full_query = (
                    f"{self._entry_select_sql()} {self._query_source()} WHERE "
                    f"({condition}) AND e.id IN (SELECT value FROM json_each(?))"
                )
                cursor = self._conn.execute(
                    full_query,
                    (*params, json.dumps(selected_ids)),
                )
                full_rows = cursor.fetchall()
                full_rows_by_id = {row[0]: row for row in full_rows}
                return [
                    full_rows_by_id[row[0]]
                    for row in selected
                    if row[0] in full_rows_by_id
                ], total
            except sqlite3.OperationalError:
                if is_cancelled():
                    raise TaskCancelled() from None
                raise
            finally:
                if cursor is not None:
                    cursor.close()
                self._conn.set_progress_handler(None, 0)
                if owns_transaction and self._conn.in_transaction:
                    self._conn.rollback()

    def _scan_quality_candidates(
        self,
        criteria: EntryFilter,
        condition: str,
        params: list,
        page_limit: int | None,
        offset: int,
        prefilter: str,
    ) -> tuple[list[tuple], int, bool]:
        """掃描符合品質條件的列，回傳（已排序的候選、符合總數、是否含全部符合列）。

        符合列太多時只保留要顯示的前 ``offset + limit`` 筆（堆積串流），此時候選不完整，
        呼叫端不能把它當成可重複使用的翻頁快取。
        """
        retain_count = None if page_limit is None else offset + page_limit
        buffered: list[tuple] = []
        retained: list[_WorstFirstQualityRow] = []
        streaming = False
        total = 0
        select = (
            self._entry_select_sql()
            if page_limit is None
            else self._quality_candidate_select_sql(criteria.sort_by)
        )
        query_source = (
            self._query_source()
            if page_limit is None
            else (
                "FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
                "LEFT JOIN translation et ON et.entry_id=e.id "
                "AND et.source=f.source"
            )
        )
        cursor = self._conn.execute(
            f"{select} {query_source} WHERE ({condition}) AND {prefilter}",
            tuple(params),
        )
        try:
            while chunk := cursor.fetchmany(_QUALITY_FETCH_BATCH_SIZE):
                raise_if_cancelled()
                for row in chunk:
                    if not matches_quality(
                        row[5] or "",
                        row[6] or "",
                        criteria.quality,
                        row[4],
                        kind=row[1],
                    ):
                        continue
                    total += 1
                    if retain_count == 0:
                        continue
                    if page_limit is None:
                        buffered.append(row)
                        continue
                    if not streaming:
                        buffered.append(row)
                        if len(buffered) > _QUALITY_PAGE_BUFFER_LIMIT:
                            retained = [
                                _WorstFirstQualityRow(row, criteria.sort_by)
                                for row in buffered
                            ]
                            heapq.heapify(retained)
                            while len(retained) > retain_count:
                                heapq.heappop(retained)
                            buffered.clear()
                            streaming = True
                    else:
                        candidate = _WorstFirstQualityRow(row, criteria.sort_by)
                        if len(retained) < retain_count:
                            heapq.heappush(retained, candidate)
                        elif retained[0] < candidate:
                            heapq.heapreplace(retained, candidate)
        finally:
            cursor.close()
        ordered = (
            [row.row for row in sorted(retained, reverse=True)]
            if streaming
            else self._sort_filtered_rows(buffered, criteria.sort_by)
        )
        complete = not streaming and retain_count != 0
        return ordered, total, complete

    def _entry_select_sql(self) -> str:
        translation_created = (
            "et.created_at" if self.has_translation_created_at else "NULL"
        )
        manual_review = translation_review_sql("man", self.has_review_state)
        manual_revision = "man.revision" if self.has_translation_revision else "NULL"
        return (
            f"SELECT {_ENTRY_COLS}, f.zh_tw, f.source, f.checker, "
            f"{self._effective_review_sql}, "
            f"CASE WHEN f.entry_id IS NULL THEN 0 ELSE {self._DIFF_SQL} END, "
            f"e.created_at, {translation_created}, et.updated_at, "
            f"{_LAST_ACTIVITY_SQL}, man.zh_tw, man.checker, "
            f"{manual_review}, {manual_revision}, man.updated_at, "
            f"{('et.revision' if self.has_translation_revision else 'NULL')} "
        )

    def _batch_replace_select_sql(self) -> str:
        """只選取批次預覽需要的欄位，保持資料列配置穩定。"""
        manual_review = translation_review_sql("man", self.has_review_state)
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
        if not has_quality and not batch_replace_projection and limit is not None:
            return self._paged_entry_rows(
                criteria, cond, params, limit=limit, offset=offset, conn=conn
            )
        if has_quality and batch_replace_projection:
            matched = self._quality_matched_batch_rows(
                criteria, cond, params, conn, progress_callback
            )
            return self._sort_filtered_rows(matched, criteria.sort_by)
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
                if matches_quality(
                    row[5] or "",
                    row[6] or "",
                    criteria.quality,
                    row[4],
                    kind=row[1],
                ):
                    filtered.append(row)
            rows = filtered
            rows = self._sort_filtered_rows(rows, criteria.sort_by)
            if limit is not None:
                rows = rows[
                    max(0, int(offset)) : max(0, int(offset)) + max(0, int(limit))
                ]
        return rows

    def _quality_matched_batch_rows(
        self,
        criteria: EntryFilter,
        cond: str,
        params: list,
        conn: sqlite3.Connection | None,
        progress_callback: Callable[[str, float], None] | None,
    ) -> list[tuple]:
        """品質篩選的批次取代範圍：先用窄查詢找出符合的 id，再只讀這些列的完整欄位。

        全版本預覽若先讀回每一列的完整投影（約 21 欄、三個 JOIN）再用 Python 篩選，
        幾乎都花在搬運最後會被丟掉的列上。這裡的結果與舊流程相同。
        """
        connection = conn or self._conn
        quality = criteria.quality
        source = self._query_source()
        narrow = (
            f"SELECT e.id, e.en_us, f.zh_tw, e.key, e.kind {source} "
            f"WHERE ({cond}) AND "
            f"{self._quality_prefilter_sql(quality, self._quality_flags_ready(conn))}"
        )
        matched_ids: list[int] = []
        connection.set_progress_handler(lambda: int(is_cancelled()), 10_000)
        try:
            cursor = connection.execute(narrow, tuple(params))
            scanned = 0
            while chunk := cursor.fetchmany(_CHUNK * 10):
                raise_if_cancelled()
                scanned += len(chunk)
                matched_ids.extend(
                    int(row[0])
                    for row in chunk
                    if matches_quality(
                        row[1] or "", row[2] or "", quality, row[3], kind=row[4]
                    )
                )
                if progress_callback is not None:
                    progress_callback(f"檢查品質條件（已檢查 {scanned:,} 筆）", 0.05)
            rows: list[tuple] = []
            select = self._batch_replace_select_sql()
            for offset in range(0, len(matched_ids), 5000):
                raise_if_cancelled()
                rows.extend(
                    connection.execute(
                        f"{select} {source} WHERE e.id IN "
                        "(SELECT value FROM json_each(?))",
                        (json.dumps(matched_ids[offset : offset + 5000]),),
                    ).fetchall()
                )
        except sqlite3.OperationalError:
            if is_cancelled():
                raise TaskCancelled() from None
            raise
        finally:
            connection.set_progress_handler(None, 0)
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
        query_field: str = "all",
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
                query_field=query_field,
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
            rows, total = self._quality_filtered_page(
                criteria, limit=limit, offset=offset
            )
        else:
            total = self._facet_total(criteria)
            if total is None and self._scans_whole_version_for_search(criteria):
                # 搜尋（整個版本逐列比對）只掃一次：同一份分面同時回答總筆數與下拉選項
                total = self._facet_total(criteria, compute=True)
            if total is None:
                cond, params = self._filter_sql(criteria)
                total = self._cached_count(self._count_sql(criteria, cond), params)
            rows = self._plain_page_rows(criteria, total, limit, offset)
        return [self._row(row) for row in rows], total

    @staticmethod
    def _scans_whole_version_for_search(criteria: EntryFilter) -> bool:
        """搜尋且沒有選模組／來源：筆數查詢本來就要掃整個版本，改由分面一次回答（指定模組時索引已縮小範圍）。"""
        return (
            bool(criteria.query.strip())
            and criteria.mod_id is None
            and criteria.source is None
        )

    def _plain_page_rows(
        self, criteria: EntryFilter, total: int, limit: int | None, offset: int
    ) -> list[tuple]:
        """非品質篩選的一頁資料列；``total`` 是同一條件的精確總數。

        - 沒有符合的列（或位移已超過總數）時不必查頁面：頁面查詢會沿排序索引逐列檢查條件，
          條件幾乎都不符合時等於掃完整個版本。
        - 預設排序的後半段改從另一端反向讀取（``default`` 與 ``default_desc`` 互為反向、
          都沿 ``idx_entry_list_order``），最後一頁不必數過前面的所有列。
        """
        offset = max(0, int(offset))
        if limit is None:
            return self._query_entry_rows(criteria, limit=limit, offset=offset)
        size = max(0, int(limit))
        if size == 0 or total <= offset:
            return []
        if criteria.sort_by in _REVERSIBLE_SORTS and offset > total // 2:
            count = min(size, total - offset)
            reverse = replace(criteria, sort_by=_REVERSIBLE_SORTS[criteria.sort_by])
            rows = self._query_entry_rows(
                reverse, limit=count, offset=total - offset - count
            )
            return rows[::-1]
        return self._query_entry_rows(criteria, limit=size, offset=offset)

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
            # 逐組 AND 再 OR：SQLite 才會對每組用 idx_entry_content 四欄索引查找。
            # 寫成 ``(kind, mod_id, key, en_us) IN ((?,?,?,?), …)`` 時查詢計畫只用到 kind，
            # 每批都掃過整個 kind 範圍（大版本上一批約 150 ms，對比約 2 ms）。
            key_match = " OR ".join(
                "(e.kind=? AND e.mod_id=? AND e.key=? AND e.en_us=?)" for _ in chunk
            )
            params = [value for key in chunk for value in key]
            sql = (
                f"{self._batch_replace_select_sql()} {self._query_source()} "
                f"WHERE ({key_match}) "
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

    def _query_batch_replace_fast_rows(
        self,
        criteria: EntryFilter,
        *,
        conn: sqlite3.Connection | None = None,
        find_text: str | None = None,
        ids_only: bool = False,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> list[tuple] | list[int]:
        """針對簡單預覽只取得範圍識別碼或實際匹配文字。"""
        cond, params = self._filter_sql(criteria)
        if ids_only:
            select = "SELECT e.id"
        else:
            select = self._batch_replace_select_sql().removesuffix(" ")
            cond += " AND f.entry_id IS NOT NULL AND f.zh_tw IS NOT NULL "
            cond += " AND f.zh_tw<>'' AND instr(f.zh_tw, ?) > 0"
            params.append(find_text or "")
        sql = f"{select} {self._query_source()} WHERE {cond}"
        connection = conn or self._conn
        result: list[tuple] | list[int] = []
        connection.set_progress_handler(lambda: int(is_cancelled()), 10_000)
        try:
            cursor = connection.execute(sql, tuple(params))
            while chunk := cursor.fetchmany(_CHUNK):
                raise_if_cancelled()
                if ids_only:
                    result.extend(int(row[0]) for row in chunk)
                else:
                    result.extend(chunk)
                if progress_callback is not None:
                    progress_callback(
                        "查詢替換範圍" if ids_only else "查詢符合文字的條目",
                        0.08 if ids_only else 0.42,
                    )
            raise_if_cancelled()
        except sqlite3.OperationalError:
            if is_cancelled():
                raise TaskCancelled() from None
            raise
        finally:
            connection.set_progress_handler(None, 0)
        return result

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
        collect_skipped: bool = False,
    ) -> BatchReplacePlan:
        raise_if_cancelled()
        if progress_callback is not None:
            progress_callback("查詢符合條目", 0.02)
        # Cross-version checks and custom Python quality filters need every
        # candidate row. The common no-propagation path only needs the root IDs
        # plus rows whose effective translation can contain the find text.
        collect_skipped = collect_skipped or propagate or criteria.quality.active
        fast_path = not collect_skipped
        if fast_path:
            root_ids = tuple(
                sorted(
                    self._query_batch_replace_fast_rows(
                        criteria,
                        conn=conn,
                        ids_only=True,
                        progress_callback=progress_callback,
                    )
                )
            )
            roots = self._query_batch_replace_fast_rows(
                criteria,
                conn=conn,
                find_text=find_text,
                progress_callback=progress_callback,
            )
        else:
            roots = self._query_entry_rows(
                criteria,
                conn=conn,
                progress_callback=progress_callback,
                batch_replace_projection=True,
            )
            root_ids = tuple(sorted(int(row[0]) for row in roots))
        # 只有譯文含尋找文字的根條目才會用到其他版本候選（見下方同步迴圈的 continue 條件）；
        # 全版本預覽時這能把要查的 key 從所有條目縮到實際會被取代的那一小部分。
        siblings = (
            self._batch_sibling_rows(
                [
                    row
                    for row in roots
                    if row[7] is not None and row[5] and find_text in (row[6] or "")
                ],
                conn=conn,
                progress_callback=progress_callback,
            )
            if propagate
            else {}
        )
        root_id_set = set(root_ids) if propagate else set()
        changes: dict[int, BatchReplaceChange] = {}
        skipped: dict[int, BatchReplaceSkipped] = {}

        def add_candidate(
            row: tuple, *, extra: bool, root_entry_id: int | None
        ) -> None:
            entry_id = int(row[0])
            old_text = row[6] or ""
            if row[7] is None or not old_text:
                if collect_skipped:
                    skipped.setdefault(
                        entry_id,
                        self._batch_skipped(row, "沒有目前生效譯文", extra=extra),
                    )
                return
            if find_text not in old_text:
                if collect_skipped:
                    skipped.setdefault(
                        entry_id,
                        self._batch_skipped(row, "找不到符合的原文片段", extra=extra),
                    )
                return
            new_text = old_text.replace(find_text, replace_text)
            if not new_text.strip():
                if collect_skipped:
                    skipped.setdefault(
                        entry_id,
                        self._batch_skipped(row, "替換後譯文為空白", extra=extra),
                    )
                return
            if new_text == old_text:
                if collect_skipped:
                    skipped.setdefault(
                        entry_id,
                        self._batch_skipped(row, "替換後內容未改變", extra=extra),
                    )
                return
            source_text = row[5] or ""
            old_issues, new_issues, quality_deltas = _cached_quality_comparison(
                source_text, old_text, new_text, row[1]
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

        ordered_changes = tuple(changes[key] for key in sorted(changes))
        ordered_skipped = tuple(skipped[key] for key in sorted(skipped))
        extra_ids: set[int] = set()
        root_changes: list[BatchReplaceChange] = []
        extra_version_count = 0
        quality_mixed_count = 0
        quality_worsened_count = 0
        for row in ordered_changes:
            if row.is_extra_version:
                extra_ids.add(row.entry_id)
                extra_version_count += 1
            else:
                root_changes.append(row)
            quality_mixed_count += row.quality_mixed
            quality_worsened_count += row.quality_worsened
        conflict_count = 0
        for row in ordered_skipped:
            if row.is_extra_version:
                extra_ids.add(row.entry_id)
                conflict_count += 1
        return BatchReplacePlan(
            database_identity=self._database_identity(),
            criteria=criteria,
            find_text=find_text,
            replace_text=replace_text,
            propagate=propagate,
            root_ids=root_ids,
            changes=ordered_changes,
            skipped=ordered_skipped,
            confirmed_quality_worsening=confirmed_quality_worsening,
            total_unique_entries=(
                len(root_id_set | extra_ids) if propagate else len(root_ids)
            ),
            extra_version_count=extra_version_count,
            extra_candidate_count=len(extra_ids),
            conflict_count=conflict_count,
            quality_mixed_count=quality_mixed_count,
            quality_worsened_count=quality_worsened_count,
            root_changes=tuple(root_changes),
            skipped_count_value=(
                None if collect_skipped else len(root_ids) - len(ordered_changes)
            ),
            skipped_details_loaded=collect_skipped,
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
        """建立不修改資料庫的完整批次取代預覽計畫。"""
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

    def load_batch_replace_skipped_page(
        self,
        plan: BatchReplacePlan,
        *,
        offset: int = 0,
        limit: int = 50,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> tuple[BatchReplaceSkipped, ...]:
        """在使用者開啟跳過檢視後載入一頁跳過理由。"""
        start = max(0, int(offset))
        size = max(1, int(limit))
        if plan.skipped_details_loaded:
            return plan.skipped[start : start + size]
        with self._lock:
            if self._database_identity() != plan.database_identity:
                raise ValueError("資料庫已切換，請重新產生批次替換預覽")
            current = self._build_batch_replace_plan(
                plan.criteria,
                plan.find_text,
                plan.replace_text,
                propagate=plan.propagate,
                confirmed_quality_worsening=plan.confirmed_quality_worsening,
            )
            if current != plan:
                raise ValueError("預覽後條目已變動，請重新產生批次替換預覽")
            cond, filter_params = self._filter_sql(plan.criteria)
            reason_sql = (
                "CASE "
                "WHEN f.entry_id IS NULL OR f.zh_tw IS NULL OR f.zh_tw='' "
                "THEN ? "
                "WHEN instr(f.zh_tw, ?) = 0 THEN ? "
                "WHEN trim(replace(f.zh_tw, ?, ?), ?) = '' THEN ? "
                "WHEN replace(f.zh_tw, ?, ?) = f.zh_tw THEN ? "
                "END"
            )
            sql = (
                "SELECT entry_id, mc_version, key, reason FROM ("
                "SELECT e.id AS entry_id, e.mc_version, e.key, "
                f"{reason_sql} AS reason {self._query_source()} WHERE {cond}"
                ") WHERE reason IS NOT NULL ORDER BY entry_id LIMIT ? OFFSET ?"
            )
            params = (
                "沒有目前生效譯文",
                plan.find_text,
                "找不到符合的原文片段",
                plan.find_text,
                plan.replace_text,
                _BATCH_REPLACE_STRIP_CHARS,
                "替換後譯文為空白",
                plan.find_text,
                plan.replace_text,
                "替換後內容未改變",
                *filter_params,
                size,
                start,
            )
            connection = self._conn
            connection.set_progress_handler(lambda: int(is_cancelled()), 10_000)
            try:
                if progress_callback is not None:
                    progress_callback("載入略過原因", 0.1)
                rows = connection.execute(sql, params).fetchall()
                raise_if_cancelled()
            except sqlite3.OperationalError:
                if is_cancelled():
                    raise TaskCancelled() from None
                raise
            finally:
                connection.set_progress_handler(None, 0)
            return tuple(
                BatchReplaceSkipped(
                    entry_id=int(row[0]),
                    mc_version=row[1],
                    key=row[2],
                    reason=row[3],
                )
                for row in rows
            )

    def execute_batch_replace(
        self,
        plan: BatchReplacePlan,
        *,
        actor: str = "",
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> BatchReplaceResult:
        """在一次 CAS 交易中套用預覽過的精確取代內容。"""
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
                collect_skipped=plan.skipped_details_loaded,
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
            self._clear_superseded_reviews(
                conn,
                [(change.entry_id, change.new_zh_tw) for change in plan.changes],
                batch,
                actor,
            )
            self._refresh(conn, [change.entry_id for change in plan.changes])
            shadowed = self._count_shadowed_replacements(conn, plan.changes)
        if progress_callback is not None:
            progress_callback("批次替換已提交", 1.0)
        return BatchReplaceResult(
            batch,
            len(plan.changes),
            plan.skipped_count,
            plan.total_unique_entries,
            shadowed,
        )

    @staticmethod
    def _count_shadowed_replacements(conn: sqlite3.Connection, changes) -> int:
        """寫入後生效譯文仍不是新譯文的條目數（來源優先序讓別的來源蓋過人工）。"""
        wanted = {int(change.entry_id): change.new_zh_tw for change in changes}
        ids = list(wanted)
        shadowed = 0
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" for _ in chunk)
            for entry_id, zh_tw in conn.execute(
                f"SELECT entry_id, zh_tw FROM effective WHERE entry_id IN ({marks})",
                chunk,
            ):
                if zh_tw != wanted[int(entry_id)]:
                    shadowed += 1
        return shadowed

    def revert_batch_replace(self, batch_id: str) -> BatchRevertResult:
        """CAS 還原仍維持目前內容的批次資料列，保留較新的編輯。"""
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
                self._restore_cleared_reviews(conn, batch_id, eid)
                reverted.append(eid)
            self._refresh(conn, reverted)
        return BatchRevertResult(len(reverted), skipped, len(reverted) + skipped)

    def get_entry(self, entry_id: int) -> EntryRow | None:
        effective_created = (
            "et.created_at" if self.has_translation_created_at else "NULL"
        )
        last_activity = _LAST_ACTIVITY_SQL
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
        translation_review = translation_review_sql(
            "translation", self.has_review_state
        )
        translation_created_sql = (
            "created_at" if self.has_translation_created_at else "NULL"
        )
        translation_revision_sql = (
            "revision" if self.has_translation_revision else "NULL"
        )
        detail.translations = [
            TranslationRow(*r)
            for r in self._q(
                "SELECT source, zh_tw, zh_cn, checker, updated_at, "
                f"{translation_review}, {translation_created_sql}, "
                f"{translation_revision_sql} FROM translation "
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
        history_source_sql = "source_id" if self.has_history_source else "NULL"
        detail.history = [
            HistoryRow(*r)
            for r in self._q(
                "SELECT id, batch, at, actor, action, old_zh_tw, new_zh_tw, note, "
                f"prev_manual, {history_review_sql}, {history_revision_sql}, "
                f"{history_source_sql} "
                f"FROM history WHERE entry_id=? ORDER BY id DESC LIMIT {HISTORY_RETENTION_LIMIT}",
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
        """回傳同時包含生效與既有人工快照的審核範圍。"""
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
        # 審核不改變生效來源：同步到其他版本、必須改寫譯文時，也寫進同一個來源
        selected_source = conn.execute(
            "SELECT source FROM effective WHERE entry_id=?", (entry_id,)
        ).fetchone()
        target_source = int(selected_source[0]) if selected_source else SRC_MANUAL
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
            for row in conn.execute(query, (target_source, *chunk)).fetchall():
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
                included = review_status != "reviewed"
                reason = "目前版本" if included else "目前版本已審核"
            elif not propagate:
                included, reason = False, "已選擇僅審核目前版本"
            elif (review_status == "reviewed" and text == expected_zh_tw) or (
                row[9] == "reviewed" and (row[7] or "") == expected_zh_tw
            ):
                included, reason = False, "已審核相同譯文"
            elif (
                text != expected_zh_tw
                and review_status == "reviewed"
                and source != target_source
            ):
                included, reason = False, "其他版本已有另一來源的已審核譯文"
            elif text != expected_zh_tw:
                included, reason = True, "原文相同但目前生效譯文不同"
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

    def _clear_superseded_reviews(
        self,
        conn: sqlite3.Connection,
        changes: Iterable[tuple[int, str]],
        batch: str,
        actor: str,
    ) -> None:
        """人工寫入新譯文後，其他來源上「已審核」且譯文不同的列退回未審核。

        已審核的列優先於未審核的人工譯文；不退回的話，手動儲存／批次取代成功寫入
        卻不會生效。只有退回後人工譯文真的能生效才退回：若有排在人工之前的其他來源
        （不論審核與否，譯文不同），或另有不會被退回的「視為已審核」的列，退回也換不到
        人工生效，只會白白丟掉審核標記，所以整個條目都不退回。退回的狀態以 ``review_clear`` 記錄在同一個 batch，還原人工寫入時
        一併復原（譯文與版本都沒變才復原）。
        """
        rank_of = lambda source: conn.execute(
            f"SELECT {rank_sql(self.priority, '?')}", (source,)
        ).fetchone()[0]
        manual_rank = rank_of(SRC_MANUAL)
        for entry_id, text in changes:
            others = conn.execute(
                "SELECT source, zh_tw, checker, review_status, revision "
                "FROM translation WHERE entry_id=? AND source<>? AND zh_tw<>''",
                (entry_id, SRC_MANUAL),
            ).fetchall()
            # 退回審核後人工仍贏不了（有排在人工前面、或同樣被視為已審核的其他譯文）就不退回
            if any(
                zh_tw != text
                and (
                    rank_of(source) < manual_rank
                    or (bool(checker) and status not in ("unreviewed", "reviewed"))
                )
                for source, zh_tw, checker, status, _revision in others
            ):
                continue
            for source, zh_tw, checker, status, revision in others:
                if status != "reviewed" or zh_tw == text:
                    continue
                # 有校驗者的列即使沒有狀態也會優先，所以明確標成未審核
                cleared = "unreviewed" if checker else None
                conn.execute(
                    "UPDATE translation SET review_status=? WHERE entry_id=? AND source=?",
                    (cleared, entry_id, source),
                )
                new_revision = conn.execute(
                    "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                    (entry_id, source),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, "
                    "new_zh_tw, note, prev_checker, prev_review_status, new_checker, "
                    "new_review_status, prev_revision, new_revision, source_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        entry_id,
                        batch,
                        actor,
                        "review_clear",
                        zh_tw,
                        zh_tw,
                        "人工修改了譯文，原來源的已審核標記退回未審核",
                        checker,
                        "reviewed",
                        checker,
                        cleared or "",
                        revision,
                        new_revision,
                        source,
                    ),
                )

    def _restore_cleared_reviews(
        self, conn: sqlite3.Connection, batch: str, entry_id: int
    ) -> None:
        """還原人工寫入時，把同一個 batch 退回的審核標記復原（列沒被動過才復原）。"""
        # 不寫 ORDER BY id：有排序時規劃器會改走 idx_history_batch（同一批次可有數十萬列，
        # 每個條目都讀完整批），沒有排序才用 (entry_id, action) 索引；每個條目最多數列，在 Python 排序。
        cleared = conn.execute(
            "SELECT id, source_id, new_zh_tw, prev_review_status, new_revision "
            "FROM history WHERE entry_id=? AND action='review_clear' AND batch=?",
            (entry_id, batch),
        ).fetchall()
        for hid, source, zh_tw, prev_status, new_revision in sorted(
            cleared, reverse=True
        ):
            cur = conn.execute(
                "SELECT zh_tw, revision, review_status FROM translation "
                "WHERE entry_id=? AND source=?",
                (entry_id, source),
            ).fetchone()
            if cur is None or cur[0] != zh_tw or cur[1] != new_revision:
                continue
            conn.execute(
                "UPDATE translation SET review_status=? WHERE entry_id=? AND source=?",
                (prev_status, entry_id, source),
            )
            restored = conn.execute(
                "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                (entry_id, source),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO history (entry_id, batch, action, old_zh_tw, new_zh_tw, "
                "note, prev_review_status, new_review_status, prev_revision, "
                "new_revision, source_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry_id,
                    batch + "-restore",
                    "revert",
                    zh_tw,
                    zh_tw,
                    f"還原審核標記 #{hid}",
                    cur[2] or "",
                    prev_status,
                    cur[1],
                    restored,
                    source,
                ),
            )

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
            # 人工列早就是這段文字、但生效的是別的譯文：不用改寫人工列，仍要切換生效
            changed_ids_set = {int(row[0]) for row, _prev in changed}
            switch_only = [
                (row, previous[int(row[0])])
                for row in candidates
                if int(row[0]) not in changed_ids_set
                and (row[2] or "") != text
                and previous.get(int(row[0])) is not None
            ]
            if switch_only:
                # 人工列沒有改文字，但要有新的版本：之後才不會被更早的歷史紀錄越過
                switch_revisions: dict[int, int] = {}
                for (eid, *_rest), _prev in switch_only:
                    conn.execute(
                        "UPDATE translation SET updated_at=CURRENT_TIMESTAMP "
                        "WHERE entry_id=? AND source=?",
                        (eid, SRC_MANUAL),
                    )
                    switch_revisions[int(eid)] = conn.execute(
                        "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                        (eid, SRC_MANUAL),
                    ).fetchone()[0]
                history_only = [
                    (
                        eid,
                        batch,
                        actor,
                        "manual",
                        old or "",
                        text,
                        prev[0],
                        "" if eid == entry_id else f"同步自 {ver_self}",
                        prev[1],
                        prev[2],
                        prev[1],
                        prev[2],
                        prev[3],
                        switch_revisions[int(eid)],
                    )
                    for (eid, _ver, old, _src), prev in switch_only
                ]
                conn.executemany(
                    "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, "
                    "new_zh_tw, prev_manual, note, prev_checker, prev_review_status, "
                    "new_checker, new_review_status, prev_revision, new_revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    history_only,
                )
                self._clear_superseded_reviews(
                    conn,
                    [(int(row[0]), text) for row, _prev in switch_only],
                    batch,
                    actor,
                )
                done.extend(
                    Impact(row[0], row[1], row[2] or "", row[3], row[0] == entry_id)
                    for row, _prev in switch_only
                )
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
                self._clear_superseded_reviews(
                    conn, [(int(row[0]), text) for row, _prev in changed], batch, actor
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
        """把目前生效文字確認為已審核人工翻譯。"""
        if not expected_zh_tw.strip():
            raise ValueError("沒有可審核的譯文，請先輸入並儲存")
        batch = uuid.uuid4().hex
        done: list[Impact] = []
        with self._tx(bump_generation_on_noop=False, keep_review=True) as conn:
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
            done = self._apply_review_preview(
                conn, entry_id, preview, expected_zh_tw, actor, batch
            )
            self._refresh(conn, [change.entry_id for change in done])
        return done

    def clear_manual_review(
        self,
        entry_id: int,
        *,
        expected_zh_tw: str,
        expected_source: int | None = None,
        expected_review_status: str | None = None,
        expected_checker: str | None = None,
        actor: str = "",
    ) -> bool:
        """解除目前生效譯文的人工審核標記，但保留譯文與來源。

        這是單筆、CAS 保護的狀態變更：只有畫面仍顯示同一份譯文、來源、審核者
        與已審核狀態時才會寫入，避免使用者在背景更新後清掉別人的新審核。
        """
        if not self.has_review_state:
            raise ValueError("目前資料庫版本不支援解除人工審核，請先升級資料庫")
        if not expected_zh_tw.strip():
            raise ValueError("沒有可解除審核的譯文")

        with self._tx(bump_generation_on_noop=False, keep_review=True) as conn:
            current = self._clear_review_current(
                conn,
                entry_id,
                expected_zh_tw=expected_zh_tw,
                expected_source=expected_source,
                expected_review_status=expected_review_status,
                expected_checker=expected_checker,
            )
            source = int(current[1])
            row = self._clear_review_translation(conn, entry_id, source)
            updated = self._unmark_translation(conn, entry_id, source, row)
            self._record_review_unmark(
                conn,
                entry_id,
                source,
                row,
                updated,
                current[3],
                actor,
            )
            self._refresh(conn, [entry_id])
        return True

    def _clear_review_current(
        self,
        conn: sqlite3.Connection,
        entry_id: int,
        *,
        expected_zh_tw: str,
        expected_source: int | None,
        expected_review_status: str | None,
        expected_checker: str | None,
    ):
        """Load and validate the effective row used by the review CAS."""
        review_sql = effective_review_sql("f", self.has_review_state)
        current = conn.execute(
            "SELECT f.zh_tw, f.source, f.checker, "
            f"{review_sql} FROM effective f WHERE f.entry_id=?",
            (entry_id,),
        ).fetchone()
        if current is None or current[0] != expected_zh_tw:
            raise ValueError("目前譯文或審核狀態已變動，請重新整理後再解除審核")
        if expected_source is not None and current[1] != expected_source:
            raise ValueError("目前譯文或審核狀態已變動，請重新整理後再解除審核")
        if expected_review_status is not None and current[3] != expected_review_status:
            raise ValueError("目前譯文或審核狀態已變動，請重新整理後再解除審核")
        if expected_checker is not None and (current[2] or "") != expected_checker:
            raise ValueError("目前譯文或審核狀態已變動，請重新整理後再解除審核")
        if current[3] not in ("reviewed", "legacy_unknown"):
            raise ValueError("目前譯文或審核狀態已變動，請重新整理後再解除審核")
        return current

    def _clear_review_translation(
        self, conn: sqlite3.Connection, entry_id: int, source: int
    ):
        """Load the source row and validate its review state before mutation."""
        columns = "zh_tw, checker, review_status"
        columns += ", revision" if self.has_translation_revision else ", NULL"
        row = conn.execute(
            f"SELECT {columns} FROM translation WHERE entry_id=? AND source=?",
            (entry_id, source),
        ).fetchone()
        if row is None or row[2] not in (None, "reviewed", "legacy_unknown"):
            raise ValueError("目前譯文或審核狀態已變動，請重新整理後再解除審核")
        return row

    def _unmark_translation(
        self,
        conn: sqlite3.Connection,
        entry_id: int,
        source: int,
        row,
    ):
        """Clear the review fields while retaining text and source."""
        checker = "" if source == SRC_MANUAL else row[1] or ""
        conn.execute(
            "UPDATE translation SET checker=?, review_status=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE entry_id=? AND source=?",
            (checker, "unreviewed", entry_id, source),
        )
        return conn.execute(
            "SELECT checker, review_status, "
            + ("revision" if self.has_translation_revision else "NULL")
            + " FROM translation WHERE entry_id=? AND source=?",
            (entry_id, source),
        ).fetchone()

    def _record_review_unmark(
        self,
        conn: sqlite3.Connection,
        entry_id: int,
        source: int,
        row,
        updated,
        previous_review_status: str,
        actor: str,
    ) -> None:
        """Record an auditable no-text-change review status transition."""
        conn.execute(
            "INSERT INTO history "
            "(entry_id, batch, actor, action, old_zh_tw, new_zh_tw, "
            "prev_manual, note, prev_checker, prev_review_status, "
            "new_checker, new_review_status, prev_revision, new_revision, source_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                entry_id,
                uuid.uuid4().hex,
                actor,
                "review_unmark",
                row[0] or "",
                row[0] or "",
                row[0] or "",
                "解除人工審核標記，保留原譯文與來源",
                row[1] or "",
                previous_review_status,
                updated[0] or "",
                updated[1],
                row[3],
                updated[2],
                source,
            ),
        )

    def _apply_review_preview(
        self,
        conn: sqlite3.Connection,
        entry_id: int,
        preview: Sequence[ReviewPreviewItem],
        expected_zh_tw: str,
        actor: str,
        batch: str,
    ) -> list[Impact]:
        """標記預覽包含的資料列為已審核，由呼叫端負責交易。"""
        done: list[Impact] = []
        own = next((item for item in preview if item.entry_id == entry_id), None)
        own_source = (
            int(own.source)
            if own is not None and own.source is not None
            else SRC_MANUAL
        )
        for item in preview:
            if not item.included:
                continue
            eid, ver = item.entry_id, item.mc_version
            same_text = item.text == expected_zh_tw and item.source is not None
            target = int(item.source) if same_text else own_source
            prev = conn.execute(
                "SELECT zh_tw, checker, review_status, revision FROM translation "
                "WHERE entry_id=? AND source=?",
                (eid, target),
            ).fetchone()
            if prev and prev[2] == "reviewed" and prev[0] == expected_zh_tw:
                continue
            # 人工來源記錄審核者；其他來源沿用原本的校驗欄位，改寫譯文時清空
            checker = (actor or "人工") if target == SRC_MANUAL else ""
            conn.execute(
                "INSERT INTO translation "
                "(entry_id, source, zh_tw, checker, review_status) "
                "VALUES (?,?,?,?, 'reviewed') "
                "ON CONFLICT(entry_id, source) DO UPDATE SET "
                "checker=CASE WHEN ?=1 OR translation.zh_tw IS NOT excluded.zh_tw "
                "THEN excluded.checker ELSE translation.checker END, "
                "zh_tw=excluded.zh_tw, "
                "review_status='reviewed', updated_at=CURRENT_TIMESTAMP",
                (
                    eid,
                    target,
                    expected_zh_tw,
                    checker,
                    1 if target == SRC_MANUAL else 0,
                ),
            )
            written = conn.execute(
                "SELECT checker, revision FROM translation WHERE entry_id=? AND source=?",
                (eid, target),
            ).fetchone()
            conn.execute(
                "INSERT INTO history "
                "(entry_id, batch, actor, action, old_zh_tw, new_zh_tw, "
                "prev_manual, note, prev_checker, prev_review_status, "
                "new_checker, new_review_status, prev_revision, new_revision, "
                "source_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    # 非人工來源的審核欄位原本是 NULL；用空字串記錄，還原時換回 NULL
                    (prev[2] if prev[2] is not None else "") if prev else None,
                    written[0],
                    "reviewed",
                    prev[3] if prev else None,
                    written[1],
                    target,
                ),
            )
            done.append(Impact(eid, ver, item.text, item.source, eid == entry_id))
        if not any(change.is_self for change in done):
            current = conn.execute(
                "SELECT zh_tw, review_status FROM translation "
                "WHERE entry_id=? AND source=?",
                (entry_id, own_source),
            ).fetchone()
            if (
                current is None
                or current[0] != expected_zh_tw
                or current[1] != "reviewed"
            ):
                raise ValueError("目前譯文已變動，請重新整理後再審核")
        return done

    def revert(self, history_id: int, *, whole_batch: bool = True) -> int:
        """依 history 記錄還原單筆或整批變更，並回傳實際還原的條目數。"""
        row = self._one("SELECT batch, action FROM history WHERE id=?", (history_id,))
        if row is None:
            return 0
        if row[1] in ("quality_repair", "quality_repair_review"):
            where, params = (
                ("batch = ?", [row[0]]) if whole_batch else ("id = ?", [history_id])
            )
            reverted: list[int] = []
            new_batch = uuid.uuid4().hex
            with self._tx(bump_generation_on_noop=False, keep_review=True) as conn:
                rows = conn.execute(
                    "SELECT id, entry_id, old_zh_tw, new_zh_tw, source_id, "
                    "prev_checker, prev_review_status, new_checker, "
                    "new_review_status, prev_revision, new_revision, "
                    "repair_prev_zh_tw FROM history "
                    "WHERE action IN ('quality_repair','quality_repair_review') "
                    f"AND {where} ORDER BY id DESC",
                    params,
                ).fetchall()
                for (
                    hid,
                    eid,
                    old_text,
                    new_text,
                    source_id,
                    previous_checker,
                    previous_review,
                    new_checker,
                    new_review,
                    previous_revision,
                    new_revision,
                    previous_repair_text,
                ) in rows:
                    if source_id is None:
                        continue
                    current = conn.execute(
                        "SELECT zh_tw, checker, review_status, revision FROM translation "
                        "WHERE entry_id=? AND source=?",
                        (eid, source_id),
                    ).fetchone()
                    if current is None or current[0] != new_text:
                        continue
                    if new_revision is not None and current[3] != new_revision:
                        continue
                    if current[1] != new_checker or current[2] != new_review:
                        continue
                    if source_id == SRC_AI_REPAIR and previous_revision is None:
                        conn.execute(
                            "DELETE FROM translation WHERE entry_id=? AND source=?",
                            (eid, source_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE translation SET zh_tw=?, checker=?, review_status=?, "
                            "updated_at=CURRENT_TIMESTAMP WHERE entry_id=? AND source=?",
                            (
                                previous_repair_text
                                if source_id == SRC_AI_REPAIR
                                and previous_repair_text is not None
                                else old_text,
                                previous_checker or "",
                                previous_review,
                                eid,
                                source_id,
                            ),
                        )
                    restored = conn.execute(
                        "SELECT checker, review_status, revision FROM translation "
                        "WHERE entry_id=? AND source=?",
                        (eid, source_id),
                    ).fetchone()
                    conn.execute(
                        "INSERT INTO history (entry_id, batch, action, old_zh_tw, "
                        "new_zh_tw, note, prev_checker, prev_review_status, "
                        "new_checker, new_review_status, prev_revision, new_revision, "
                        "source_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            eid,
                            new_batch,
                            "revert",
                            new_text,
                            (
                                previous_repair_text
                                if source_id == SRC_AI_REPAIR
                                and previous_repair_text is not None
                                else old_text
                            ),
                            f"還原 #{hid}",
                            current[1],
                            current[2],
                            restored[0] if restored else "",
                            restored[1] if restored else None,
                            current[3],
                            restored[2] if restored else None,
                            source_id,
                        ),
                    )
                    reverted.append(eid)
                self._refresh(conn, reverted)
            return len(reverted)
        where, params = (
            ("batch = ?", [row[0]]) if whole_batch else ("id = ?", [history_id])
        )
        reverted: list[int] = []
        new_batch = uuid.uuid4().hex
        with self._tx(bump_generation_on_noop=False, keep_review=True) as conn:
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
                row_source,
                row_batch,
                row_action,
            ) in conn.execute(
                f"SELECT id, entry_id, new_zh_tw, prev_manual, prev_checker, "
                f"prev_review_status, new_checker, new_review_status, "
                f"prev_revision, new_revision, source_id, batch, action FROM history "
                f"WHERE action IN ('manual','review','review_unmark','smart_review_apply') AND {where} "
                "ORDER BY id DESC",
                params,
            ).fetchall():
                # 審核不改變來源：history 記錄實際被改的來源，舊紀錄一律是人工
                rsrc = SRC_MANUAL if row_source is None else int(row_source)
                cur = conn.execute(
                    "SELECT zh_tw, checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (eid, rsrc),
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
                        "AND action IN ('manual','review','review_unmark','smart_review_apply','revert','batch_replace',"
                        "'batch_revert','quality_repair','quality_repair_review') LIMIT 1",
                        (eid, hid),
                    ).fetchone()
                    if later:
                        continue
                if new_review_status is not None:
                    if cur[1] != new_checker or cur[2] != new_review_status:
                        continue
                elif cur[2] not in (None, "legacy_unknown"):
                    continue  # 舊 history 沒有狀態快照，只能還原未被新版操作改動的舊列
                if (
                    row_action != "review_unmark"
                    and prev_manual is not None
                    and (prev_checker is None or prev_review_status is None)
                ):
                    continue  # 不虛構舊 checker／審核狀態，避免改變有效譯文排序
                if prev_manual is None:
                    conn.execute(
                        "DELETE FROM translation WHERE entry_id=? AND source=?",
                        (eid, rsrc),
                    )
                else:
                    restored_review_status = (
                        None
                        if row_action == "review_unmark"
                        and prev_review_status == "legacy_unknown"
                        else prev_review_status
                    )
                    conn.execute(
                        "UPDATE translation SET zh_tw=?, checker=?, review_status=?, "
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE entry_id=? AND source=?",
                        (
                            prev_manual,
                            prev_checker,
                            restored_review_status,
                            eid,
                            rsrc,
                        ),
                    )
                restored = conn.execute(
                    "SELECT checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (eid, rsrc),
                ).fetchone()
                conn.execute(
                    "INSERT INTO history (entry_id, batch, action, old_zh_tw, new_zh_tw, "
                    "note, prev_checker, prev_review_status, new_checker, new_review_status, "
                    "prev_revision, new_revision, source_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        rsrc,
                    ),
                )
                self._restore_cleared_reviews(conn, row_batch, eid)
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
        """在目前交易中讀取共用的生效來源優先序。"""
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

            repair_row = conn.execute(
                "SELECT zh_tw, checker, review_status, revision FROM translation "
                "WHERE entry_id=? AND source=?",
                (entry_id, SRC_AI_REPAIR),
            ).fetchone()
            if repair_row is not None and repair_row[0] == new_zh_tw:
                effective = conn.execute(
                    "SELECT zh_tw FROM effective WHERE entry_id=?", (entry_id,)
                ).fetchone()
                return AITranslationReplaceResult(
                    "unchanged",
                    shadowed=effective is None or effective[0] != new_zh_tw,
                )
            if repair_row is None:
                conn.execute(
                    "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?,?,?)",
                    (entry_id, SRC_AI_REPAIR, new_zh_tw),
                )
            else:
                result = conn.execute(
                    "UPDATE translation SET zh_tw=?, checker='', review_status=NULL, "
                    "updated_at=CURRENT_TIMESTAMP WHERE entry_id=? AND source=? "
                    "AND zh_tw=? AND revision=?",
                    (
                        new_zh_tw,
                        entry_id,
                        SRC_AI_REPAIR,
                        repair_row[0],
                        repair_row[3],
                    ),
                )
                if result.rowcount != 1:
                    return AITranslationReplaceResult("skipped_changed")
            new_revision = conn.execute(
                "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                (entry_id, SRC_AI_REPAIR),
            ).fetchone()[0]

            conn.execute(
                "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, "
                "new_zh_tw, prev_checker, prev_review_status, new_checker, "
                "new_review_status, prev_revision, new_revision, source_id, "
                "repair_prev_zh_tw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry_id,
                    uuid.uuid4().hex,
                    actor,
                    "ai_retranslate",
                    current[0],
                    new_zh_tw,
                    repair_row[1] if repair_row else "",
                    repair_row[2] if repair_row else None,
                    "",
                    None,
                    repair_row[3] if repair_row else None,
                    new_revision,
                    SRC_AI_REPAIR,
                    repair_row[0] if repair_row else None,
                ),
            )
            self._refresh(conn, [entry_id])
            effective = conn.execute(
                "SELECT zh_tw FROM effective WHERE entry_id=?", (entry_id,)
            ).fetchone()
        return AITranslationReplaceResult(
            "updated", shadowed=effective is None or effective[0] != new_zh_tw
        )

    @staticmethod
    def _reviewed_like(checker: str | None, review: str | None) -> bool:
        """與 ``_priority_order`` 相同：這一列是否因審核而排在未審核列之前。"""
        return review == "reviewed" or (bool(checker) and review != "unreviewed")

    def _would_lose_effect_when_unreviewed(
        self, conn: sqlite3.Connection, entry_id: int, source_id: int
    ) -> bool:
        """這一列退回未審核後，是否會被其他來源取代而不再生效。"""
        rank_of = lambda source: conn.execute(
            f"SELECT {rank_sql(self.priority, '?')}", (source,)
        ).fetchone()[0]
        own_rank = rank_of(source_id)
        return any(
            self._reviewed_like(checker, status) or rank_of(source) < own_rank
            for source, checker, status in conn.execute(
                "SELECT source, checker, review_status FROM translation "
                "WHERE entry_id=? AND source<>? AND zh_tw<>''",
                (entry_id, source_id),
            )
        )

    def current_translation_row(
        self,
        entry_id: int,
        source_id: int,
        *,
        expected_version: str,
        expected_kind: str,
        expected_mod_id: str,
        expected_key: str,
        expected_en_us: str,
    ) -> tuple[str, int] | None:
        """``(譯文, revision)`` of one source row, or ``None`` when it no longer matches."""
        row = self._conn.execute(
            "SELECT t.zh_tw, t.revision FROM translation t "
            "JOIN entry e ON e.id=t.entry_id "
            "WHERE t.entry_id=? AND t.source=? AND e.mc_version=? "
            "AND e.kind=? AND e.mod_id=? AND e.key=? AND e.en_us=?",
            (
                entry_id,
                source_id,
                expected_version,
                expected_kind,
                expected_mod_id,
                expected_key,
                expected_en_us,
            ),
        ).fetchone()
        return None if row is None else (row[0], int(row[1]))

    def replace_translation_quality_mismatch(
        self,
        entry_id: int,
        source_id: int,
        expected_old_zh_tw: str,
        new_zh_tw: str,
        *,
        expected_version: str,
        expected_kind: str,
        expected_mod_id: str,
        expected_key: str,
        expected_en_us: str,
        expected_revision: int | None,
        actor: str = "AI 特殊字元修復",
        action: str = "quality_repair",
        strict_newline: bool = False,
        sync_other_versions: bool = False,
    ) -> AITranslationReplaceResult:
        """以 CAS 取代一筆不匹配來源資料，但不改變其身分。"""
        if action not in {"quality_repair", "quality_repair_review"}:
            raise ValueError("invalid quality repair history action")
        if not isinstance(new_zh_tw, str) or not new_zh_tw.strip():
            raise ValueError("AI 修復譯文不可為空")

        with self._tx(bump_generation_on_noop=False, keep_review=True) as conn:
            current = conn.execute(
                "SELECT t.zh_tw, t.revision, t.checker, t.review_status "
                "FROM translation t JOIN entry e ON e.id=t.entry_id "
                "WHERE t.entry_id=? AND t.source=? AND e.mc_version=? "
                "AND e.kind=? AND e.mod_id=? AND e.key=? AND e.en_us=?",
                (
                    entry_id,
                    source_id,
                    expected_version,
                    expected_kind,
                    expected_mod_id,
                    expected_key,
                    expected_en_us,
                ),
            ).fetchone()
            if current is None or current[0] != expected_old_zh_tw:
                return AITranslationReplaceResult("skipped_changed")
            if expected_revision is not None and current[1] != expected_revision:
                return AITranslationReplaceResult("skipped_changed")
            if action == "quality_repair":
                # 自動修復只處理生效中的來源；人工審查會列出每個來源，不受此限
                live = conn.execute(
                    "SELECT source FROM effective WHERE entry_id=?", (entry_id,)
                ).fetchone()
                if live is None or live[0] != source_id:
                    # 預覽之後來源優先序（或審核）變了，這個來源已不是畫面上生效的那個
                    return AITranslationReplaceResult("skipped_changed")
            eligible, _issues, _mixed = repair_input_issues_for_kind(
                expected_en_us, current[0], kind=expected_kind
            )
            if not eligible:
                return AITranslationReplaceResult("skipped_changed")
            # Automated repair must pass output checks; an explicit human review
            # accepts the reviewed draft while retaining the same source CAS.
            if action == "quality_repair" and repair_output_issues_for_kind(
                expected_en_us,
                new_zh_tw,
                kind=expected_kind,
                allow_actual_newlines=not strict_newline,
            ):
                return AITranslationReplaceResult("skipped_changed")
            if new_zh_tw == current[0]:
                return AITranslationReplaceResult("unchanged")

            def apply_row(target_id, revision, checker, review) -> bool:
                # 審核狀態跟著文字走：自動修復的新文字還沒人看過，人工確認草稿才算已審核
                if action == "quality_repair":
                    new_checker = ""
                    new_review = "unreviewed" if source_id == SRC_MANUAL else None
                    if (
                        source_id != SRC_MANUAL
                        and self._reviewed_like(checker, review)
                        and self._would_lose_effect_when_unreviewed(
                            conn, target_id, source_id
                        )
                    ):
                        return False  # 退回審核後生效來源會換掉，保留原文
                else:
                    new_checker = actor if source_id == SRC_MANUAL else ""
                    new_review = "reviewed"
                updated = conn.execute(
                    "UPDATE translation SET zh_tw=?, checker=?, review_status=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE entry_id=? AND source=? "
                    "AND zh_tw=? AND revision=? AND checker IS ? AND review_status IS ?",
                    (
                        new_zh_tw,
                        new_checker,
                        new_review,
                        target_id,
                        source_id,
                        current[0],
                        revision,
                        checker,
                        review,
                    ),
                )
                if updated.rowcount != 1:
                    return False
                new_revision = conn.execute(
                    "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                    (target_id, source_id),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, "
                    "new_zh_tw, prev_checker, prev_review_status, new_checker, "
                    "new_review_status, prev_revision, new_revision, source_id, "
                    "repair_prev_zh_tw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        target_id,
                        uuid.uuid4().hex,
                        actor,
                        action,
                        current[0],
                        new_zh_tw,
                        checker or "",
                        review,
                        new_checker or "",
                        new_review,
                        revision,
                        new_revision,
                        source_id,
                        None,
                    ),
                )
                return True

            if not apply_row(entry_id, current[1], current[2], current[3]):
                return AITranslationReplaceResult("skipped_changed")
            synced: list[int] = []
            if sync_other_versions and action in {
                "quality_repair",
                "quality_repair_review",
            }:
                # 自動修復只處理生效中的來源：其他版本裡這個來源沒在生效就不同步
                live_only = (
                    "JOIN effective f ON f.entry_id=t.entry_id AND f.source=t.source "
                    if action == "quality_repair"
                    else ""
                )
                siblings = conn.execute(
                    "SELECT t.entry_id, t.revision, t.checker, t.review_status "
                    "FROM entry e0 JOIN entry e ON e.kind=e0.kind AND e.mod_id=e0.mod_id "
                    "AND e.key=e0.key AND e.en_us=e0.en_us AND e.mc_version<>e0.mc_version "
                    "JOIN translation t ON t.entry_id=e.id AND t.source=? AND t.zh_tw=? "
                    f"{live_only}"
                    "WHERE e0.id=?",
                    (source_id, current[0], entry_id),
                ).fetchall()
                synced = [row[0] for row in siblings if apply_row(*row)]
            self._refresh(conn, [entry_id, *synced])
        return AITranslationReplaceResult("updated", len(synced))

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

    _REUSABLE_PEERS_SQL = """(
        SELECT COUNT(DISTINCT f2.zh_tw) FROM entry e2 JOIN effective f2 ON f2.entry_id = e2.id
        WHERE e2.kind = u.kind AND e2.mod_id = u.mod_id AND e2.key = u.key
          AND e2.en_us = u.en_us)"""

    def _untranslated_candidates_cte(
        self, version: str, mod_ids: Sequence[str] | None, *, ordered: bool = True
    ) -> tuple[str, list]:
        """未翻譯候選（``ordered`` 時依 (類型, 模組, 鍵值) 排序）；先用窄索引找出 id，再讀寬欄位。

        只需計數時 ``ordered=False``，不建立用不到的臨時排序。

        候選本身沒有生效譯文，所以同內容的 ``effective`` 列必定來自其他版本，
        不必再比對 ``mc_version``。
        """
        mod_filter = ""
        params: list = [version]
        if mod_ids:
            mod_filter = f" AND i.mod_id IN ({','.join('?' * len(mod_ids))})"
            params.extend(mod_ids)
        sql = (
            "WITH u AS MATERIALIZED ("
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us "
            "FROM (SELECT i.id FROM entry i WHERE i.mc_version = ?"
            f"{mod_filter} AND i.id NOT IN (SELECT entry_id FROM effective)) c "
            "JOIN entry e ON e.id = c.id WHERE e.en_us <> ''"
            f"{' ORDER BY e.kind, e.mod_id, e.key' if ordered else ''}) "
        )
        return sql, params

    def count_reusable(self, version: str, mod_ids: Sequence[str] | None = None) -> int:
        """未翻譯條目中，其他版本已有相同內容譯文、開始機翻時會直接沿用的筆數。"""
        cte, params = self._untranslated_candidates_cte(version, mod_ids, ordered=False)
        sql = f"{cte}SELECT COUNT(*) FROM u WHERE {self._REUSABLE_PEERS_SQL} = 1"
        return self._cached_count(sql, params)

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
        if exclude_reusable:
            cte, params = self._untranslated_candidates_cte(version, mod_ids)
            sql = (
                f"{cte}SELECT id, kind, mod_id, key, en_us FROM u "
                f"WHERE {self._REUSABLE_PEERS_SQL} <> 1 ORDER BY kind, mod_id, key"
            )
        else:
            where, extra = self._untranslated_where(mod_ids)
            sql = (
                "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us "
                "FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
                f"WHERE {where} ORDER BY e.kind, e.mod_id, e.key"
            )
            params = [version, *extra]
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        key = (
            "untranslated:"
            + hashlib.sha1(json.dumps([sql, params]).encode("utf-8")).hexdigest()
        )
        return [
            tuple(row)
            for row in self._cached(key, lambda: self._q(sql, params), persist=False)
        ]

    def _same_as_source_ai_where(
        self, version: str, mod_ids: Sequence[str] | None
    ) -> tuple[str, list]:
        where = [
            "e.mc_version = ?",
            self._same_as_source_sql(),
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
            f"SELECT COUNT(*) FROM {self._same_as_source_entry_table()} "
            f"LEFT JOIN effective f ON f.entry_id=e.id WHERE {where}"
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
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, f.zh_tw, e.mc_version, "
            "f.source, t.revision, t.checker, t.review_status "
            f"FROM (SELECT e.id FROM {self._same_as_source_entry_table()} "
            f"JOIN effective f ON f.entry_id = e.id WHERE {where}) c "
            "JOIN entry e ON e.id = c.id "
            "JOIN effective f ON f.entry_id = e.id "
            "LEFT JOIN translation t ON t.entry_id=e.id AND t.source=f.source "
            "ORDER BY e.mod_id, e.kind, e.key"
        )
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        return [SameSourceAIEntry(*row) for row in self._q(sql, params)]

    def mismatched_translation_entries(
        self,
        version: str,
        mod_ids: Sequence[str] | None = None,
        limit: int | None = None,
        *,
        check: Callable[[], None] | None = None,
    ) -> tuple[int, list[SameSourceAIEntry]]:
        """找出所有儲存來源翻譯中的特殊字元不匹配。"""
        if check is not None:
            check()
        token_chars = " OR ".join(
            f"{column} GLOB {_TOKEN_CHAR_GLOB_SQL} OR instr({column}, char(10)) > 0"
            for column in ("e.en_us", "t.zh_tw")
        )
        where = [
            "e.mc_version=?",
            "e.en_us<>''",
            "t.zh_tw<>''",
            f"(({token_chars}) OR e.kind IN ('{KIND_GUIDE}', '{KIND_PATCHOULI}'))",
        ]
        params: list = [version]
        if mod_ids:
            where.append(f"e.mod_id IN ({','.join('?' * len(mod_ids))})")
            params.extend(mod_ids)
        sql = (
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, t.zh_tw, "
            "e.mc_version, t.source, t.revision, t.checker, t.review_status "
            "FROM entry e JOIN translation t ON t.entry_id=e.id "
            f"WHERE {' AND '.join(where)} ORDER BY e.mod_id, e.kind, e.key, t.source"
        )
        selected_limit = limit if limit and limit > 0 else None

        def load_candidates():
            count = 0
            candidates = []
            with self._lock:
                cursor = self._conn.execute(sql, tuple(params))
                try:
                    while rows := cursor.fetchmany(512):
                        for row in rows:
                            if check is not None:
                                check()
                            if (
                                quality_state(row[4], row[5], kind=row[1])[0]
                                != "mismatch"
                            ):
                                continue
                            count += 1
                            if (
                                selected_limit is None
                                or len(candidates) < selected_limit
                            ):
                                candidates.append(row)
                finally:
                    cursor.close()
            return count, candidates

        key = "mismatched_sources:" + json.dumps([version, mod_ids, selected_limit])
        count, rows = self._cached(key, load_candidates, persist=False)
        return count, [SameSourceAIEntry(*row) for row in rows]

    def iter_repairable_translation_entries(
        self,
        version: str,
        mod_ids: Sequence[str] | None = None,
        *,
        check: Callable[[], None] | None = None,
    ):
        """逐筆產生可修復資料，只處理目前生效的來源列。"""
        # 一般條目兩邊都沒有任何格式字元（§ % $ { \ 換行）就不可能有 token，
        # 但 guide 還要保留純 Markdown 結構差異；其餘大量條目仍先在 SQL 排除。
        token_chars = " OR ".join(
            f"{column} GLOB {_TOKEN_CHAR_GLOB_SQL} OR instr({column}, char(10)) > 0"
            for column in ("e.en_us", "t.zh_tw")
        )
        where = [
            "e.mc_version=?",
            "e.en_us<>''",
            "t.zh_tw<>''",
            f"(({token_chars}) OR e.kind IN ('{KIND_GUIDE}', '{KIND_PATCHOULI}'))",
        ]
        params: list = [version]
        if mod_ids:
            where.append(f"e.mod_id IN ({','.join('?' * len(mod_ids))})")
            params.extend(mod_ids)
        sql = (
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, t.zh_tw, "
            "e.mc_version, t.source, t.revision, t.checker, t.review_status "
            "FROM entry e JOIN translation t ON t.entry_id=e.id "
            "JOIN effective f ON f.entry_id=e.id AND f.source=t.source "
            f"WHERE {' AND '.join(where)} ORDER BY e.mod_id, e.kind, e.key, t.source"
        )
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            while rows := cursor.fetchmany(512):
                for row in rows:
                    if check is not None:
                        check()
                    wanted, previous = format_tokens(row[4]), format_tokens(row[5])
                    newline_mismatch = wanted["\n"] != previous["\n"]
                    eligible, _issues, mixed = repair_input_issues_for_kind(
                        row[4], row[5], kind=row[1]
                    )
                    if eligible:
                        yield (
                            SameSourceAIEntry(*row),
                            newline_mismatch,
                            mixed,
                        )

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

    def _reviewed_lang_candidate_rows(
        self, mod_id: str, mc_version: str | None
    ) -> list[list[str]]:
        """載入一次模組與版本候選快照，供重複術語查找。"""
        clauses = [
            "e.mod_id = ?",
            "e.kind = 'lang'",
            "f.review_status = 'reviewed'",
            "e.en_us <> ''",
            "f.zh_tw <> ''",
            "e.en_us <> f.zh_tw",
        ]
        params: list[object] = [mod_id]
        if mc_version:
            clauses.append("e.mc_version = ?")
            params.append(mc_version)
        cache_key = "reviewed_lang_candidates:" + json.dumps(
            [mod_id, mc_version], ensure_ascii=False, separators=(",", ":")
        )

        def load() -> list[list[str]]:
            rows = self._q(
                f"""
                SELECT e.key, e.en_us, f.zh_tw
                FROM entry e
                JOIN effective f ON f.entry_id = e.id
                WHERE {" AND ".join(clauses)}
                ORDER BY e.key, e.id
                """,
                params,
            )
            return [
                list(row)
                for row in rows
                if all(isinstance(value, str) for value in row)
                and row[1].strip()
                and row[2].strip()
                and quality_state(row[1], row[2], kind="lang")[0] == "consistent"
            ]

        return self._cached(cache_key, load)

    def reviewed_lang_glossary(
        self,
        mod_id: str,
        mc_version: str | None = None,
        source_texts: Sequence[str] | None = None,
        *,
        limit: int | None = 240,
    ) -> list[dict[str, str]]:
        """回傳品質乾淨且已審核的指定模組 lang 術語。"""
        normalized_sources = tuple(
            dict.fromkeys(
                source
                for source in source_texts or ()
                if isinstance(source, str) and source.strip()
            )
        )
        if limit is not None and int(limit) <= 0:
            return []
        if not normalized_sources:
            return []

        # 跨語言大小寫折疊由 Python ``casefold`` 負責；SQLite ``lower`` 只
        # 保證 ASCII，不能拿來當 Unicode 的排除式預篩。候選集合依模組、
        # 版本與資料世代快取，來源相關性仍由每次呼叫的 matcher 判斷。
        rows = self._reviewed_lang_candidate_rows(mod_id, mc_version)
        matched_sources = matching_terms(
            normalized_sources, tuple(row[1] for row in rows)
        )
        matched_source_keys = {
            term_canonical_key(matched) for matched in matched_sources
        }
        by_source: dict[str, list[dict[str, str]]] = {}
        for key, en_us, zh_tw in rows:
            if not (
                isinstance(key, str)
                and isinstance(en_us, str)
                and isinstance(zh_tw, str)
                and term_canonical_key(en_us) in matched_source_keys
            ):
                continue
            by_source.setdefault(term_canonical_key(en_us), []).append(
                {"key": key, "en_us": en_us, "zh_tw": zh_tw}
            )

        result: list[dict[str, str]] = []
        for source in sorted(by_source):
            candidates = by_source[source]
            targets = {entry["zh_tw"] for entry in candidates}
            if len(targets) != 1:
                continue
            result.append(candidates[0])
            if limit is not None and len(result) >= max(0, int(limit)):
                break
        return result

    def load_mod_for_priority(
        self, mod_id: str, priority: tuple[int, ...]
    ) -> list[tuple[str, str, str, str, str, int]]:
        """以呼叫端快照優先序唯讀選出模組生效譯文，不依賴共享 effective 表。"""
        return self._q(self._load_mod_for_priority_sql(priority), (mod_id,))

    def _load_mod_for_priority_sql(self, priority: tuple[int, ...]) -> str:
        """建立合併解析器使用的模組範圍排序查詢。"""
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
