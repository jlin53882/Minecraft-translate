"""翻譯來源的模組包對照：把只有譯文的模組包內容，補進資料庫已有的同一個模組包。

KubeJS／FTB Quests 條目的身分含模組包身分（``ftbquests@<名稱>@<路徑雜湊>``），雜湊由
原始路徑算出。翻譯 ZIP 或搬過位置的資料夾算出的身分和資料庫裡不同，同樣的 key 會被當成
另一批條目重新新增，而且 ZIP 沒有英文時，中文會被存成原文。

這裡用「key 重疊度」找出資料庫同版本中對得上的既有模組包（同一個模組包的不同版本可能
同時存在，所以可以不只一個），已存在的條目只補譯文、保留原本的英文。譯文輸入沒有英文，
無法得知它是照哪一份原文翻的，所以逐筆比對各模組包的英文：

- 各模組包的英文相同：每一份都補。
- 英文不同：只補進重疊度唯一最高的那一份；沒有唯一最高者就不補。
- 沒有任何模組包有這個 key：不匯入（否則中文會被存成原文）。

找不到重疊度達門檻的模組包時不動，維持以新的模組包身分新增。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from translation_tool.core.lang_merge_dict import contains_cjk
from translation_tool.translation_db.content_identity import loose_text
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.schema import KIND_FTB_QUESTS, KIND_KUBEJS_SCRIPT

if TYPE_CHECKING:
    from translation_tool.translation_db.repository import TranslationDB

# 掃描到的來源只有譯文（沒有 en_us）時的來源語系
TRANSLATED_LOCALES = frozenset({"zh_tw", "zh_cn"})
MIN_OVERLAP_RATIO = 0.5
# 文字直接寫在 SNBT／JS 裡、沒有語系檔的類型：來源若已就地翻成中文，原文欄會是中文
IN_PLACE_KINDS = frozenset({KIND_FTB_QUESTS, KIND_KUBEJS_SCRIPT})

# (kind, mod_id 的命名空間, key)：不含模組包身分的條目識別
_Ident = tuple[str, str, str]


@dataclass(frozen=True)
class ScopeMatch:
    scopes: tuple[str, ...]  # 重疊度達門檻的既有模組包，由高到低
    best: str | None  # 重疊度唯一最高的模組包；最高分平手時為 ``None``
    # 各模組包中該條目的英文原文：``{識別: {模組包身分: 英文}}``
    originals: Mapping[_Ident, Mapping[str, str]]
    overlap: int  # 最高的重疊筆數
    total: int


@dataclass
class AdoptedItems:
    """譯文輸入對到既有模組包後的結果。"""

    items: list[ScanItem] = field(default_factory=list)
    # 沒有任何模組包有這個 key：沒有英文原文可對照，未匯入
    without_original: list[ScanItem] = field(default_factory=list)
    # 各模組包的英文不同：只補進重疊度唯一最高的模組包，沒有唯一最高者時未匯入
    original_differs: list[ScanItem] = field(default_factory=list)
    # 帶英文的項目只歸在重疊度唯一最高的模組包；沒有唯一最高者時未匯入
    no_single_pack: list[ScanItem] = field(default_factory=list)


def _ident(item: ScanItem) -> _Ident:
    return item.kind, item.mod_id.partition("@")[0], item.key


def _is_in_place_translation(item: ScanItem) -> bool:
    return (
        item.kind in IN_PLACE_KINDS
        and not item.source_locale
        and contains_cjk(item.en_us)
    )


def _is_locale_only(item: ScanItem) -> bool:
    return item.source_locale in TRANSLATED_LOCALES or _is_in_place_translation(item)


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
    """找出 key 重疊度達門檻的既有模組包；一個都沒有就回 ``None``。"""
    wanted: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in locale_only_items(items):
        kind, prefix, key = _ident(item)
        wanted[(kind, prefix)].add(key)
    total = sum(len(keys) for keys in wanted.values())
    overlap: dict[str, int] = defaultdict(int)
    found: dict[_Ident, dict[str, str]] = defaultdict(dict)
    for (kind, prefix), keys in wanted.items():
        for scope, originals in db.pack_entry_keys(version, kind, prefix).items():
            for key in keys & originals.keys():
                overlap[scope] += 1
                found[(kind, prefix, key)][scope] = originals[key]
    ranked = sorted(overlap.items(), key=lambda pair: (-pair[1], pair[0]))
    scopes = tuple(scope for scope, hits in ranked if hits / total >= min_ratio)
    if not scopes:
        return None
    top = ranked[0][1]
    best = scopes[0] if len(ranked) == 1 or ranked[1][1] < top else None
    kept = set(scopes)
    originals_by_ident: dict[_Ident, dict[str, str]] = {}
    for ident, by_scope in found.items():
        in_matched = {scope: en for scope, en in by_scope.items() if scope in kept}
        if in_matched:
            originals_by_ident[ident] = in_matched
    return ScopeMatch(scopes, best, originals_by_ident, top, total)


def carries_translation(items: list[ScanItem], match: ScopeMatch) -> bool:
    """對到的既有條目中，這份輸入是否帶了中文譯文。

    既有條目的原文都不是中文（見 ``TranslationDB.pack_entry_keys``），所以輸入是中文就
    一定是譯文而不是同一份原文。
    """
    return any(
        _ident(item) in match.originals
        and contains_cjk(item.zh_tw or item.zh_cn or item.en_us)
        for item in items
    )


def _rescope(item: ScanItem, scope: str) -> ScanItem:
    """改歸到既有模組包，且不記來源（路徑、selector）。

    這份輸入不是原模組包的檔案，記進去會讓原模組包多出不存在的來源，或用譯文檔的雜湊
    蓋掉原本英文來源的雜湊，破壞之後的回寫比對。
    """
    return replace(
        item,
        mod_id=f"{item.mod_id.partition('@')[0]}@{scope}",
        pack_scope=scope,
        source_path="",
        source_selector="",
    )


def _as_translation(item: ScanItem) -> ScanItem:
    """中文當譯文、原文留空：ingest 會沿用資料庫裡的英文，只補譯文。"""
    if item.source_locale == "zh_cn":
        return replace(item, en_us="", zh_cn=item.zh_cn or item.en_us)
    return replace(item, en_us="", zh_tw=item.zh_tw or item.en_us)


def _fill_targets(holders: Mapping[str, str], best: str | None) -> tuple[str, ...]:
    """這筆譯文要補進哪些模組包：英文都相同才全部補，否則只補唯一最高的那一份。"""
    if len({loose_text(en) for en in holders.values()}) == 1:
        return tuple(holders)
    return (best,) if best is not None and best in holders else ()


def keep_existing_originals(items: list[ScanItem], match: ScopeMatch) -> AdoptedItems:
    """把譯文輸入改寫成「只補譯文、保留既有英文」的匯入項目，並列出沒匯入的項目。"""
    result = AdoptedItems()
    for item in items:
        if not _is_locale_only(item):
            # 混合輸入裡帶英文的項目：只歸在唯一最高的模組包，不猜它屬於哪一份
            if match.best is None:
                result.no_single_pack.append(item)
            else:
                result.items.append(_rescope(item, match.best))
            continue
        holders = match.originals.get(_ident(item), {})
        if not holders:
            result.without_original.append(item)
            continue
        targets = _fill_targets(holders, match.best)
        if len(targets) < len(holders):
            result.original_differs.append(item)
        translation = _as_translation(item)
        result.items.extend(_rescope(translation, scope) for scope in targets)
    return result
