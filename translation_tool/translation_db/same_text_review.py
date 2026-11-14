"""同文一致性審核：同一個版本內，英文原文相同的條目，譯文卻不一致。

「原文相同」＝ :func:`content_identity.loose_text` 相同（不分大小寫、連續空白視為一個），
類型屬於同一個「同文類型群組」（:func:`content_identity.same_text_kinds`：模組 lang 與
整合包 KubeJS lang 互通，其他類型只比同類型），且來源語系相容（語系相同；語系未知的條目
不併入任何群組，兩個都沒有來源紀錄的舊條目視為相容，與 ``source_locale_match_sql`` 一致）。

- :func:`list_inconsistent_groups` 掃描一個遊戲版本，回傳「有 >= 2 種不同的非空譯文，或
  至少一種譯文加上尚未翻譯的成員」的群組。掃描依條目編號分段（走 ``idx_entry_version``，
  不需要臨時排序），段與段之間釋放資料庫鎖、回報進度並檢查是否取消；整個版本的成員
  只以輕量的 :class:`SameTextMember` 暫存在記憶體，結果只保留不一致的群組。
- 統一譯文只能從**已審核**的譯文中選（:class:`CanonicalCandidate`）；沒有已審核候選的群組
  照樣列出，但 ``applicable`` 為 False，不能套用。
- :func:`apply_canonical` 把統一譯文寫進使用者勾選的成員：在同一個交易內重新確認目標與
  統一譯文的狀態（譯文、來源、審核狀態、revision、原文），任何一項與畫面所見不同都略過；
  目標已審核者除非項目明確允許覆蓋，否則不動。

寫入規則與跨版本沿用一致：目標尚未審核時，另寫（或更新）一列 ``SRC_MANUAL`` 的已審核譯文
（已審核列贏過未審核列，其他來源的譯文保留在自己的列）；目標已經是已審核時，改寫目標目前
生效的那一列。使用 ``SRC_MANUAL`` 而不是 ``SRC_CROSS_VERSION``：這是使用者在同一個版本內
人工決定的統一，而不是「沿用其他版本」；人工來源也不會被之後的掃描匯入覆蓋。寫入沿用審核
流程的歷史格式（``action='review'``，備註「同文統一」），所以 ``TranslationDB.revert`` 可以
整批還原。不新增資料表、欄位或索引。
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .content_identity import (
    PACK_KINDS,
    loose_text,
    pack_scope_of,
    same_text_kinds,
)
from .query_interrupt import QueryCancelled
from .schema import SRC_CROSS_VERSION, SRC_MANUAL
from .sql_helpers import effective_review_sql, reference_locale_sql

if TYPE_CHECKING:
    import sqlite3

    from .repository import TranslationDB

MAX_SOURCE_CHARS = 80  # 預設只看短句：長文的譯文本來就不該強求一致
JAR_SOURCE_LOCALE = "en_us"
CATEGORY_INCONSISTENT = "inconsistent"
CATEGORY_FILL = "fill"
SAME_TEXT_NOTE = "同文統一"
_REVIEWED_STATES = ("reviewed", "legacy_unknown")
_REVIEW_ACTION = "review"
_ID_CHUNK = 500  # 一次處理的條目數：控制單次查詢大小，也是取消、回報進度與釋放鎖的間隔
SAME_TEXT_CHECKER = "同文統一"  # 寫入列的 checker：與真正的人工審核區分


@dataclass(frozen=True)
class SameTextMember:
    """群組中的一個條目與其目前生效譯文。"""

    entry_id: int
    kind: str
    mod_id: str
    key: str
    en_us: str
    zh_tw: str  # 生效譯文；尚未翻譯為空字串
    source: int | None
    reviewed: bool
    revision: int | None

    @property
    def translated(self) -> bool:
        return bool(self.zh_tw)


@dataclass(frozen=True)
class CanonicalCandidate:
    """可當統一譯文的已審核譯文（同一種譯文只列一次，附一個代表條目）。"""

    zh_tw: str
    entry_id: int  # 代表條目（已審核）：套用時重新確認它仍是這段已審核譯文
    revision: int
    member_count: int  # 目前生效譯文就是這段文字的成員數（含尚未審核者）
    reviewed_count: int
    manual: bool  # 代表條目是人工來源
    source: int | None = None  # 代表條目的生效來源（畫面標示用）


@dataclass(frozen=True)
class SameTextGroup:
    """原文相同、譯文不一致的一組條目。"""

    group_id: str
    en_us: str  # 第一個成員的原文（同組成員可能只差大小寫或空白）
    kinds: tuple[
        str, ...
    ]  # 這一組比對的類型群組（lang 與 kubejs_lang 同組，其他只有自己）
    locale: str
    members: tuple[SameTextMember, ...]
    candidates: tuple[CanonicalCandidate, ...]  # 預設建議排第一

    @property
    def applicable(self) -> bool:
        """至少有一個已審核候選才能統一。"""
        return bool(self.candidates)

    @property
    def default_candidate(self) -> CanonicalCandidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def distinct_translations(self) -> int:
        return len({m.zh_tw for m in self.members if m.zh_tw})

    @property
    def category(self) -> str:
        """``inconsistent``：>= 2 種不同的譯文；``fill``：一種譯文加上尚未翻譯的成員（補譯）。"""
        return (
            CATEGORY_INCONSISTENT if self.distinct_translations >= 2 else CATEGORY_FILL
        )

    @property
    def untranslated(self) -> int:
        return sum(1 for m in self.members if not m.zh_tw)

    def candidate(self, zh_tw: str | None) -> CanonicalCandidate | None:
        """指定譯文的候選；``None`` 或不在候選內回傳預設建議。"""
        for candidate in self.candidates:
            if candidate.zh_tw == zh_tw:
                return candidate
        return self.default_candidate

    def targets(self, zh_tw: str) -> list[SameTextMember]:
        """套用 ``zh_tw`` 時會被改動的成員：目前譯文與它不同者（含尚未翻譯）。"""
        return [m for m in self.members if m.zh_tw != zh_tw]


@dataclass(frozen=True)
class SameTextApplyItem:
    """套用統一譯文到一個成員；``expected_*`` 是畫面所見的狀態，任何一項不同就不寫入。"""

    entry_id: int
    zh_tw: str
    canonical_entry_id: int
    expected_canonical_revision: int
    expected_en_us: str
    expected_target_zh_tw: str
    expected_target_source: int | None
    expected_target_reviewed: bool
    expected_target_revision: int | None
    allow_overwrite_reviewed: bool = False

    @classmethod
    def from_member(
        cls,
        member: SameTextMember,
        candidate: CanonicalCandidate,
        *,
        allow_overwrite_reviewed: bool = False,
    ) -> SameTextApplyItem:
        return cls(
            member.entry_id,
            candidate.zh_tw,
            candidate.entry_id,
            candidate.revision,
            member.en_us,
            member.zh_tw,
            member.source,
            member.reviewed,
            member.revision,
            allow_overwrite_reviewed,
        )


@dataclass
class SameTextApplyResult:
    """套用統一譯文的結果。"""

    applied: int = 0
    skipped_reviewed: int = 0  # 目標已審核且項目不允許覆蓋
    unchanged: int = 0  # 目標譯文已經是統一譯文
    duplicates: int = 0  # 同一批內重複的條目（只套用第一筆）
    stale: int = 0  # 套用時畫面資料已過期而略過
    history_id: int | None = None  # 任一筆歷史編號，可傳給 ``TranslationDB.revert``
    entry_ids: list[int] = field(default_factory=list)


# ------------------------------------------------------------------ 掃描


def _scan_sql(kinds: Sequence[str], by_mod: bool) -> str:
    """第一階段：分段讀取條目與生效譯文（只取分群需要的欄位，不算來源語系）。

    指定索引讓 ``ORDER BY e.id`` 直接走索引順序：整個版本走 ``idx_entry_version``，
    單一模組走 ``idx_entry_mod``（兩者的 rowid 都是第二鍵），不會建立臨時排序。
    """
    index = "idx_entry_mod" if by_mod else "idx_entry_version"
    mod_clause = " AND e.mod_id = ?" if by_mod else ""
    kind_clause = f"AND e.kind IN ({','.join('?' * len(kinds))}) " if kinds else ""
    return (
        "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, ft.zh_tw "
        f"FROM entry e INDEXED BY {index} "
        "LEFT JOIN effective ft ON ft.entry_id = e.id "
        f"WHERE e.mc_version = ?{mod_clause} AND e.id > ? "
        f"{kind_clause}"
        "AND e.en_us <> '' AND length(e.en_us) <= ? "
        "ORDER BY e.id LIMIT ?"
    )


def _id_range(db: TranslationDB, version: str) -> tuple[int, int]:
    with db._lock:
        row = db._conn.execute(
            "SELECT MIN(id), MAX(id) FROM entry INDEXED BY idx_entry_version "
            "WHERE mc_version = ?",
            (version,),
        ).fetchone()
    return (int(row[0]), int(row[1])) if row and row[0] is not None else (0, 0)


def _scan_chunks(
    db: TranslationDB,
    version: str,
    mod_id: str | None,
    kinds: Sequence[str],
    max_chars: int,
    cancel: Callable[[], bool] | None,
) -> Iterator[list[tuple]]:
    """依條目編號分段讀取；每段之間釋放資料庫鎖並檢查是否取消。"""
    sql = _scan_sql(kinds, mod_id is not None)
    last = 0
    while True:
        if cancel is not None and cancel():
            raise QueryCancelled
        args = [version, *([mod_id] if mod_id is not None else []), last, *kinds]
        args += [max_chars, _ID_CHUNK]
        with db._lock:
            rows = db._conn.execute(sql, args).fetchall()
        if not rows:
            return
        yield rows
        last = int(rows[-1][0])


def _candidates(members: Sequence[SameTextMember]) -> tuple[CanonicalCandidate, ...]:
    """成員中已審核的譯文，依「人工來源優先、成員數多、已審核成員多」排序。"""
    by_text: dict[str, list[SameTextMember]] = {}
    for member in members:
        if member.zh_tw:
            by_text.setdefault(member.zh_tw, []).append(member)
    out: list[CanonicalCandidate] = []
    for text, same in by_text.items():
        reviewed = [m for m in same if m.reviewed and m.revision is not None]
        if not reviewed:
            continue
        # 代表條目：人工來源優先，再依條目編號，結果與掃描順序無關
        rep = min(reviewed, key=lambda m: (m.source != SRC_MANUAL, m.entry_id))
        out.append(
            CanonicalCandidate(
                text,
                rep.entry_id,
                int(rep.revision or 0),
                len(same),
                len(reviewed),
                rep.source == SRC_MANUAL,
                rep.source,
            )
        )
    out.sort(key=lambda c: (not c.manual, -c.member_count, -c.reviewed_count, c.zh_tw))
    return tuple(out)


def _texts_inconsistent(texts: Sequence[str]) -> bool:
    """>= 2 種不同的非空譯文，或一種譯文加上尚未翻譯的成員。"""
    translations = {t for t in texts if t}
    return len(translations) >= 2 or (
        len(translations) == 1 and any(not t for t in texts)
    )


def _fast_loose(text: str) -> str:
    """:func:`loose_text` 的快速路徑：沒有 ``%``（格式控制碼）與換行時只需轉小寫並收合空白。"""
    if "%" in text or "\n" in text or "\r" in text:
        return loose_text(text)
    return " ".join(text.casefold().split())


def _detail_sql(count: int) -> str:
    return (
        "SELECT e.id, ft.source, "
        f"{effective_review_sql('ft', True)}, tt.revision, {reference_locale_sql('e')} "
        "FROM entry e "
        "LEFT JOIN effective ft ON ft.entry_id = e.id "
        "LEFT JOIN translation tt ON tt.entry_id = e.id AND tt.source = ft.source "
        f"WHERE e.id IN ({','.join('?' * count)})"
    )


def _fetch_details(
    db: TranslationDB, ids: Sequence[int], cancel: Callable[[], bool] | None
) -> dict[int, tuple]:
    """第二階段：只替候選群組的成員讀來源、審核狀態、revision 與來源語系（主鍵查找）。"""
    out: dict[int, tuple] = {}
    for start in range(0, len(ids), _ID_CHUNK):
        if cancel is not None and cancel():
            raise QueryCancelled
        chunk = ids[start : start + _ID_CHUNK]
        with db._lock:
            for row in db._conn.execute(_detail_sql(len(chunk)), chunk):
                out[int(row[0])] = row[1:]
    return out


def _member(row: tuple, detail: tuple) -> SameTextMember:
    source, status, revision, _locale = detail
    return SameTextMember(
        int(row[0]),
        row[1],
        row[2],
        row[3],
        row[4],
        row[5] or "",
        None if source is None else int(source),
        status in _REVIEWED_STATES,
        None if revision is None else int(revision),
    )


def _member_locale(kind: str, locale: str) -> str | None:
    """成員併組用的來源語系；語系未知回傳 None（不併入任何群組）。"""
    if locale.startswith("unknown:"):
        return None
    if not locale and kind not in PACK_KINDS:
        return JAR_SOURCE_LOCALE  # 模組 jar 的 lang 不記錄來源紀錄，原文一定是 en_us
    return locale


def _regroup_with_locale(
    candidates: Sequence[list[tuple]],
    details: dict[int, tuple],
    loose: dict[str, str],
) -> list[SameTextGroup]:
    """候選群組依來源語系再分組，並只保留不一致者（成員維持條目編號順序）。"""
    groups: dict[tuple, list[SameTextMember]] = {}
    sources: dict[tuple, str] = {}
    for rows in candidates:
        for row in rows:
            detail = details.get(int(row[0]))
            locale = None if detail is None else _member_locale(row[1], str(detail[3]))
            if locale is None:
                continue
            key = (same_text_kinds(row[1]), locale, loose[row[4]])
            groups.setdefault(key, []).append(_member(row, detail))
            sources.setdefault(key, row[4])
    return [
        SameTextGroup(
            "\x1f".join((",".join(key[0]), key[1], key[2])),
            sources[key],
            key[0],
            key[1],
            tuple(members),
            _candidates(members),
        )
        for key, members in groups.items()
        if len(members) >= 2 and _texts_inconsistent([m.zh_tw for m in members])
    ]


def _case_variants(text: str) -> list[str]:
    """用 ``idx_entry_en`` 找寬鬆相同原文時要查的字串：原樣、收合空白與常見的大小寫寫法。

    寬鬆比對（大小寫、空白）無法用 B-tree 索引表達，所以只查這些常見寫法：原樣、全小寫、
    全大寫、每字首大寫、僅句首大寫，各搭配「原樣」與「去頭尾並收合空白」兩種空白。
    其他混合大小寫（例如 ``AD Astra``）或帶多餘空白的變體不會被找到。
    """
    bases = [text, text.strip(), " ".join(text.split())]
    out: list[str] = []
    for base in bases:
        for variant in (
            base,
            base.lower(),
            base.upper(),
            base.title(),
            base.capitalize(),
        ):
            if variant and variant not in out:
                out.append(variant)
    return out


def _rows_by_en_us(
    db: TranslationDB,
    version: str,
    wanted_text: str,
    mod_ids: Sequence[str] | None,
    kinds: Sequence[str],
    max_chars: int,
) -> list[tuple]:
    """以 ``idx_entry_en`` 依原文取候選條目（不掃整個版本），結果依條目編號排序。"""
    variants = _case_variants(wanted_text)
    sql = (
        "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, ft.zh_tw "
        "FROM entry e INDEXED BY idx_entry_en "
        "LEFT JOIN effective ft ON ft.entry_id = e.id "
        f"WHERE e.en_us IN ({','.join('?' * len(variants))}) AND e.mc_version = ? "
        "AND length(e.en_us) <= ?"
    )
    args: list[object] = [*variants, version, max_chars]
    if mod_ids:
        sql += f" AND e.mod_id IN ({','.join('?' * len(mod_ids))})"
        args += list(mod_ids)
    if kinds:
        sql += f" AND e.kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    with db._lock:
        rows = db._conn.execute(sql, args).fetchall()
    return sorted(rows, key=lambda r: int(r[0]))


_CACHE_SIZE = 4  # 每個資料庫實例最多保留幾組掃描結果


def _read_generation(db: TranslationDB):
    """目前的資料世代；取不到時回傳 None（不使用快取）。"""
    reader = getattr(db, "cache_generation_nowait", None) or getattr(
        db, "_cache_generation", None
    )
    return None if reader is None else reader()


def _cache_key(
    db: TranslationDB, version: str, mod_ids, pack_scope, kinds, en_us, max_chars
) -> tuple | None:
    """掃描結果快取的鍵：資料世代加上全部查詢條件；取不到世代時不使用快取。

    世代讀取用不等主連線鎖的版本（畫面執行緒也能安全呼叫）。
    """
    generation = _read_generation(db)
    if generation is None:
        return None
    return (
        generation,
        version,
        tuple(mod_ids or ()),
        pack_scope,
        tuple(kinds or ()),
        en_us,
        max_chars,
    )


def _cache_of(db: TranslationDB) -> tuple[OrderedDict, threading.Lock]:
    """資料庫實例上的結果快取（Python 物件，不經 ``READ_CACHE``）；以世代失效。"""
    cache = db.__dict__.setdefault("_same_text_cache", OrderedDict())
    lock = db.__dict__.setdefault("_same_text_cache_lock", threading.Lock())
    return cache, lock


def _scan_groups(
    db: TranslationDB,
    version: str,
    *,
    mod_ids: Sequence[str] | None = None,
    pack_scope: str = "",
    kinds: Sequence[str] | None = None,
    en_us: str | None = None,
    max_chars: int = MAX_SOURCE_CHARS,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[float], None] | None = None,
) -> list[SameTextGroup]:
    """某個遊戲版本中，原文相同但譯文不一致的群組（成員數多的在前）。

    - ``mod_ids``：只掃這些模組的條目（群組只含這些模組的成員）；
    - ``pack_scope``：模組識別帶模組包身分（``命名空間@資料夾@路徑雜湊``，KubeJS／FTB 各類型）的
      條目只取該模組包身分的條目，模組 jar 的 lang 不分模組包版本；
    - ``kinds``：只掃這些類型；預設掃全部類型（各類型只和 ``same_text_kinds`` 同群組的類型分組）；
    - ``en_us``：只看與這段原文（寬鬆比對）相同的群組。這條路徑不掃整個版本，改用
      ``idx_entry_en`` 查原文的常見寫法（見 :func:`_case_variants`），其他混合大小寫或帶
      多餘空白的寫法不會被找到；
    - ``max_chars``：原文超過這個長度的條目不納入；
    - ``cancel`` 回傳 True 時拋出 :class:`QueryCancelled`；``progress`` 以 0..1 回報進度。

    分兩個階段：第一階段只讀分群需要的欄位，以 ``(類型群組, 寬鬆原文)`` 分群並留下「不分語系
    也不一致」的群組（語系只會把群組切小，所以這是不一致群組的超集）；第二階段只替這些候選
    成員讀來源、審核狀態、revision 與來源語系（主鍵查找，來源語系的子查詢不必對整個版本執行），
    再依來源語系分組並判斷。掃描走編號順序而不是原文順序（寬鬆比對無法用索引表達），沒有
    臨時排序；結果只保留不一致的群組。
    """
    if not db.has_review_state:
        return []
    scan_kinds = tuple(kinds) if kinds else ()
    groups: dict[tuple, list[tuple]] = {}
    loose: dict[str, str] = {}  # 原文 → 寬鬆形式（很多條目的原文完全相同，只算一次）
    if en_us is not None:
        rows = _rows_by_en_us(db, version, en_us, mod_ids, scan_kinds, max_chars)
        _collect(rows, groups, _fast_loose(en_us), pack_scope, loose)
    else:
        _scan_into(
            db,
            version,
            mod_ids,
            scan_kinds,
            max_chars,
            pack_scope,
            groups,
            loose,
            cancel,
            progress,
        )
    candidates = [
        rows
        for rows in groups.values()
        if len(rows) >= 2 and _texts_inconsistent([r[5] or "" for r in rows])
    ]
    ids = [int(r[0]) for rows in candidates for r in rows]
    details = _fetch_details(db, ids, cancel)
    out = _regroup_with_locale(candidates, details, loose)
    if progress is not None:
        progress(1.0)
    out.sort(key=lambda g: (-len(g.members), g.group_id))
    return out


def list_inconsistent_groups(
    db: TranslationDB,
    version: str,
    *,
    mod_ids: Sequence[str] | None = None,
    pack_scope: str = "",
    kinds: Sequence[str] | None = None,
    en_us: str | None = None,
    max_chars: int = MAX_SOURCE_CHARS,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[float], None] | None = None,
) -> list[SameTextGroup]:
    """:func:`_scan_groups` 加上以資料世代為鍵的結果快取。

    鍵含資料世代與全部查詢條件，資料一變（包含對話框自己的套用與復原）就自然失效並重掃；
    沒有變動時重新開啟對話框直接取快取。結果是不可變物件，快取只留最近 ``_CACHE_SIZE`` 組。
    世代在掃描前讀取；掃描途中資料有變（世代已前進）的結果不存入快取。
    """
    key = _cache_key(db, version, mod_ids, pack_scope, kinds, en_us, max_chars)
    cache, lock = _cache_of(db)
    if key is not None:
        with lock:
            hit = cache.get(key)
            if hit is not None:
                cache.move_to_end(key)
        if hit is not None:
            if progress is not None:
                progress(1.0)
            return list(hit)
    groups = _scan_groups(
        db,
        version,
        mod_ids=mod_ids,
        pack_scope=pack_scope,
        kinds=kinds,
        en_us=en_us,
        max_chars=max_chars,
        cancel=cancel,
        progress=progress,
    )
    if key is not None and _read_generation(db) == key[0]:
        with lock:
            cache[key] = tuple(groups)
            while len(cache) > _CACHE_SIZE:
                cache.popitem(last=False)
    return groups


def _scan_into(
    db: TranslationDB,
    version: str,
    mod_ids: Sequence[str] | None,
    kinds: tuple[str, ...],
    max_chars: int,
    pack_scope: str,
    groups: dict[tuple, list[tuple]],
    loose: dict[str, str],
    cancel: Callable[[], bool] | None,
    progress: Callable[[float], None] | None,
) -> None:
    """第一階段：分段掃描整個版本（或指定模組）並分群。"""
    scopes: list[str | None] = list(mod_ids) if mod_ids else [None]
    low, high = _id_range(db, version) if scopes == [None] else (0, 0)
    for index, mod in enumerate(scopes):
        for rows in _scan_chunks(db, version, mod, kinds, max_chars, cancel):
            _collect(rows, groups, None, pack_scope, loose)
            if progress is not None:
                progress(_fraction(index, len(scopes), int(rows[-1][0]), low, high))


def _fraction(index: int, total: int, last_id: int, low: int, high: int) -> float:
    """第一階段的進度（占整體 95%）：整個版本依編號範圍估算；逐模組掃描依已完成的模組數。"""
    if total > 1 or high <= low:
        return min(0.95, index / total)
    return min(0.95, (last_id - low) / (high - low + 1))


def _collect(
    rows: Sequence[tuple],
    groups: dict[tuple, list[tuple]],
    wanted: str | None,
    pack_scope: str,
    loose: dict[str, str],
) -> None:
    """把一段查詢結果依（類型群組, 寬鬆原文）分群；非指定模組包、不是指定原文的條目略過。"""
    kind_groups: dict[str, tuple[str, ...]] = {}
    for row in rows:
        kind, en = row[1], row[4]
        if pack_scope:
            owner = pack_scope_of(row[2])  # kubejs_script 等類型的識別也帶 @資料夾@雜湊
            if owner and owner != pack_scope:
                continue
        key = loose.get(en)
        if key is None:
            key = loose[en] = _fast_loose(en)
        if wanted is not None and key != wanted:
            continue
        kinds = kind_groups.get(kind)
        if kinds is None:
            kinds = kind_groups[kind] = same_text_kinds(kind)
        groups.setdefault((kinds, key), []).append(row)


# ------------------------------------------------------------------ 套用


def _current_target(conn: sqlite3.Connection, item: SameTextApplyItem) -> tuple | None:
    return conn.execute(
        "SELECT e.en_us, ft.zh_tw, ft.source, "
        f"{effective_review_sql('ft', True)}, tt.revision "
        "FROM entry e LEFT JOIN effective ft ON ft.entry_id = e.id "
        "LEFT JOIN translation tt ON tt.entry_id = e.id AND tt.source = ft.source "
        "WHERE e.id = ?",
        (item.entry_id,),
    ).fetchone()


def _current_canonical(
    conn: sqlite3.Connection, item: SameTextApplyItem
) -> tuple | None:
    return conn.execute(
        "SELECT ft.zh_tw, ft.checker, "
        f"{effective_review_sql('ft', True)}, tt.revision, e.en_us "
        "FROM effective ft JOIN entry e ON e.id = ft.entry_id "
        "LEFT JOIN translation tt ON tt.entry_id = ft.entry_id AND tt.source = ft.source "
        "WHERE ft.entry_id = ?",
        (item.canonical_entry_id,),
    ).fetchone()


def _target_unchanged(item: SameTextApplyItem, target: tuple) -> bool:
    """目標的原文、譯文、來源、審核狀態與 revision 都與畫面所見相同。"""
    en_us, zh_tw, source, status, revision = target
    return (
        en_us == item.expected_en_us
        and (zh_tw or "") == item.expected_target_zh_tw
        and (None if source is None else int(source)) == item.expected_target_source
        and (status in _REVIEWED_STATES) == item.expected_target_reviewed
        and (None if revision is None else int(revision))
        == item.expected_target_revision
    )


def _canonical_unchanged(
    item: SameTextApplyItem, canonical: tuple, target_en_us: str
) -> bool:
    """統一譯文的來源條目仍是同一段已審核譯文（revision 也沒變），且原文與目標寬鬆相同。"""
    return (
        _fast_loose(canonical[4]) == _fast_loose(target_en_us)
        and canonical[0] == item.zh_tw
        and canonical[2] in _REVIEWED_STATES
        and int(canonical[3] or 0) == item.expected_canonical_revision
    )


def _check(conn: sqlite3.Connection, item: SameTextApplyItem) -> str:
    """重新確認一筆；結果是 ``ok``、``stale``、``reviewed``（目標已審核且不允許覆蓋）
    或 ``unchanged``。"""
    target = _current_target(conn, item)
    canonical = _current_canonical(conn, item)
    if (
        target is None
        or canonical is None
        or item.entry_id == item.canonical_entry_id
        or not _target_unchanged(item, target)
        or not _canonical_unchanged(item, canonical, target[0])
    ):
        return "stale"
    if (target[1] or "") == item.zh_tw:
        return "unchanged"
    if item.expected_target_reviewed and not item.allow_overwrite_reviewed:
        return "reviewed"
    return "ok"


def _write_canonical(
    conn: sqlite3.Connection,
    item: SameTextApplyItem,
    batch: str,
    actor: str,
) -> int:
    """把統一譯文以已審核狀態寫入目標條目，回傳歷史編號。

    目標尚未審核：寫入 ``SRC_MANUAL`` 列（已審核列贏過未審核列）。目標已經是已審核：
    改寫目標目前生效的那一列，否則新列與目標列同為已審核，誰生效取決於來源優先序，
    套用後畫面可能不變。
    """
    overwrite = (
        item.expected_target_reviewed and item.expected_target_source is not None
    )
    target_source = item.expected_target_source if overwrite else SRC_MANUAL
    # 與 cross_version_review 的規則一致：人工列與跨版本列帶可辨識的 checker，
    # 其他來源（模組自帶譯文等）的 checker 維持空字串
    checker = (
        SAME_TEXT_CHECKER if target_source in (SRC_MANUAL, SRC_CROSS_VERSION) else ""
    )
    prev = conn.execute(
        "SELECT zh_tw, checker, review_status, revision FROM translation "
        "WHERE entry_id=? AND source=?",
        (item.entry_id, target_source),
    ).fetchone()
    conn.execute(
        "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
        "VALUES (?,?,?,?, 'reviewed') "
        "ON CONFLICT(entry_id, source) DO UPDATE SET "
        "zh_tw=excluded.zh_tw, checker=excluded.checker, "
        "review_status='reviewed', updated_at=CURRENT_TIMESTAMP",
        (item.entry_id, target_source, item.zh_tw, checker),
    )
    written = conn.execute(
        "SELECT checker, revision FROM translation WHERE entry_id=? AND source=?",
        (item.entry_id, target_source),
    ).fetchone()
    cur = conn.execute(
        "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, new_zh_tw, "
        "prev_manual, note, prev_checker, prev_review_status, new_checker, "
        "new_review_status, prev_revision, new_revision, source_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            item.entry_id,
            batch,
            actor,
            _REVIEW_ACTION,
            item.expected_target_zh_tw,
            item.zh_tw,
            prev[0] if prev else None,
            SAME_TEXT_NOTE,
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


def apply_canonical(
    db: TranslationDB, items: Iterable[SameTextApplyItem], *, actor: str = ""
) -> SameTextApplyResult:
    """套用使用者選定的統一譯文（同一批可整批還原）。

    每一筆都在寫入的交易內重新讀取目前狀態；目標或統一譯文與畫面所見不同（譯文、來源、
    審核狀態、revision、原文任一項）就略過並計入 ``stale``，不覆蓋較新的修改。
    """
    result = SameTextApplyResult()
    if not db.has_review_state:
        return result
    batch = uuid.uuid4().hex
    with db._tx(bump_generation_on_noop=False, keep_review=True) as conn:
        seen: set[int] = set()
        for item in items:
            if item.entry_id in seen:
                result.duplicates += (
                    1  # 生效譯文到交易結束才重整，重複項目的 CAS 會讀到舊狀態
                )
                continue
            seen.add(item.entry_id)
            outcome = _check(conn, item)
            if outcome == "stale":
                result.stale += 1
            elif outcome == "reviewed":
                result.skipped_reviewed += 1
            elif outcome == "unchanged":
                result.unchanged += 1
            else:
                hid = _write_canonical(conn, item, batch, actor)
                result.applied += 1
                result.entry_ids.append(item.entry_id)
                result.history_id = result.history_id or hid
        db._refresh(conn, result.entry_ids)
    return result


class SameTextReviewMixin:
    """``TranslationDB`` 的同文一致性審核方法。"""

    def list_same_text_groups(
        self,
        version: str,
        *,
        mod_ids: Sequence[str] | None = None,
        pack_scope: str = "",
        kinds: Sequence[str] | None = None,
        en_us: str | None = None,
        max_chars: int = MAX_SOURCE_CHARS,
        cancel: Callable[[], bool] | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> list[SameTextGroup]:
        return list_inconsistent_groups(
            self,  # type: ignore[arg-type]
            version,
            mod_ids=mod_ids,
            pack_scope=pack_scope,
            kinds=kinds,
            en_us=en_us,
            max_chars=max_chars,
            cancel=cancel,
            progress=progress,
        )

    def apply_same_text_canonical(
        self, items: Iterable[SameTextApplyItem], *, actor: str = ""
    ) -> SameTextApplyResult:
        return apply_canonical(self, items, actor=actor)  # type: ignore[arg-type]
