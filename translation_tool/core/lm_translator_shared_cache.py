from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from translation_tool.core.lm_config_rules import value_fully_translated
from translation_tool.utils.cache_manager import get_cache_dict_ref


@dataclass(frozen=True)
class CacheRule:
    """定義快取鍵值的生成規則。"""

    key_mode: str = "path|source_text"

    def make_key(self, item: Dict[str, Any]) -> str:
        """依規則建立 cache key。"""
        path = str(item.get("path") or "")
        src = str(item.get("source_text") or "")
        if self.key_mode == "path":
            return path
        return f"{path}|{src}"


STRICT_SRC_TYPES = {
    "lang",
    "kubejs",
    "ftbquests",
    "md",
}


ValidHitFn = Callable[[str, Dict[str, Any], Dict[str, Any]], bool]


def get_default_cache_rules() -> Dict[str, CacheRule]:
    """回傳預設 cache rule map。"""
    return {
        "lang": CacheRule("path"),
        "patchouli": CacheRule("path|source_text"),
        "ftbquests": CacheRule("path|source_text"),
        "kubejs": CacheRule("path|source_text"),
        "md": CacheRule("path|source_text"),
    }


def _is_valid_hit(dst: str, entry: dict, item: dict) -> bool:
    """判斷 cache 命中是否可信。"""
    if not value_fully_translated(dst):
        return False

    ctype = item.get("cache_type") or "lang"
    if ctype in STRICT_SRC_TYPES:
        item_src = (
            item.get("source_text") or item.get("source") or item.get("src_text") or ""
        )
        entry_src = entry.get("src") or ""

        if not item_src:
            return False

        return entry_src == item_src

    return True


def fast_split_items_by_cache(
    all_items: Iterable[Dict[str, Any]],
    *,
    cache_rules: Optional[Dict[str, CacheRule]] = None,
    is_valid_hit: Optional[ValidHitFn] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """直接用 cache dict 分流 items。"""
    if cache_rules is None:
        cache_rules = get_default_cache_rules()

    cached_items: List[Dict[str, Any]] = []
    items_to_translate: List[Dict[str, Any]] = []
    checker = is_valid_hit or _is_valid_hit
    cache_refs: Dict[str, Dict[str, Any]] = {}

    for it in all_items:
        if not isinstance(it, dict):
            continue

        ctype = str(it.get("cache_type") or "lang")
        rule = cache_rules.get(ctype) or CacheRule("path|source_text")
        key = rule.make_key(it)

        if ctype not in cache_refs:
            cache_refs[ctype] = get_cache_dict_ref(ctype)

        entry = cache_refs[ctype].get(key)
        if isinstance(entry, dict):
            dst = entry.get("dst")
            if isinstance(dst, str) and checker(dst, entry, it):
                new_it = dict(it)
                new_it["text"] = dst
                cached_items.append(new_it)
                continue

        items_to_translate.append(dict(it))

    return cached_items, items_to_translate
