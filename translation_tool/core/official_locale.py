"""Minecraft 官方語系索引與 LLM 術語提示建構。

官方語系是帶 key 語意的資料，不能攤平成全域 ``from -> to`` replace rule。
本模組集中處理檔案載入、完整三欄命中與批次相關術語，讓新增翻譯種類時不必
再複製一套簡繁對照邏輯。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from translation_tool.core.lm_format_policy import (
    format_policy_for_items,
    normalize_legacy_prompt,
)
from translation_tool.utils.app_paths import get_resource_root
from translation_tool.utils.text_matching import (
    contains_term,
    matching_terms,
    term_canonical_key,
)

logger = logging.getLogger(__name__)

_OFFICIAL_RELATIVE_ROOT = Path("assets") / "official_locales" / "minecraft"
_LOCALE_FILES = ("en_us.json", "zh_cn.json", "zh_tw.json")
_MAX_PROMPT_TERMS = 240
_MAX_PROMPT_CHARS = 24_000
_MAX_TERMS_PER_ITEM = 6
_MINECRAFT_FORMAT_CODE_RE = re.compile(r"§[0-9a-fk-or]", re.IGNORECASE)
_NAME_LIKE_KEY_PREFIXES = frozenset(
    {
        "attribute",
        "biome",
        "block",
        "effect",
        "enchantment",
        "entity",
        "fluid",
        "item",
        "instrument",
        "dimension",
        "banner",
        "painting",
        "particle",
        "potion",
        "trim_material",
        "trim_pattern",
    }
)

_TERMINOLOGY_INSTRUCTION = (
    "\n【翻譯原則：術語優先、不要無故保留英文】\n"
    "以下逐筆術語映射是已審核或官方的唯一參考；當同一 payload id 的原文包含來源詞時，"
    "必須使用對應目標詞，不要把參考整句複製到其他項目。\n"
    "沒有可靠術語映射時，仍要盡力翻譯人類語句；只有 placeholder、格式標記、API 識別字、"
    "程式碼、產品名或其他明確不可翻譯內容才保留原文。不要因為不確定就把整個 value 原樣回傳。"
)


@dataclass(frozen=True)
class OfficialLocaleRecord:
    key: str
    en_us: str
    zh_cn: str
    zh_tw: str


@dataclass(frozen=True)
class OfficialLocaleIndex:
    """Immutable in-memory view of the bundled official locale files."""

    records_by_key: dict[str, OfficialLocaleRecord]
    unique_by_text_pair: dict[tuple[str, str], str]
    unique_tw_by_en: dict[str, str]
    revision: str
    available: bool = True
    name_like_terms: frozenset[str] = frozenset()

    def lookup_exact(self, key: object, en_us: object, zh_cn: object) -> str | None:
        """Return official zh_tw only for an exact ``key + en + cn`` match."""
        if not all(isinstance(value, str) for value in (key, en_us, zh_cn)):
            return None
        record = self.records_by_key.get(key)
        if record is None or (record.en_us, record.zh_cn) != (en_us, zh_cn):
            return None
        return record.zh_tw or None

    def lookup_unique_text(self, en_us: object, zh_cn: object) -> str | None:
        """Return a text-only mapping only when the official pair is unambiguous."""
        if not isinstance(en_us, str) or not isinstance(zh_cn, str):
            return None
        return self.unique_by_text_pair.get((en_us, zh_cn))

    def glossary_for_items(
        self, items: list[dict[str, Any]]
    ) -> list[tuple[str, str, str]]:
        """Collect only official terms relevant to this batch.

        Key matches win. A text-only fallback is accepted only for a unique official
        English value, so one-to-many Chinese sources cannot silently overwrite one
        another.
        """
        terms: dict[tuple[str, str], str] = {}
        for item in items:
            key = item.get("path") or item.get("key")
            en_us = item.get("source_text") or item.get("text")
            record = self.records_by_key.get(key) if isinstance(key, str) else None
            target = None
            normalized_en_us = (
                normalize_terminology_text(en_us) if isinstance(en_us, str) else None
            )
            if (
                record is not None
                and normalized_en_us is not None
                and normalize_terminology_text(record.en_us) == normalized_en_us
            ):
                target = record.zh_tw or None
            if target is None and isinstance(en_us, str):
                target = self.unique_tw_by_en.get(en_us)
            if (
                isinstance(normalized_en_us, str)
                and target
                and normalized_en_us != target
            ):
                # The official table can map the same English source to
                # different Traditional Chinese values by key. Keep that key
                # in the deduplication identity and in the prompt.
                terms[(str(key) if isinstance(key, str) else "", normalized_en_us)] = (
                    target
                )

        ordered = sorted(
            ((key, source, target) for (key, source), target in terms.items()),
            key=lambda entry: (-len(entry[1]), entry[0], entry[1]),
        )
        return ordered[:_MAX_PROMPT_TERMS]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _empty_index() -> OfficialLocaleIndex:
    return OfficialLocaleIndex({}, {}, {}, "missing", available=False)


def _strip_minecraft_format_codes(value: str) -> str:
    return _MINECRAFT_FORMAT_CODE_RE.sub("", value)


def normalize_terminology_text(value: str) -> str:
    """Normalize source/term text before applying the shared terminology plan."""
    return _strip_minecraft_format_codes(value)


def _is_hyphenated_occurrence(source: str, term: str) -> bool:
    normalized_source = _strip_minecraft_format_codes(source).casefold()
    normalized_term = term.strip().casefold()
    start = 0
    while (
        normalized_term
        and (index := normalized_source.find(normalized_term, start)) >= 0
    ):
        before = normalized_source[index - 1] if index else ""
        after_index = index + len(normalized_term)
        after = (
            normalized_source[after_index]
            if after_index < len(normalized_source)
            else ""
        )
        if before == "-" or after == "-":
            return True
        start = index + 1
    return False


def _terminology_term_allowed(
    source: str,
    term: str,
    index: OfficialLocaleIndex,
    *,
    key: str | None = None,
) -> bool:
    """Avoid forcing generic UI words into sentence or hyphenated contexts."""
    normalized_source = normalize_terminology_text(source)
    normalized_term = normalize_terminology_text(term).strip()
    if term_canonical_key(normalized_source) == term_canonical_key(normalized_term):
        return True
    if " " in normalized_term:
        return not _is_hyphenated_occurrence(source, normalized_term)
    if _is_hyphenated_occurrence(source, normalized_term):
        return False
    key_prefix = key.split(".", 1)[0] if isinstance(key, str) else ""
    if key_prefix in _NAME_LIKE_KEY_PREFIXES:
        return True
    return term_canonical_key(normalized_term) in index.name_like_terms


def terminology_term_allowed(
    source: str,
    term: str,
    *,
    key: str | None = None,
    index: OfficialLocaleIndex | None = None,
) -> bool:
    """Expose the production context filter to review/evidence consumers."""
    return _terminology_term_allowed(
        source,
        term,
        index or load_official_locale_index(),
        key=key,
    )


def _load_index(root: Path) -> OfficialLocaleIndex:
    locale_root = root / _OFFICIAL_RELATIVE_ROOT
    try:
        manifest = json.loads(
            (locale_root / "manifest.json").read_text(encoding="utf-8")
        )
        data = {
            Path(name).stem: json.loads(
                (locale_root / name).read_text(encoding="utf-8")
            )
            for name in _LOCALE_FILES
        }
        manifest_files = manifest.get("files", {})
        for name in _LOCALE_FILES:
            expected = manifest_files.get(name, {}).get("sha256")
            if (
                not isinstance(expected, str)
                or _file_sha256(locale_root / name) != expected
            ):
                raise ValueError(f"官方語系完整性驗證失敗：{name}")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("找不到或無法讀取 Minecraft 官方語系：%s", exc)
        return _empty_index()
    if not all(isinstance(values, dict) for values in data.values()):
        logger.warning("Minecraft 官方語系檔案必須都是 JSON object")
        return _empty_index()

    records: dict[str, OfficialLocaleRecord] = {}
    text_pairs: dict[tuple[str, str], set[str]] = {}
    en_values: dict[str, set[str]] = {}
    for key in set(data["en_us"]) & set(data["zh_cn"]) & set(data["zh_tw"]):
        en_us, zh_cn, zh_tw = (
            data["en_us"].get(key),
            data["zh_cn"].get(key),
            data["zh_tw"].get(key),
        )
        if not all(isinstance(value, str) for value in (en_us, zh_cn, zh_tw)):
            continue
        records[key] = OfficialLocaleRecord(key, en_us, zh_cn, zh_tw)
        text_pairs.setdefault((en_us, zh_cn), set()).add(zh_tw)
        en_values.setdefault(en_us, set()).add(zh_tw)

    unique_pairs = {
        pair: next(iter(targets))
        for pair, targets in text_pairs.items()
        if len(targets) == 1
    }
    unique_en = {
        source: next(iter(targets))
        for source, targets in en_values.items()
        if len(targets) == 1
    }
    name_like_terms = frozenset(
        term_canonical_key(record.en_us)
        for record in records.values()
        if record.en_us in unique_en
        and record.key.split(".", 1)[0] in _NAME_LIKE_KEY_PREFIXES
    )
    revision = ":".join(_file_sha256(locale_root / name)[:16] for name in _LOCALE_FILES)
    return OfficialLocaleIndex(
        records, unique_pairs, unique_en, revision, name_like_terms=name_like_terms
    )


@lru_cache(maxsize=4)
def load_official_locale_index(root: str | None = None) -> OfficialLocaleIndex:
    """Load the bundled index once per resource root."""
    return _load_index(Path(root) if root else get_resource_root())


def official_translation_for_item(item: dict[str, Any]) -> str | None:
    """Resolve an item with optional ``zh_cn`` data before an LLM request."""
    zh_cn = item.get("zh_cn") or item.get("official_zh_cn")
    if zh_cn is None:
        return None
    return load_official_locale_index().lookup_exact(
        item.get("path") or item.get("key"),
        item.get("source_text") or item.get("text"),
        zh_cn,
    )


def official_exact_translation(key: object, en_us: object, zh_cn: object) -> str | None:
    """Shared key-aware lookup used by every merge/translation kind."""
    return load_official_locale_index().lookup_exact(key, en_us, zh_cn)


def build_translation_prompt(
    base_prompt: str,
    items: list[dict[str, Any]],
    *,
    profile: str,
    include_official: bool = True,
    include_moddb_reviewed: bool = True,
) -> str:
    """Append Chinese, batch-scoped glossary instructions to a base prompt."""
    sections = [
        normalize_legacy_prompt(base_prompt).rstrip(),
        format_policy_for_items(items, profile=profile),
        _TERMINOLOGY_INSTRUCTION,
    ]
    index = load_official_locale_index()
    directives = _terminology_directives(
        items,
        index,
        include_official=include_official,
        include_moddb_reviewed=include_moddb_reviewed,
    )
    if directives:
        sections.append("\n".join(directives))
    official_terms = (
        index.glossary_for_items(items) if include_official and index.available else []
    )
    if official_terms:
        lines = ["\n【官方 Minecraft 術語（本批相關）】"]
        lines.extend(
            (
                f"- key「{key}」：{source} → {target}"
                if key
                else f"- {source} → {target}"
            )
            for key, source, target in official_terms
        )
        sections.append("\n".join(lines))

    # Reviewed Mod DB lang entries are terminology references for every
    # Patchouli-compatible route as well.  They are never written directly;
    # the model still translates the current source item.
    if profile in {"lang", "patch", "md"} and include_moddb_reviewed:
        reviewed = _reviewed_glossary_for_items(items, index)
        if reviewed:
            lines = ["\n【Mod DB 已審核 lang 翻譯參考（目前版本、本批相關）】"]
            lines.extend(f"- {source} → {target}" for source, target in reviewed)
            sections.append("\n".join(lines))

    prompt = "\n\n".join(sections)
    return prompt[: len(base_prompt) + _MAX_PROMPT_CHARS]


def _reviewed_glossary_for_items(
    items: list[dict[str, Any]],
    index: OfficialLocaleIndex,
) -> list[tuple[str, str]]:
    """Keep only unambiguous reviewed terms that occur in the current batch."""
    by_source: dict[str, tuple[str, set[str]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        # Reuse the same per-item filtering and longest-term selection as the
        # payload-bound directives.  A batch-wide raw substring scan could
        # reintroduce generic words or nested, conflicting terms.
        selected = _item_terminology_terms(
            item,
            index,
            include_official=False,
            include_moddb_reviewed=True,
        )
        for source, target in selected:
            normalized_source = normalize_terminology_text(source).strip()
            canonical = term_canonical_key(normalized_source)
            display_source, targets = by_source.setdefault(
                canonical, (normalized_source, set())
            )
            targets.add(target.strip())
            by_source[canonical] = (display_source, targets)
    return [
        (display_source, next(iter(targets)))
        for _canonical, (display_source, targets) in sorted(
            by_source.items(), key=lambda entry: (-len(entry[1][0]), entry[0])
        )
        if len(targets) == 1
    ][:_MAX_PROMPT_TERMS]


def _item_terminology_terms(
    item: dict[str, Any],
    index: OfficialLocaleIndex,
    *,
    include_official: bool = True,
    include_moddb_reviewed: bool = True,
) -> list[tuple[str, str]]:
    """Return unique official/reviewed mappings that occur in one source item."""
    source_text = item.get("source_text") or item.get("text")
    if not isinstance(source_text, str) or not source_text.strip():
        return []
    normalized_source = normalize_terminology_text(source_text)
    item_key = item.get("path") or item.get("key")

    candidates: dict[str, tuple[str, set[str]]] = {}

    if include_official and index.available:
        key = item.get("path") or item.get("key")
        record = index.records_by_key.get(key) if isinstance(key, str) else None
        if (
            record is not None
            and normalize_terminology_text(record.en_us) == normalized_source
            and record.zh_tw
        ):
            candidates[term_canonical_key(normalized_source)] = (
                normalized_source,
                {record.zh_tw.strip()},
            )

        # The source item is often a compound name (for example, ``Ruby Heavy
        # Crossbow``).  Match the unique official sub-terms as well; exact
        # whole-value lookup alone cannot enforce Minecraft's established
        # terminology inside a newly introduced key.
        official_terms = matching_terms(
            (normalized_source,), tuple(index.unique_tw_by_en)
        )
        for term in sorted(official_terms, key=lambda value: (-len(value), value)):
            if not _terminology_term_allowed(
                source_text,
                term,
                index,
                key=item_key if isinstance(item_key, str) else None,
            ):
                continue
            target = index.unique_tw_by_en.get(term)
            if not isinstance(target, str) or not target.strip() or target == term:
                continue
            canonical = term_canonical_key(term)
            display, targets = candidates.setdefault(canonical, (term, set()))
            targets.add(target.strip())
            candidates[canonical] = (display, targets)

    reviewed = item.get("_reviewed_glossary")
    if include_moddb_reviewed and isinstance(reviewed, list):
        for entry in reviewed:
            if not isinstance(entry, dict):
                continue
            term, target = entry.get("en_us"), entry.get("zh_tw")
            if not (
                isinstance(term, str)
                and isinstance(target, str)
                and term.strip()
                and target.strip()
                and contains_term(normalized_source, normalize_terminology_text(term))
                and _terminology_term_allowed(
                    source_text,
                    normalize_terminology_text(term),
                    index,
                    key=entry.get("key") if isinstance(entry.get("key"), str) else None,
                )
            ):
                continue
            normalized_term = normalize_terminology_text(term).strip()
            canonical = term_canonical_key(normalized_term)
            display, targets = candidates.setdefault(
                canonical, (normalized_term, set())
            )
            targets.add(target.strip())
            candidates[canonical] = (display, targets)

    ordered = [
        (display, next(iter(targets)))
        for _canonical, (display, targets) in sorted(
            candidates.items(), key=lambda entry: (-len(entry[1][0]), entry[0])
        )
        if len(targets) == 1
    ]
    selected: list[tuple[str, str]] = []
    for term, target in ordered:
        # Prefer the longest authoritative phrase.  Sending both ``Brick Slab
        # -> 紅磚半磚`` and ``Brick -> 紅磚頭`` creates contradictory
        # instructions for the same source span and makes the model regress.
        if any(contains_term(selected_term, term) for selected_term, _ in selected):
            continue
        selected.append((term, target))
        if len(selected) >= _MAX_TERMS_PER_ITEM:
            break
    return selected


def _terminology_directives(
    items: list[dict[str, Any]],
    index: OfficialLocaleIndex,
    *,
    include_official: bool = True,
    include_moddb_reviewed: bool = True,
) -> list[str]:
    """Render mappings next to the payload id the model must translate."""
    lines = [
        "\n【逐筆必用術語（只適用於同一 payload id）】",
        "每一行的來源詞若出現在該 id 的 value，譯文必須採用同一行的目標詞。",
    ]
    count = 0
    for item_id, item in enumerate(items):
        terms = _item_terminology_terms(
            item,
            index,
            include_official=include_official,
            include_moddb_reviewed=include_moddb_reviewed,
        )
        key = item.get("path") or item.get("key") or ""
        for source, target in terms:
            lines.append(f"- id「{item_id}」 key「{key}」：{source} → {target}")
            count += 1
            if count >= _MAX_PROMPT_TERMS:
                return lines
    return lines if count else []


def terminology_terms_for_item(
    item: dict[str, Any],
    *,
    include_official: bool = True,
    include_moddb_reviewed: bool = True,
    index: OfficialLocaleIndex | None = None,
) -> list[tuple[str, str]]:
    """Resolve the cached-input terminology plan for one translation item."""
    return _item_terminology_terms(
        item,
        index or load_official_locale_index(),
        include_official=include_official,
        include_moddb_reviewed=include_moddb_reviewed,
    )


def terminology_issues(
    item: dict[str, Any],
    translated_text: str,
    *,
    include_official: bool = True,
    include_moddb_reviewed: bool = True,
    terms: Sequence[tuple[str, str]] | None = None,
) -> list[str]:
    """Report authoritative source terms missing from one model translation."""
    expected_terms = terms
    if expected_terms is None:
        expected_terms = terminology_terms_for_item(
            item,
            include_official=include_official,
            include_moddb_reviewed=include_moddb_reviewed,
        )
    if not isinstance(translated_text, str) or not translated_text.strip():
        return [f"術語未校對：{source} → {target}" for source, target in expected_terms]
    return [
        f"術語未命中：{source} → {target}"
        for source, target in expected_terms
        if target not in translated_text
    ]


__all__ = [
    "OfficialLocaleIndex",
    "OfficialLocaleRecord",
    "build_translation_prompt",
    "load_official_locale_index",
    "normalize_terminology_text",
    "official_exact_translation",
    "official_translation_for_item",
    "terminology_issues",
    "terminology_term_allowed",
    "terminology_terms_for_item",
]
