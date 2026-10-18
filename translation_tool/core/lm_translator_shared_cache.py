"""translation_tool/core/lm_translator_shared_cache.py 模組。

用途：翻譯共用快取管理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from translation_tool.core.lm_config_rules import value_fully_translated
from translation_tool.core.lm_format_policy import kind_for_item
from translation_tool.plugins.shared.rich_text_shield import (
    inspect_shielded_translation,
)
from translation_tool.translation_db.quality import translation_issues
from translation_tool.utils.cache_manager import get_cache_dict_ref


@dataclass(frozen=True)
class CacheRule:
    """定義快取鍵值的生成規則。"""

    key_mode: str = "path|source_text"

    def make_key(self, item: dict[str, Any]) -> str:
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

ValidHitFn = Callable[[str, dict[str, Any], dict[str, Any]], bool]


def cache_quality_issues(item: dict[str, Any], translated: str) -> list[str]:
    """Return the shared quality gate used by AI, cache, and DB translation hits."""
    kind = kind_for_item(item)
    candidate = str(translated or "")
    shielded = item.get("_shielded")
    if shielded is not None and getattr(shielded, "shields", None):
        checked = inspect_shielded_translation(candidate, shielded)
        candidate = checked.restored_text
        shield_issues = list(checked.issues)
    else:
        shield_issues = []
    return shield_issues + translation_issues(
        str(item.get("source_text") or ""), candidate, kind=kind
    )


def restore_cache_hit_text(item: dict[str, Any]) -> str:
    """將快取命中轉為可寫入正式輸出的文字，兼容舊 placeholder 快取。"""
    text = item.get("text")
    if not isinstance(text, str):
        return ""
    shielded = item.get("_shielded")
    if shielded is None or not getattr(shielded, "shields", None):
        return text
    return inspect_shielded_translation(text, shielded).restored_text


def cache_storage_text(item: dict[str, Any]) -> str:
    """回傳與暫時 placeholder 解耦、可跨執行重用的快取文字。"""
    return restore_cache_hit_text(item)


def safe_output_text(item: dict[str, Any]) -> str:
    """Return text allowed to enter a formal output file or database buffer.

    A rejected AI candidate remains on the item for Mod DB manual review, but
    must never be treated as a valid translation by a formal-output callback.
    Such callbacks fall back to the original source text instead.
    """
    if item.get("_cache_rejected"):
        source = item.get("source_text")
        return source if isinstance(source, str) else ""
    text = item.get("text")
    return text if isinstance(text, str) else ""


def get_default_cache_rules() -> dict[str, CacheRule]:
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
    if cache_quality_issues(item, dst):
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
    all_items: Iterable[dict[str, Any]],
    *,
    cache_rules: dict[str, CacheRule] | None = None,
    is_valid_hit: ValidHitFn | None = None,
    cache_provider: Callable[[str], dict[str, Any]] | None = None,
    cache_fallbacks: dict[str, tuple[str, ...]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """依據 shared 規則分為 cache hit/miss；provider 只注入既有 cache ownership。"""
    if cache_rules is None:
        cache_rules = get_default_cache_rules()

    cached_items: list[dict[str, Any]] = []
    items_to_translate: list[dict[str, Any]] = []
    checker = is_valid_hit or _is_valid_hit
    provider = cache_provider or get_cache_dict_ref
    cache_refs: dict[str, dict[str, Any]] = {}
    fallbacks = cache_fallbacks or {}

    for it in all_items:
        if not isinstance(it, dict):
            continue

        ctype = str(it.get("cache_type") or "lang")
        rule = cache_rules.get(ctype) or CacheRule("path|source_text")
        key = rule.make_key(it)

        lookup_types = (ctype, *fallbacks.get(ctype, ()))
        for lookup_type in lookup_types:
            if lookup_type not in cache_refs:
                cache_refs[lookup_type] = provider(lookup_type)
            entry = cache_refs[lookup_type].get(key)
            if isinstance(entry, dict):
                dst = entry.get("dst")
                if isinstance(dst, str) and checker(dst, entry, it):
                    new_it = dict(it)
                    # 快取層直接交付正式文字；各插件不必再猜測這筆資料是
                    # 新的還原格式或舊的暫時 placeholder 格式。
                    new_it["text"] = restore_cache_hit_text({**it, "text": dst})
                    cached_items.append(new_it)
                    break
        else:
            items_to_translate.append(dict(it))
            continue

    return cached_items, items_to_translate
