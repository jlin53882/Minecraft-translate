"""學名候選掃描服務。

這個服務只讀取 Mod 翻譯資料庫，先以格式規則找出可能的學名，
再由 UI 明確觸發線上查詢。掃描本身不會修改任何資料庫內容。
"""

from __future__ import annotations

from dataclasses import dataclass

from translation_tool.translation_db import EntryRow, TranslationDB
from translation_tool.utils.species_cache import (
    STATUS_ERROR,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_RATE_LIMITED,
    SpeciesLookup,
    is_potential_species_name,
    lookup_species,
    lookup_species_name,
)

SCAN_PAGE_SIZE = 1000
DEFAULT_SCAN_LIMIT = 500


@dataclass(frozen=True)
class SpeciesCandidate:
    """一筆可能是學名的 Mod 條目。"""

    entry_id: int
    mc_version: str
    mod_id: str
    kind: str
    key: str
    value: str


@dataclass(frozen=True)
class SpeciesScanResult:
    """目前範圍的掃描結果與是否達到安全上限。"""

    candidates: tuple[SpeciesCandidate, ...]
    scanned_entries: int
    total_candidates: int
    truncated: bool


def _candidate_from_row(row: EntryRow) -> SpeciesCandidate | None:
    value = row.en_us or ""
    if not is_potential_species_name(value):
        return None
    return SpeciesCandidate(
        entry_id=row.id,
        mc_version=row.mc_version,
        mod_id=row.mod_id,
        kind=row.kind,
        key=row.key,
        value=" ".join(value.split()),
    )


def scan_species_candidates(
    db: TranslationDB | None,
    version: str | None,
    *,
    mod_id: str | None = None,
    kind: str | None = None,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> SpeciesScanResult:
    """掃描指定版本／模組／類型中的英文原文，回傳可能的學名條目。

    ``limit`` 只限制候選列數；``scanned_entries`` 仍記錄實際檢查過的條目數。
    達到上限後停止繼續查詢，讓大型資料庫的 UI 操作有可預期的時間。
    """
    if db is None or not version:
        return SpeciesScanResult((), 0, 0, False)
    if limit < 1:
        raise ValueError("學名候選上限必須大於 0")

    candidates: list[SpeciesCandidate] = []
    scanned = 0
    offset = 0
    truncated = False
    while True:
        rows, total = db.list_entries(
            version,
            mod_id=mod_id,
            kind=kind,
            state="all",
            limit=SCAN_PAGE_SIZE,
            offset=offset,
        )
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            candidate = _candidate_from_row(row)
            if candidate is None:
                continue
            if len(candidates) < limit:
                candidates.append(candidate)
            else:
                truncated = True
                break
        if truncated or offset + len(rows) >= total:
            break
        offset += len(rows)

    return SpeciesScanResult(
        tuple(candidates),
        scanned,
        len(candidates),
        truncated,
    )


__all__ = [
    "DEFAULT_SCAN_LIMIT",
    "SCAN_PAGE_SIZE",
    "STATUS_ERROR",
    "STATUS_FOUND",
    "STATUS_NOT_FOUND",
    "STATUS_RATE_LIMITED",
    "SpeciesCandidate",
    "SpeciesLookup",
    "SpeciesScanResult",
    "is_potential_species_name",
    "lookup_species",
    "lookup_species_name",
    "scan_species_candidates",
]
