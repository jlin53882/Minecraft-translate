"""翻譯來源的模組包對照：把只有譯文的模組包內容，補進資料庫已有的同一個模組包。

KubeJS／FTB Quests 條目的身分含模組包身分（``ftbquests@<名稱>@<路徑雜湊>``），雜湊由
原始路徑算出。翻譯 ZIP 或搬過位置的資料夾算出的身分和資料庫裡不同，同樣的 key 會被當成
另一批條目重新新增，而且 ZIP 沒有英文時，中文會被存成原文。

這裡用「key 重疊度」找出資料庫同版本中唯一對得上的既有模組包：找到才沿用它的身分，並讓
已存在的條目只補譯文、保留原本的英文；找不到、有多個候選或重疊不足時不動，維持新增。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from translation_tool.translation_db.models import ScanItem

if TYPE_CHECKING:
    from translation_tool.translation_db.repository import TranslationDB

# 掃描到的來源只有譯文（沒有 en_us）時的來源語系
TRANSLATED_LOCALES = frozenset({"zh_tw", "zh_cn"})
MIN_OVERLAP_RATIO = 0.5


@dataclass(frozen=True)
class ScopeMatch:
    scope: str
    existing: frozenset[tuple[str, str, str]]  # (kind, mod_id, key)：資料庫已有的條目
    overlap: int
    total: int


def locale_only_items(items: list[ScanItem]) -> list[ScanItem]:
    return [item for item in items if item.source_locale in TRANSLATED_LOCALES]


def match_existing_scope(
    db: TranslationDB,
    version: str,
    items: list[ScanItem],
    *,
    min_ratio: float = MIN_OVERLAP_RATIO,
) -> ScopeMatch | None:
    """找出 key 重疊度達門檻且唯一最高的既有模組包；沒有就回 ``None``。"""
    candidates = locale_only_items(items)
    if not candidates:
        return None
    wanted: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in candidates:
        wanted[(item.kind, item.mod_id.partition("@")[0])].add(item.key)
    overlap: dict[str, int] = defaultdict(int)
    existing: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for (kind, prefix), keys in wanted.items():
        for scope, scope_keys in db.pack_entry_keys(version, kind, prefix).items():
            hits = keys & scope_keys
            overlap[scope] += len(hits)
            existing[scope].update((kind, f"{prefix}@{scope}", key) for key in hits)
    ranked = sorted(overlap.items(), key=lambda pair: pair[1], reverse=True)
    if not ranked or ranked[0][1] == 0:
        return None
    best_scope, best = ranked[0]
    if best / len(candidates) < min_ratio:
        return None
    if len(ranked) > 1 and ranked[1][1] == best:
        return None  # 兩個以上同樣吻合：不猜
    return ScopeMatch(
        best_scope, frozenset(existing[best_scope]), best, len(candidates)
    )


def keep_existing_originals(items: list[ScanItem], match: ScopeMatch) -> list[ScanItem]:
    """對到既有條目的項目：中文當譯文、原文留空，ingest 會沿用資料庫裡的英文只補譯文。"""
    out: list[ScanItem] = []
    for item in items:
        known = (item.kind, item.mod_id, item.key) in match.existing
        if known and item.source_locale == "zh_tw":
            item = replace(item, en_us="", zh_tw=item.zh_tw or item.en_us)
        elif known and item.source_locale == "zh_cn":
            item = replace(item, en_us="", zh_cn=item.zh_cn or item.en_us)
        out.append(item)
    return out
