"""翻譯來源的模組包對照：把只有譯文的模組包內容，補進資料庫已有的同一個模組包。

KubeJS／FTB Quests 條目的身分含模組包身分（``ftbquests@<名稱>@<路徑雜湊>``），雜湊由
原始路徑算出。翻譯 ZIP 或搬過位置的資料夾算出的身分和資料庫裡不同，同樣的 key 會被當成
另一批條目重新新增，而且 ZIP 沒有英文時，中文會被存成原文。

這裡用「key 重疊度」找出資料庫同版本中唯一對得上的既有模組包：找到才沿用它的身分，並讓
已存在的條目只補譯文、保留原本的英文，該模組包沒有的 key 略過不匯入；找不到、有多個候選或
重疊不足時不動，維持新增。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from translation_tool.core.lang_merge_dict import contains_cjk
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.schema import KIND_FTB_QUESTS, KIND_KUBEJS_SCRIPT

if TYPE_CHECKING:
    from translation_tool.translation_db.repository import TranslationDB

# 掃描到的來源只有譯文（沒有 en_us）時的來源語系
TRANSLATED_LOCALES = frozenset({"zh_tw", "zh_cn"})
MIN_OVERLAP_RATIO = 0.5
# 文字直接寫在 SNBT／JS 裡、沒有語系檔的類型：來源若已就地翻成中文，原文欄會是中文
IN_PLACE_KINDS = frozenset({KIND_FTB_QUESTS, KIND_KUBEJS_SCRIPT})


@dataclass(frozen=True)
class ScopeMatch:
    scope: str
    existing: frozenset[tuple[str, str, str]]  # (kind, mod_id, key)：資料庫已有的條目
    overlap: int
    total: int


def _is_in_place_translation(item: ScanItem) -> bool:
    return (
        item.kind in IN_PLACE_KINDS
        and not item.source_locale
        and contains_cjk(item.en_us)
    )


def locale_only_items(items: list[ScanItem]) -> list[ScanItem]:
    """只有譯文的項目：語言檔的 zh_tw／zh_cn 來源，或就地翻成中文的 SNBT／JS 文字。"""
    return [item for item in items if _is_locale_only(item)]


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
    total = sum(len(keys) for keys in wanted.values())
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
    if best / total < min_ratio:
        return None
    if len(ranked) > 1 and ranked[1][1] == best:
        return None  # 兩個以上同樣吻合：不猜
    return ScopeMatch(best_scope, frozenset(existing[best_scope]), best, total)


def _is_locale_only(item: ScanItem) -> bool:
    return item.source_locale in TRANSLATED_LOCALES or _is_in_place_translation(item)


def carries_translation(items: list[ScanItem], match: ScopeMatch) -> bool:
    """對到的既有條目中，這份輸入是否帶了中文譯文。

    既有條目的原文都不是中文（見 ``TranslationDB.pack_entry_keys``），所以輸入是中文就
    一定是譯文而不是同一份原文。
    """
    return any(
        (item.kind, item.mod_id, item.key) in match.existing
        and contains_cjk(item.zh_tw or item.zh_cn or item.en_us)
        for item in items
    )


def keep_existing_originals(
    items: list[ScanItem], match: ScopeMatch
) -> tuple[list[ScanItem], list[ScanItem]]:
    """回傳 ``(要匯入的項目, 略過的項目)``。

    對到既有條目的項目：中文當譯文、原文留空，ingest 會沿用資料庫裡的英文只補譯文。
    沒對到既有條目、又只有譯文的項目略過：它沒有英文原文，寫進去會把中文存成既有模組包
    的原文，之後原模組包出現這個 key 的英文時會被當成原文已變動。

    所有項目都不再記來源（路徑、selector）：這份輸入不是原模組包的檔案，記進去會讓
    原模組包多出不存在的來源，或用譯文檔的雜湊蓋掉原本英文來源的雜湊，破壞之後的回寫比對。
    """
    out: list[ScanItem] = []
    skipped: list[ScanItem] = []
    for item in items:
        if (item.kind, item.mod_id, item.key) not in match.existing:
            if _is_locale_only(item):
                skipped.append(item)
            else:
                out.append(replace(item, source_path="", source_selector=""))
            continue
        # 譯文輸入的檔案不是原模組包的檔案（含混合檔裡沒翻的英文項目）：全部不記來源
        item = replace(item, source_path="", source_selector="")
        if item.source_locale == "zh_tw":
            item = replace(item, en_us="", zh_tw=item.zh_tw or item.en_us)
        elif item.source_locale == "zh_cn":
            item = replace(item, en_us="", zh_cn=item.zh_cn or item.en_us)
        elif _is_in_place_translation(item):
            item = replace(item, en_us="", zh_tw=item.en_us)
        out.append(item)
    return out, skipped
