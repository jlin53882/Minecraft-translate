"""translation_tool/core/lm_translator_shared_cache.py 模組。

用途：翻譯共用快取管理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from translation_tool.core.lm_config_rules import value_fully_translated
from translation_tool.core.lm_format_policy import kind_for_item
from translation_tool.core.source_locale import normalize_source_locale
from translation_tool.plugins.shared.rich_text_shield import (
    inspect_shielded_translation,
)
from translation_tool.translation_db.quality import translation_issues
from translation_tool.utils.cache_manager import get_cache_dict_ref


def source_locale_key_suffix(item: Mapping[str, Any]) -> str:
    """建立來源語系的快取後綴；未提供欄位時保留 legacy key。"""
    if "source_locale" not in item:
        return ""
    locale = normalize_source_locale(item.get("source_locale"))
    if locale == "en_us":
        return ""
    if locale is not None:
        return f"@{locale}"
    entry_id = str(item.get("_entry_id") or "").strip()
    return f"@unknown:{entry_id}" if entry_id else "@unknown"


def _scoped_cache_key(
    *,
    key_mode: str,
    path: str,
    source_text: str,
    module_id: str = "",
    pack_scope: str,
    locale: str,
) -> str:
    """建立無歧義、可拆解的 scoped cache key。

    直接把模組或 scope 拼到 path 尾端會讓人為構造的路徑互相碰撞（例如路徑
    本身已包含 ``@pack:``）。有來源身分的新項目使用長度前綴欄位；沒有
    模組與 scope 的舊項目仍由 ``CacheRule.make_key`` 保留原本 key，避免
    整個既有快取失效。欄位格式升版為 v3，讓舊 scoped key 不會誤命中新語意。
    """
    fields = (
        "v3",
        key_mode,
        path,
        source_text if key_mode != "path" else "",
        module_id,
        pack_scope,
        locale,
    )
    return "|".join(f"{len(value)}:{value}" for value in fields)


@dataclass(frozen=True)
class CacheRule:
    """定義快取鍵值的生成規則。"""

    key_mode: str = "path|source_text"

    def make_key(self, item: dict[str, Any]) -> str:
        """依規則建立 cache key。

        模組包身分、pack scope 與來源語系都會加入結構化鍵，讓相同路徑／原文
        的不同來源不共用快取；沒有來源包身分的舊項目仍維持原本格式，保留既有
        快取相容性。
        """
        path = str(item.get("path") or "")
        src = str(item.get("source_text") or "")
        module_id = str(item.get("_mod_id") or item.get("mod_id") or "").strip()
        scope = str(item.get("pack_scope") or "").strip()
        has_locale = "source_locale" in item
        locale = normalize_source_locale(item.get("source_locale"))
        entry_id = str(item.get("_entry_id") or "").strip()
        locale_identity = locale or (
            f"unknown:{entry_id}"
            if has_locale and entry_id
            else "unknown"
            if has_locale
            else ""
        )
        if module_id or scope:
            return _scoped_cache_key(
                key_mode=self.key_mode,
                path=path,
                source_text=src,
                module_id=module_id,
                pack_scope=scope,
                locale=locale_identity,
            )
        suffix = source_locale_key_suffix(item)
        if self.key_mode == "path":
            return f"{path}{suffix}"
        return f"{path}|{src}{suffix}"


STRICT_SRC_TYPES = {
    "lang",
    "advancement",
    "kubejs",
    "ftbquests",
    "md",
}

ValidHitFn = Callable[[str, dict[str, Any], dict[str, Any]], bool]


def cache_quality_issues(
    item: dict[str, Any],
    translated: str,
    *,
    allow_legacy_placeholders: bool = False,
) -> list[str]:
    """Return the shared quality gate used by AI, cache, and DB translation hits."""
    kind = kind_for_item(item)
    candidate = str(translated or "")
    shielded = item.get("_shielded")
    if shielded is not None and getattr(shielded, "shields", None):
        checked = inspect_shielded_translation(
            candidate,
            shielded,
            allow_legacy_placeholders=allow_legacy_placeholders,
        )
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
    return inspect_shielded_translation(
        text, shielded, allow_legacy_placeholders=True
    ).restored_text


def cache_storage_text(item: dict[str, Any]) -> str:
    """回傳與暫時 placeholder 解耦、可跨執行重用的快取文字。"""
    text = item.get("text")
    if not isinstance(text, str):
        return ""
    shielded = item.get("_shielded")
    if shielded is None or not getattr(shielded, "shields", None):
        return text
    return inspect_shielded_translation(text, shielded).restored_text


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
        "advancement": CacheRule("path|source_text"),
        "ftbquests": CacheRule("path|source_text"),
        "kubejs": CacheRule("path|source_text"),
        "md": CacheRule("path|source_text"),
    }


def _is_valid_hit(dst: str, entry: dict, item: dict) -> bool:
    """判斷 cache 命中是否可信。"""
    if (
        "source_locale" in item
        and normalize_source_locale(item.get("source_locale")) is None
    ):
        # unknown 不能和任何已知 locale 或另一筆 unknown 自動共用；沒有
        # 可重播的來源身份時，交給本次 provider 重新處理。
        return False
    if not value_fully_translated(dst):
        return False
    if cache_quality_issues(item, dst, allow_legacy_placeholders=True):
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
