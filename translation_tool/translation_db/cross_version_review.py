"""跨版本已審核譯文沿用與版本差異審核。

「內容相同」＝類型、模組、鍵值、原文都相同且來源語系相容的條目。其他版本若已有
**已審核**的生效譯文，目標版本的條目就可以沿用，或列出差異讓使用者決定。

- :func:`reuse_reviewed` 供匯入後自動沿用：只處理這次匯入新增或譯文有變動的條目，
  參考譯文彼此不一致（衝突）、目標已審核者一律不動。
- :func:`list_differences` 供「版本差異審核」：回傳目標版本與參考版本的差異與衝突。
- :func:`apply_references` 供使用者逐筆或批次套用參考譯文。

寫入沿用審核流程的歷史格式（``action='review'``），因此 ``TranslationDB.revert``
可以整批還原。這裡不新增資料表或欄位，查詢沿用 ``idx_entry_content``。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .sql_helpers import source_locale_match_sql

if TYPE_CHECKING:
    import sqlite3

    from .repository import TranslationDB

REFERENCE_LATEST = ""  # 參考版本＝該條目最近一次被審核的版本
_REVIEW_ACTION = "review"
_NOTE_PREFIX = "跨版本沿用自 "


@dataclass(frozen=True)
class VersionRef:
    """某個版本中，內容相同條目的已審核生效譯文。"""

    entry_id: int
    version: str
    zh_tw: str
    source: int
    checker: str
    reviewed_at: str


@dataclass(frozen=True)
class CrossVersionRow:
    """目標版本的一個條目與其他版本的已審核參考譯文。"""

    entry_id: int
    kind: str
    mod_id: str
    key: str
    en_us: str
    target_zh_tw: str
    target_source: int | None
    target_reviewed: bool
    refs: tuple[VersionRef, ...]

    @property
    def conflict(self) -> bool:
        """各版本的已審核譯文不一致。"""
        return len({ref.zh_tw for ref in self.refs}) > 1

    @property
    def reference(self) -> VersionRef:
        """預設參考：最近一次被審核的版本（同時間取版本字串較大者，結果穩定）。"""
        return max(self.refs, key=lambda ref: (ref.reviewed_at, ref.version))

    @property
    def differs(self) -> bool:
        return any(ref.zh_tw != self.target_zh_tw for ref in self.refs)


@dataclass(frozen=True)
class ApplyItem:
    """套用一筆參考譯文；``expected_*`` 讓畫面過期時不覆蓋較新的內容。"""

    entry_id: int
    ref_version: str
    expected_target_zh_tw: str
    expected_ref_zh_tw: str


@dataclass
class ReuseResult:
    """自動沿用或批次套用的結果。"""

    applied: int = 0
    skipped_reviewed: int = 0  # 目標已有人工審核的不同譯文，保留不動
    conflicts: int = 0  # 各版本已審核譯文不一致，只列出不處理
    stale: int = 0  # 套用時畫面資料已過期而略過
    history_id: int | None = None  # 任一筆歷史編號，可傳給 ``TranslationDB.revert``
    entry_ids: list[int] = field(default_factory=list)


def _scope_sql(
    since: str | None,
    mod_ids: Sequence[str] | None,
    kinds: Sequence[str] | None,
    entry_id: int | None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if entry_id is not None:
        clauses.append("e.id = ?")
        params.append(entry_id)
    if since is not None:
        clauses.append(
            "(e.created_at >= ? OR EXISTS (SELECT 1 FROM translation tt "
            "WHERE tt.entry_id = e.id AND tt.updated_at >= ?))"
        )
        params += [since, since]
    if mod_ids:
        clauses.append(f"e.mod_id IN ({','.join('?' * len(mod_ids))})")
        params += list(mod_ids)
    if kinds:
        clauses.append(f"e.kind IN ({','.join('?' * len(kinds))})")
        params += list(kinds)
    return "".join(f" AND {c}" for c in clauses), params


def _fetch_rows(
    conn: sqlite3.Connection,
    version: str,
    *,
    ref_version: str,
    since: str | None,
    mod_ids: Sequence[str] | None,
    kinds: Sequence[str] | None,
    entry_id: int | None = None,
) -> list[CrossVersionRow]:
    scope, params = _scope_sql(since, mod_ids, kinds, entry_id)
    ref_clause = " AND e2.mc_version = ?" if ref_version else ""
    sql = (
        "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, "
        "ft.zh_tw, ft.source, ft.review_status, "
        "e2.id, e2.mc_version, fr.zh_tw, fr.source, fr.checker, "
        "COALESCE(tr.updated_at, '') "
        "FROM entry e "
        "LEFT JOIN effective ft ON ft.entry_id = e.id "
        "JOIN entry e2 ON e2.kind = e.kind AND e2.mod_id = e.mod_id "
        "AND e2.key = e.key AND e2.en_us = e.en_us "
        "AND e2.mc_version <> e.mc_version "
        f"AND {source_locale_match_sql('e', 'e2')}{ref_clause} "
        "JOIN effective fr ON fr.entry_id = e2.id "
        "AND fr.review_status = 'reviewed' AND fr.zh_tw <> '' "
        "LEFT JOIN translation tr ON tr.entry_id = e2.id AND tr.source = fr.source "
        f"WHERE e.mc_version = ? AND e.en_us <> ''{scope}"
    )
    args: list[object] = [*([ref_version] if ref_version else []), version, *params]
    grouped: dict[int, list[VersionRef]] = {}
    heads: dict[int, tuple] = {}
    for row in conn.execute(sql, args):
        eid = int(row[0])
        heads.setdefault(eid, (row[1:5], row[5], row[6], row[7]))
        grouped.setdefault(eid, []).append(
            VersionRef(int(row[8]), row[9], row[10], int(row[11]), row[12], row[13])
        )
    out = []
    for eid in sorted(grouped):
        (kind, mod_id, key, en_us), tw, source, status = heads[eid]
        out.append(
            CrossVersionRow(
                eid,
                kind,
                mod_id,
                key,
                en_us,
                tw or "",
                None if source is None else int(source),
                status == "reviewed",
                tuple(sorted(grouped[eid], key=lambda r: (r.reviewed_at, r.version))),
            )
        )
    return out


def list_differences(
    db: TranslationDB,
    version: str,
    *,
    ref_version: str = REFERENCE_LATEST,
    mod_ids: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
) -> list[CrossVersionRow]:
    """目標版本與已審核參考譯文有差異（或各版本衝突）的條目。

    ``ref_version`` 空字串＝每個條目取自己最近一次被審核的版本；指定版本時只比較該版本。
    """
    if not db.has_review_state:
        return []
    with db._lock:
        rows = _fetch_rows(
            db._conn,
            version,
            ref_version=ref_version,
            since=None,
            mod_ids=mod_ids,
            kinds=kinds,
        )
    return [row for row in rows if row.differs]


def _write_reference(
    db: TranslationDB,
    conn: sqlite3.Connection,
    row: CrossVersionRow,
    ref: VersionRef,
    batch: str,
    actor: str,
) -> int:
    """把參考譯文以已審核狀態寫入目標條目，回傳歷史編號。"""
    prev = conn.execute(
        "SELECT zh_tw, checker, review_status, revision FROM translation "
        "WHERE entry_id=? AND source=?",
        (row.entry_id, ref.source),
    ).fetchone()
    conn.execute(
        "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
        "VALUES (?,?,?,?, 'reviewed') "
        "ON CONFLICT(entry_id, source) DO UPDATE SET "
        "zh_tw=excluded.zh_tw, checker=excluded.checker, "
        "review_status='reviewed', updated_at=CURRENT_TIMESTAMP",
        (row.entry_id, ref.source, ref.zh_tw, ref.checker),
    )
    written = conn.execute(
        "SELECT checker, revision FROM translation WHERE entry_id=? AND source=?",
        (row.entry_id, ref.source),
    ).fetchone()
    cur = conn.execute(
        "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, new_zh_tw, "
        "prev_manual, note, prev_checker, prev_review_status, new_checker, "
        "new_review_status, prev_revision, new_revision, source_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row.entry_id,
            batch,
            actor,
            _REVIEW_ACTION,
            row.target_zh_tw,
            ref.zh_tw,
            prev[0] if prev else None,
            f"{_NOTE_PREFIX}{ref.version}",
            prev[1] if prev else None,
            (prev[2] if prev[2] is not None else "") if prev else None,
            written[0],
            "reviewed",
            prev[3] if prev else None,
            written[1],
            ref.source,
        ),
    )
    return int(cur.lastrowid or 0)


def reuse_reviewed(
    db: TranslationDB,
    version: str,
    *,
    since: str,
    mod_ids: Sequence[str] | None = None,
    actor: str = "",
) -> ReuseResult:
    """匯入後自動沿用其他版本已審核的相同譯文。

    只處理 ``since`` 之後新增或譯文有變動的條目。參考譯文一致且目標尚未審核才寫入；
    衝突與「目標已審核且不同」只計數。同樣的匯入重跑不會再寫入（目標已等於參考）。
    """
    result = ReuseResult()
    if not db.has_review_state:
        return result
    batch = uuid.uuid4().hex
    with db._tx(bump_generation_on_noop=False, keep_review=True) as conn:
        rows = _fetch_rows(
            conn,
            version,
            ref_version=REFERENCE_LATEST,
            since=since,
            mod_ids=mod_ids,
            kinds=None,
        )
        for row in rows:
            if not row.differs:
                continue
            if row.conflict:
                result.conflicts += 1
            elif row.target_reviewed:
                result.skipped_reviewed += 1
            else:
                hid = _write_reference(db, conn, row, row.reference, batch, actor)
                result.applied += 1
                result.entry_ids.append(row.entry_id)
                result.history_id = result.history_id or hid
        db._refresh(conn, result.entry_ids)
    return result


def apply_references(
    db: TranslationDB, items: Iterable[ApplyItem], *, actor: str = ""
) -> ReuseResult:
    """套用使用者選定的參考譯文（同一批可整批還原）。

    目標或參考的內容與畫面所見不同時略過並計入 ``stale``，不覆蓋較新的修改。
    """
    result = ReuseResult()
    if not db.has_review_state:
        return result
    batch = uuid.uuid4().hex
    with db._tx(bump_generation_on_noop=False, keep_review=True) as conn:
        for item in items:
            row = _current_row(conn, item)
            if row is None:
                result.stale += 1
                continue
            ref = next(r for r in row.refs if r.version == item.ref_version)
            hid = _write_reference(db, conn, row, ref, batch, actor)
            result.applied += 1
            result.entry_ids.append(row.entry_id)
            result.history_id = result.history_id or hid
        db._refresh(conn, result.entry_ids)
    return result


def _current_row(conn: sqlite3.Connection, item: ApplyItem) -> CrossVersionRow | None:
    """依畫面期待值重新讀取一筆；任何一方已變動就回傳 None。"""
    head = conn.execute(
        "SELECT mc_version FROM entry WHERE id=?", (item.entry_id,)
    ).fetchone()
    if head is None:
        return None
    rows = _fetch_rows(
        conn,
        head[0],
        ref_version=item.ref_version,
        since=None,
        mod_ids=None,
        kinds=None,
        entry_id=item.entry_id,
    )
    row = next((r for r in rows if r.entry_id == item.entry_id), None)
    if row is None or row.target_zh_tw != item.expected_target_zh_tw:
        return None
    if not any(
        r.version == item.ref_version and r.zh_tw == item.expected_ref_zh_tw
        for r in row.refs
    ):
        return None
    return row


class CrossVersionReviewMixin:
    """``TranslationDB`` 的跨版本沿用／差異審核方法。"""

    def db_timestamp(self) -> str:
        """資料庫目前時間（與 ``created_at``／``updated_at`` 同格式），供匯入範圍比對。"""
        return str(self._q("SELECT CURRENT_TIMESTAMP")[0][0])  # type: ignore[attr-defined]

    def reuse_reviewed_translations(
        self,
        version: str,
        *,
        since: str,
        mod_ids: Sequence[str] | None = None,
        actor: str = "",
    ) -> ReuseResult:
        return reuse_reviewed(self, version, since=since, mod_ids=mod_ids, actor=actor)  # type: ignore[arg-type]

    def list_cross_version_differences(
        self,
        version: str,
        *,
        ref_version: str = REFERENCE_LATEST,
        mod_ids: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
    ) -> list[CrossVersionRow]:
        return list_differences(
            self,  # type: ignore[arg-type]
            version,
            ref_version=ref_version,
            mod_ids=mod_ids,
            kinds=kinds,
        )

    def apply_cross_version_references(
        self, items: Iterable[ApplyItem], *, actor: str = ""
    ) -> ReuseResult:
        return apply_references(self, items, actor=actor)  # type: ignore[arg-type]
