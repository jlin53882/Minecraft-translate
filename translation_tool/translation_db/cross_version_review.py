"""跨版本已審核譯文沿用與版本差異審核。

「內容相同」＝類型、模組、鍵值、原文都相同且來源語系相容的條目。其他版本若已有
**已審核**的生效譯文，目標版本的條目就可以沿用，或列出差異讓使用者決定。

- :func:`reuse_reviewed` 供匯入後自動沿用：只處理這次匯入實際新增或新增譯文的條目
  （由匯入交易回報的條目編號），參考譯文彼此不一致（衝突）、目標已審核者一律不動。
- :func:`list_differences` 供「版本差異審核」：回傳目標版本與參考版本的差異與衝突，
  分段讀取並在段與段之間釋放資料庫鎖，可中途取消。
- :func:`apply_references` 供使用者逐筆或批次套用參考譯文；套用時在同一個交易內重新
  確認譯文、來源、審核狀態與 revision，任何一項與畫面所見不同都略過。

寫入規則：目標尚未審核時，一律另寫一列 ``SRC_CROSS_VERSION``（已審核列贏過未審核列，
目標原本的譯文與來源完整保留）；目標已經是已審核時，才改寫目標目前生效的那一列
（使用者明確選擇覆蓋）。寫入沿用審核流程的歷史格式（``action='review'``），因此
``TranslationDB.revert`` 可以整批還原。這裡不新增資料表或欄位，查詢沿用
``idx_entry_content``。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .content_identity import (
    content_key_sql,
    content_mod_sql,
    other_version_sql,
    pack_label,
    same_content_sql,
)
from .query_interrupt import QueryCancelled
from .schema import KIND_KUBEJS_LANG, SRC_CROSS_VERSION, SRC_MANUAL
from .sql_helpers import (
    effective_review_sql,
    source_locale_match_sql,
    source_locale_sql,
)

if TYPE_CHECKING:
    import sqlite3

    from .repository import TranslationDB

# 「已審核」沿用全庫共用的定義（``effective_review_sql``）：舊資料的人工譯文
# （``legacy_unknown``）畫面同樣顯示「人工已審核」，批量審核與解除審核也都視為已審核。
_REVIEWED_STATES = ("reviewed", "legacy_unknown")
REFERENCE_LATEST = ""  # 參考版本＝該條目最近一次被審核的版本
_REVIEW_ACTION = "review"
_NOTE_PREFIX = "跨版本沿用自 "
_ID_CHUNK = 500  # 一次處理的目標條目數：控制單次查詢大小，也是取消與釋放鎖的間隔


@dataclass(frozen=True)
class VersionRef:
    """某個版本中，內容相同條目的已審核生效譯文。"""

    entry_id: int
    version: str
    zh_tw: str
    source: int
    checker: str
    reviewed_at: str  # 最後一次審核事件的時間；沒有審核歷史時退回譯文列的更新時間
    revision: int = 0
    review_seq: int = 0  # 最後一次審核事件的歷史編號；同一秒內的先後以它為準
    mod_id: str = ""  # 參考條目的模組識別（KubeJS／FTB 可能與目標的整合包身分不同）


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
    target_revision: int | None = None

    @property
    def conflict(self) -> bool:
        """各版本的已審核譯文不一致。"""
        return len({ref.zh_tw for ref in self.refs}) > 1

    @property
    def reference(self) -> VersionRef:
        """預設參考：最近一次被審核的版本（同一秒內以歷史編號、再以版本字串決定）。"""
        return max(self.refs, key=_ref_order)

    @property
    def differs(self) -> bool:
        return any(ref.zh_tw != self.target_zh_tw for ref in self.refs)


def _ref_order(ref: VersionRef) -> tuple[str, int, str]:
    return (ref.reviewed_at, ref.review_seq, ref.version)


@dataclass(frozen=True)
class ApplyItem:
    """套用一筆參考譯文；``expected_*`` 是畫面所見的狀態，任何一項不同就不寫入。"""

    entry_id: int
    ref_version: str
    ref_entry_id: int  # 同一版本可能有多個整合包身分的參考，以條目編號區分
    expected_target_zh_tw: str
    expected_ref_zh_tw: str
    expected_target_source: int | None
    expected_target_reviewed: bool
    expected_target_revision: int | None
    expected_ref_revision: int

    @classmethod
    def from_row(cls, row: CrossVersionRow, ref: VersionRef) -> ApplyItem:
        """以畫面上的一列與選定的參考建立套用項目。"""
        return cls(
            row.entry_id,
            ref.version,
            ref.entry_id,
            row.target_zh_tw,
            ref.zh_tw,
            row.target_source,
            row.target_reviewed,
            row.target_revision,
            ref.revision,
        )


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
    entry_ids: Sequence[int] | None,
    mod_ids: Sequence[str] | None,
    kinds: Sequence[str] | None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if entry_ids is not None:
        clauses.append(f"e.id IN ({','.join('?' * len(entry_ids))})")
        params += [int(i) for i in entry_ids]
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
    entry_ids: Sequence[int] | None,
    mod_ids: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    ref_pack: str = "",
    target_pack: str = "",
) -> list[CrossVersionRow]:
    """讀取指定目標條目（或整個版本）與其他版本已審核譯文的對照。

    「內容相同」用 ``content_identity.same_content_sql``（KubeJS／FTB 只看命名空間），
    走 ``idx_entry_content_ns``。``ref_pack`` 非空時，KubeJS／FTB 的參考只取該整合包資料夾
    （同一個遊戲版本有多個整合包版本時用來指定其中一個）。
    """
    scope, params = _scope_sql(entry_ids, mod_ids, kinds)
    ref_clause = " AND e2.mc_version = ?" if ref_version else ""
    sql = (
        "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, "
        f"ft.zh_tw, ft.source, {effective_review_sql('ft', True)}, tt.revision, "
        "e2.id, e2.mc_version, fr.zh_tw, fr.source, fr.checker, "
        "COALESCE(hh.at, tr.updated_at, ''), COALESCE(tr.revision, 0), "
        "COALESCE(hh.id, 0), e2.mod_id "
        "FROM entry e "
        "LEFT JOIN effective ft ON ft.entry_id = e.id "
        "LEFT JOIN translation tt ON tt.entry_id = e.id AND tt.source = ft.source "
        f"JOIN entry e2 ON {same_content_sql('e', 'e2')} "
        f"AND {other_version_sql('e', 'e2')} "
        f"AND {source_locale_match_sql('e', 'e2')}{ref_clause} "
        "JOIN effective fr ON fr.entry_id = e2.id "
        f"AND {effective_review_sql('fr', True)} IN ('reviewed','legacy_unknown') "
        "AND fr.zh_tw <> '' "
        "LEFT JOIN translation tr ON tr.entry_id = e2.id AND tr.source = fr.source "
        "LEFT JOIN history hh ON hh.id = (SELECT MAX(h.id) FROM history h "
        f"WHERE h.entry_id = e2.id AND h.action = '{_REVIEW_ACTION}') "
        f"WHERE e.mc_version = ? AND e.en_us <> ''{scope}"
    )
    args: list[object] = [*([ref_version] if ref_version else []), version, *params]
    grouped: dict[int, list[VersionRef]] = {}
    heads: dict[int, tuple] = {}
    for row in conn.execute(sql, args):
        if ref_pack and pack_label(row[1], row[17]) != ref_pack:
            continue
        if target_pack and pack_label(row[1], row[2]) != target_pack:
            continue
        eid = int(row[0])
        heads.setdefault(eid, (row[1:5], row[5], row[6], row[7], row[8]))
        grouped.setdefault(eid, []).append(
            VersionRef(
                int(row[9]),
                row[10],
                row[11],
                int(row[12]),
                row[13],
                row[14],
                int(row[15]),
                int(row[16]),
                row[17],
            )
        )
    out = []
    for eid in sorted(grouped):
        (kind, mod_id, key, en_us), tw, source, status, revision = heads[eid]
        out.append(
            CrossVersionRow(
                eid,
                kind,
                mod_id,
                key,
                en_us,
                tw or "",
                None if source is None else int(source),
                status in _REVIEWED_STATES,
                tuple(sorted(grouped[eid], key=_ref_order)),
                None if revision is None else int(revision),
            )
        )
    return out


def _target_id_chunks(
    db: TranslationDB, version: str, cancel: Callable[[], bool] | None
) -> Iterable[list[int]]:
    """依條目編號分段列出目標版本的條目；每段之間釋放資料庫鎖並檢查是否取消。

    ``idx_entry_version`` 以 ``(mc_version, rowid)`` 排序，所以 ``ORDER BY id`` 不需要臨時排序。
    """
    last = 0
    while True:
        if cancel is not None and cancel():
            raise QueryCancelled
        with db._lock:
            ids = [
                int(row[0])
                for row in db._conn.execute(
                    "SELECT id FROM entry WHERE mc_version = ? AND id > ? "
                    "AND en_us <> '' ORDER BY id LIMIT ?",
                    (version, last, _ID_CHUNK),
                )
            ]
        if not ids:
            return
        yield ids
        last = ids[-1]


def list_differences(
    db: TranslationDB,
    version: str,
    *,
    ref_version: str = REFERENCE_LATEST,
    mod_ids: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    cancel: Callable[[], bool] | None = None,
    ref_pack: str = "",
    target_pack: str = "",
) -> list[CrossVersionRow]:
    """目標版本與已審核參考譯文有差異（或各版本衝突）的條目。

    ``ref_version`` 空字串＝每個條目取自己最近一次被審核的版本；指定版本時只比較該版本，
    ``ref_pack`` 再指定其中的整合包資料夾（KubeJS／FTB）；``target_pack`` 非空時目標也只取該整合包資料夾的條目。
    分段讀取（每段 ``_ID_CHUNK`` 個目標條目），段與段之間釋放資料庫鎖；``cancel`` 回傳
    True 時拋出 :class:`QueryCancelled`。結果只保留有差異的列，記憶體與差異筆數成正比。
    """
    if not db.has_review_state:
        return []
    out: list[CrossVersionRow] = []
    for ids in _target_id_chunks(db, version, cancel):
        with db._lock:
            rows = _fetch_rows(
                db._conn,
                version,
                ref_version=ref_version,
                entry_ids=ids,
                mod_ids=mod_ids,
                kinds=kinds,
                ref_pack=ref_pack,
                target_pack=target_pack,
            )
        out.extend(row for row in rows if row.differs)
    return out


@dataclass
class ExclusionSummary:
    """內容相同、但沒有被列為差異的條目：讓使用者知道「為什麼沒列出來」。"""

    locale_incompatible: int = 0  # 有已審核的參考，但來源語系與目標不相容
    target_locale_unknown: int = (
        0  # 其中目標的來源語系未記錄（舊版匯入），重新掃描可補上
    )
    ref_unreviewed: int = 0  # 其他版本只有尚未審核的譯文（依設計不當參考）
    samples: list[str] = field(
        default_factory=list
    )  # 例：「key（目標語系 pt_br、參考語系 unknown:12）」
    source_changed: int = (
        0  # 類型、模組、鍵值相同，但原文不同，且其他版本有已審核的不同譯文
    )
    source_case_only: int = 0  # 其中原文只差大小寫或空白
    source_samples: list[str] = field(
        default_factory=list
    )  # 例：「key：「Mostre Sua Cara」→「Mostre sua Cara」」

    @property
    def total(self) -> int:
        return self.locale_incompatible + self.ref_unreviewed


_SAMPLE_LIMIT = 3


def explain_exclusions(
    db: TranslationDB,
    version: str,
    *,
    ref_version: str = REFERENCE_LATEST,
    mod_ids: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    cancel: Callable[[], bool] | None = None,
    ref_pack: str = "",
    target_pack: str = "",
) -> ExclusionSummary:
    """統計目標版本中「內容相同的其他版本有譯文，卻沒被列為差異」的條目與原因。

    只診斷不寫入。已經有可列出的參考（已審核且語系相容）的條目不計；其餘依原因分類：
    參考已審核但來源語系不相容，或參考尚未審核。
    """
    summary = ExclusionSummary()
    if not db.has_review_state:
        return summary
    for ids in _target_id_chunks(db, version, cancel):
        scope, params = _scope_sql(ids, mod_ids, kinds)
        ref_clause = " AND e2.mc_version = ?" if ref_version else ""
        sql = (
            "SELECT e.id, e.key, ft.zh_tw, fr.zh_tw, "
            f"{effective_review_sql('fr', True)}, "
            f"{source_locale_match_sql('e', 'e2')}, "
            f"{source_locale_sql('e')}, {source_locale_sql('e2')}, e.kind, e2.mod_id, e.mod_id "
            "FROM entry e LEFT JOIN effective ft ON ft.entry_id = e.id "
            f"JOIN entry e2 ON {same_content_sql('e', 'e2')} "
            f"AND {other_version_sql('e', 'e2')}{ref_clause} "
            "JOIN effective fr ON fr.entry_id = e2.id AND fr.zh_tw <> '' "
            f"WHERE e.mc_version = ? AND e.en_us <> ''{scope}"
        )
        args: list[object] = [*([ref_version] if ref_version else []), version, *params]
        with db._lock:
            rows = db._conn.execute(sql, args).fetchall()
        if ref_pack:
            rows = [r for r in rows if pack_label(r[8], r[9]) == ref_pack]
        if target_pack:
            rows = [r for r in rows if pack_label(r[8], r[10]) == target_pack]
        _classify_exclusions(rows, summary)
        _count_source_changes(
            db,
            version,
            ids,
            summary,
            ref_version,
            ref_pack,
            target_pack,
            mod_ids,
            kinds,
        )
    return summary


def _loose_text(text: str) -> str:
    """比較「只差大小寫或空白」用的寬鬆形式。"""
    return " ".join(text.casefold().split())


def _count_source_changes(
    db: TranslationDB,
    version: str,
    ids: Sequence[int],
    summary: ExclusionSummary,
    ref_version: str,
    ref_pack: str,
    target_pack: str,
    mod_ids: Sequence[str] | None,
    kinds: Sequence[str] | None,
) -> None:
    """統計「鍵值相同、原文不同」而不當成相同內容的條目（依設計不比對，讓使用者知道原因）。

    只計目標尚未審核、且其他版本有已審核且譯文不同的條目；原文只差大小寫或空白的另計。
    """
    scope, params = _scope_sql(ids, mod_ids, kinds)
    ref_clause = " AND e2.mc_version = ?" if ref_version else ""
    sql = (
        "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, e2.mod_id, e2.en_us, "
        f"ft.zh_tw, fr.zh_tw, {effective_review_sql('ft', True)} "
        "FROM entry e LEFT JOIN effective ft ON ft.entry_id = e.id "
        "JOIN entry e2 ON e2.kind = e.kind "
        f"AND {content_mod_sql('e2')} = {content_mod_sql('e')} "
        f"AND {content_key_sql('e2')} = {content_key_sql('e')} "
        f"AND e2.en_us <> e.en_us AND {other_version_sql('e', 'e2')}{ref_clause} "
        "JOIN effective fr ON fr.entry_id = e2.id AND fr.zh_tw <> '' "
        f"AND {effective_review_sql('fr', True)} IN ('reviewed','legacy_unknown') "
        f"WHERE e.mc_version = ? AND e.en_us <> ''{scope}"
    )
    args: list[object] = [*([ref_version] if ref_version else []), version, *params]
    with db._lock:
        rows = db._conn.execute(sql, args).fetchall()
    seen: set[int] = set()
    for row in rows:
        eid = int(row[0])
        if eid in seen or row[9] in _REVIEWED_STATES or (row[8] == (row[7] or "")):
            continue
        if ref_pack and pack_label(row[1], row[5]) != ref_pack:
            continue
        if target_pack and pack_label(row[1], row[2]) != target_pack:
            continue
        seen.add(eid)
        summary.source_changed += 1
        if _loose_text(row[4]) == _loose_text(row[6]):
            summary.source_case_only += 1
        if len(summary.source_samples) < _SAMPLE_LIMIT:
            summary.source_samples.append(f"{row[3]}：「{row[6]}」→「{row[4]}」")


def _classify_exclusions(rows: Sequence[tuple], summary: ExclusionSummary) -> None:
    """依每個目標的全部參考決定有沒有被擋住的差異；每個目標只計一個原因。

    先逐一評估目標的每個參考（是否已審核、語系是否相容、譯文是否與目標不同），再決定：

    - 有「已審核、語系相容、譯文不同」的參考：差異會被列出，不計。
    - 否則，有「已審核、譯文不同，但語系不相容」的參考：計為語系不相容（優先）。
    - 否則，有「尚未審核、譯文不同」的參考：計為參考尚未審核。

    相容但譯文相同的參考不構成差異，也不會掩蓋其他參考被擋住的差異。
    """
    by_target: dict[int, list[tuple]] = {}
    for row in rows:
        by_target.setdefault(int(row[0]), []).append(row)
    for refs in by_target.values():
        target_text = refs[0][2] or ""
        differing = [r for r in refs if r[3] != target_text]
        reviewed = [r for r in differing if r[4] in _REVIEWED_STATES]
        if any(r[5] for r in reviewed):
            continue  # 已經有可列出的差異
        if reviewed:
            summary.locale_incompatible += 1
            if str(refs[0][6]).startswith("unknown:"):
                summary.target_locale_unknown += 1
            if len(summary.samples) < _SAMPLE_LIMIT:
                key, target_loc, ref_loc = refs[0][1], refs[0][6], reviewed[0][7]
                summary.samples.append(
                    f"{key}（目標語系 {target_loc}、參考語系 {ref_loc}）"
                )
        elif differing:
            summary.ref_unreviewed += 1


def _write_reference(
    db: TranslationDB,
    conn: sqlite3.Connection,
    row: CrossVersionRow,
    ref: VersionRef,
    batch: str,
    actor: str,
) -> int:
    """把參考譯文以已審核狀態寫入目標條目，回傳歷史編號。

    目標尚未審核：寫入獨立的 ``SRC_CROSS_VERSION`` 列。已審核列一定贏過未審核列，
    目標原本的譯文與來源（例如模組自帶繁中）保留在自己的列，不會被覆寫。
    目標已經是已審核：改寫目標目前生效的那一列。否則新列與目標列同為已審核，誰生效
    取決於來源優先序，參考來源排序較低時套用後畫面不會變。
    """
    overwrite = row.target_reviewed and row.target_source is not None
    target_source = row.target_source if overwrite else SRC_CROSS_VERSION
    if target_source == SRC_MANUAL:
        checker = ref.checker or "人工"
    elif target_source == SRC_CROSS_VERSION:
        checker = ref.checker
    else:
        checker = ""
    prev = conn.execute(
        "SELECT zh_tw, checker, review_status, revision FROM translation "
        "WHERE entry_id=? AND source=?",
        (row.entry_id, target_source),
    ).fetchone()
    conn.execute(
        "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
        "VALUES (?,?,?,?, 'reviewed') "
        "ON CONFLICT(entry_id, source) DO UPDATE SET "
        "zh_tw=excluded.zh_tw, checker=excluded.checker, "
        "review_status='reviewed', updated_at=CURRENT_TIMESTAMP",
        (row.entry_id, target_source, ref.zh_tw, checker),
    )
    written = conn.execute(
        "SELECT checker, revision FROM translation WHERE entry_id=? AND source=?",
        (row.entry_id, target_source),
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
            target_source,
        ),
    )
    return int(cur.lastrowid or 0)


def reuse_reviewed(
    db: TranslationDB,
    version: str,
    *,
    entry_ids: Iterable[int],
    actor: str = "",
) -> ReuseResult:
    """匯入後自動沿用其他版本已審核的相同譯文。

    只處理 ``entry_ids``：本次匯入交易實際新增的條目或新增譯文的條目，不靠時間推測，
    所以同一秒內先前的資料不會被誤納入。參考譯文一致且目標尚未審核才寫入；
    衝突與「目標已審核且不同」只計數。同樣的匯入重跑不會再寫入（目標已等於參考）。
    """
    result = ReuseResult()
    ids = sorted({int(i) for i in entry_ids})
    if not db.has_review_state or not ids:
        return result
    batch = uuid.uuid4().hex
    with db._tx(bump_generation_on_noop=False, keep_review=True) as conn:
        for start in range(0, len(ids), _ID_CHUNK):
            rows = _fetch_rows(
                conn,
                version,
                ref_version=REFERENCE_LATEST,
                entry_ids=ids[start : start + _ID_CHUNK],
            )
            for row in rows:
                _reuse_one(db, conn, row, batch, actor, result)
        db._refresh(conn, result.entry_ids)
    return result


def _reuse_one(
    db: TranslationDB,
    conn: sqlite3.Connection,
    row: CrossVersionRow,
    batch: str,
    actor: str,
    result: ReuseResult,
) -> None:
    if not row.differs:
        return
    if row.conflict:
        result.conflicts += 1
    elif row.target_reviewed:
        result.skipped_reviewed += 1
    else:
        hid = _write_reference(db, conn, row, row.reference, batch, actor)
        result.applied += 1
        result.entry_ids.append(row.entry_id)
        result.history_id = result.history_id or hid


def apply_references(
    db: TranslationDB, items: Iterable[ApplyItem], *, actor: str = ""
) -> ReuseResult:
    """套用使用者選定的參考譯文（同一批可整批還原）。

    每一筆都在寫入的交易內重新讀取目前狀態；目標或參考與畫面所見不同（譯文、來源、
    審核狀態、revision 任一項）就略過並計入 ``stale``，不覆蓋較新的修改。
    """
    result = ReuseResult()
    if not db.has_review_state:
        return result
    batch = uuid.uuid4().hex
    with db._tx(bump_generation_on_noop=False, keep_review=True) as conn:
        for item in items:
            current = _current_row(conn, item)
            if current is None:
                result.stale += 1
                continue
            row, ref = current
            hid = _write_reference(db, conn, row, ref, batch, actor)
            result.applied += 1
            result.entry_ids.append(row.entry_id)
            result.history_id = result.history_id or hid
        db._refresh(conn, result.entry_ids)
    return result


def _current_row(
    conn: sqlite3.Connection, item: ApplyItem
) -> tuple[CrossVersionRow, VersionRef] | None:
    """依畫面期待值重新讀取一筆；目標或參考有任何關鍵狀態不同就回傳 None。"""
    head = conn.execute(
        "SELECT mc_version FROM entry WHERE id=?", (item.entry_id,)
    ).fetchone()
    if head is None:
        return None
    rows = _fetch_rows(
        conn,
        head[0],
        ref_version=item.ref_version,
        entry_ids=[item.entry_id],
    )
    row = next((r for r in rows if r.entry_id == item.entry_id), None)
    if row is None:
        return None
    if (
        row.target_zh_tw != item.expected_target_zh_tw
        or row.target_source != item.expected_target_source
        or row.target_reviewed != item.expected_target_reviewed
        or row.target_revision != item.expected_target_revision
    ):
        return None
    ref = next(
        (
            r
            for r in row.refs
            if r.entry_id == item.ref_entry_id
            and r.version == item.ref_version
            and r.zh_tw == item.expected_ref_zh_tw
            and r.revision == item.expected_ref_revision
        ),
        None,
    )
    return None if ref is None else (row, ref)


class CrossVersionReviewMixin:
    """``TranslationDB`` 的跨版本沿用／差異審核方法。"""

    def reuse_reviewed_translations(
        self, version: str, *, entry_ids: Iterable[int], actor: str = ""
    ) -> ReuseResult:
        return reuse_reviewed(self, version, entry_ids=entry_ids, actor=actor)  # type: ignore[arg-type]

    def list_cross_version_differences(
        self,
        version: str,
        *,
        ref_version: str = REFERENCE_LATEST,
        mod_ids: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        cancel: Callable[[], bool] | None = None,
        ref_pack: str = "",
        target_pack: str = "",
    ) -> list[CrossVersionRow]:
        return list_differences(
            self,  # type: ignore[arg-type]
            version,
            ref_version=ref_version,
            mod_ids=mod_ids,
            kinds=kinds,
            cancel=cancel,
            ref_pack=ref_pack,
            target_pack=target_pack,
        )

    def explain_cross_version_exclusions(
        self,
        version: str,
        *,
        ref_version: str = REFERENCE_LATEST,
        mod_ids: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        cancel: Callable[[], bool] | None = None,
        ref_pack: str = "",
        target_pack: str = "",
    ) -> ExclusionSummary:
        return explain_exclusions(
            self,  # type: ignore[arg-type]
            version,
            ref_version=ref_version,
            mod_ids=mod_ids,
            kinds=kinds,
            cancel=cancel,
            ref_pack=ref_pack,
            target_pack=target_pack,
        )

    def pack_versions(self, version: str) -> list[str]:
        """某個遊戲版本中出現過的整合包資料夾名稱（KubeJS／FTB；由模組清單推得，有快取）。"""
        return sorted(
            {
                label
                for mod_id in self.mods(version)  # type: ignore[attr-defined]
                if (label := pack_label(KIND_KUBEJS_LANG, mod_id))
            }
        )

    def pack_mods(self, version: str, pack: str) -> list[str]:
        """某個遊戲版本中屬於指定整合包資料夾的 KubeJS／FTB 模組識別。"""
        return [
            mod_id
            for mod_id in self.mods(version)  # type: ignore[attr-defined]
            if pack_label(KIND_KUBEJS_LANG, mod_id) == pack
        ]

    def apply_cross_version_references(
        self, items: Iterable[ApplyItem], *, actor: str = ""
    ) -> ReuseResult:
        return apply_references(self, items, actor=actor)  # type: ignore[arg-type]
