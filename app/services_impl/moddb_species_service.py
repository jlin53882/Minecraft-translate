"""學名候選掃描服務。

這個服務只讀取 Mod 翻譯資料庫，先以規則找出可能的學名，
再由 UI 明確觸發線上查詢。掃描本身不會修改任何資料庫內容。

只看「Genus species」的格式會把大量一般文字（Energy usage、Creeper dies）
誤判成學名，所以預設還要求 key 帶有學名提示（例如 ``.latin``），
或值本身有雜交／亞種／變種記號。需要時可放寬為只看格式。
"""

from __future__ import annotations

from dataclasses import dataclass

from translation_tool.translation_db import (
    EntryFilter,
    EntryRow,
    QualityFilter,
    TranslationDB,
)
from translation_tool.utils.species_cache import (
    STATUS_ERROR,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_RATE_LIMITED,
    SpeciesLookup,
    cached_species_lookup,
    lookup_species,
    lookup_species_name,
)
from translation_tool.utils.species_names import (
    SPECIES_KEY_HINTS,
    has_species_key_hint,
    is_potential_species_name,
    is_species_candidate,
)


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
    """目前範圍內所有的學名候選（不設上限，由 UI 分頁顯示）。"""

    candidates: tuple[SpeciesCandidate, ...]

    @property
    def total_candidates(self) -> int:
        return len(self.candidates)


def _candidate_from_row(
    row: EntryRow, *, require_key_hint: bool
) -> SpeciesCandidate | None:
    value = row.en_us or ""
    if not is_species_candidate(row.key, value, require_key_hint=require_key_hint):
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
    require_key_hint: bool = True,
) -> SpeciesScanResult:
    """找出指定版本／模組／類型中所有可能是學名的條目。

    先用資料庫的品質篩選（SQL 預篩＋規則判斷）只取出可能的條目，不逐筆載入整個版本，
    所以大型資料庫也能快速掃描；候選數量不設上限。
    """
    if db is None or not version:
        return SpeciesScanResult(())
    criteria = EntryFilter(
        version=version,
        mod_id=mod_id,
        kind=kind,
        quality=QualityFilter(
            status="species_name" if require_key_hint else "species_format"
        ),
    )
    rows, _total = db.list_entries(criteria=criteria, limit=None)
    candidates = (
        _candidate_from_row(row, require_key_hint=require_key_hint) for row in rows
    )
    return SpeciesScanResult(tuple(c for c in candidates if c is not None))


__all__ = [
    "SPECIES_KEY_HINTS",
    "STATUS_ERROR",
    "STATUS_FOUND",
    "STATUS_NOT_FOUND",
    "STATUS_RATE_LIMITED",
    "SpeciesCandidate",
    "SpeciesLookup",
    "SpeciesScanResult",
    "cached_species_lookup",
    "has_species_key_hint",
    "is_potential_species_name",
    "is_species_candidate",
    "lookup_species",
    "lookup_species_name",
    "scan_species_candidates",
]
