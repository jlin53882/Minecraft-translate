"""Minecraft 官方語系索引與 LLM 術語提示建構。

官方語系是帶 key 語意的資料，不能攤平成全域 ``from -> to`` replace rule。
本模組集中處理檔案載入、完整三欄命中與批次相關術語，讓新增翻譯種類時不必
再複製一套簡繁對照邏輯。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from translation_tool.core.lm_format_policy import (
    format_policy_for_items,
    normalize_legacy_prompt,
)
from translation_tool.utils.app_paths import get_resource_root
from translation_tool.utils.text_matching import matching_terms, term_canonical_key

logger = logging.getLogger(__name__)

_OFFICIAL_RELATIVE_ROOT = Path("assets") / "official_locales" / "minecraft"
_LOCALE_FILES = ("en_us.json", "zh_cn.json", "zh_tw.json")
_MAX_PROMPT_TERMS = 240
_MAX_PROMPT_CHARS = 24_000


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
            if record is not None and record.en_us == en_us:
                target = record.zh_tw or None
            if target is None and isinstance(en_us, str):
                target = self.unique_tw_by_en.get(en_us)
            if isinstance(en_us, str) and target and en_us != target:
                # The official table can map the same English source to
                # different Traditional Chinese values by key. Keep that key
                # in the deduplication identity and in the prompt.
                terms[(str(key) if isinstance(key, str) else "", en_us)] = target

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
    revision = ":".join(_file_sha256(locale_root / name)[:16] for name in _LOCALE_FILES)
    return OfficialLocaleIndex(records, unique_pairs, unique_en, revision)


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
    ]
    index = load_official_locale_index()
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
        reviewed = _reviewed_glossary_for_items(items)
        if reviewed:
            lines = ["\n【Mod DB 已審核 lang 翻譯參考（目前版本、本批相關）】"]
            lines.extend(f"- {source} → {target}" for source, target in reviewed)
            sections.append("\n".join(lines))

    prompt = "\n\n".join(sections)
    return prompt[: len(base_prompt) + _MAX_PROMPT_CHARS]


def _reviewed_glossary_for_items(
    items: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Keep only unambiguous reviewed terms that occur in the current batch."""
    batch_sources = tuple(
        dict.fromkeys(
            source
            for item in items
            if isinstance(item, dict)
            for source in (item.get("source_text") or item.get("text"),)
            if isinstance(source, str) and source.strip()
        )
    )
    by_source: dict[str, tuple[str, set[str]]] = {}
    for item in items:
        reviewed = item.get("_reviewed_glossary")
        if not isinstance(reviewed, list):
            continue
        for entry in reviewed:
            if not isinstance(entry, dict):
                continue
            source, target = entry.get("en_us"), entry.get("zh_tw")
            if (
                isinstance(source, str)
                and isinstance(target, str)
                and source.strip()
                and target.strip()
            ):
                canonical = term_canonical_key(source)
                _display_source, targets = by_source.setdefault(
                    canonical, (source, set())
                )
                targets.add(target)

    matched_sources = matching_terms(
        batch_sources,
        tuple(display_source for display_source, _targets in by_source.values()),
    )
    matched_source_keys = {term_canonical_key(matched) for matched in matched_sources}
    return [
        (display_source, next(iter(targets)))
        for canonical, (display_source, targets) in sorted(by_source.items())
        if canonical in matched_source_keys and len(targets) == 1
    ][:_MAX_PROMPT_TERMS]


__all__ = [
    "OfficialLocaleIndex",
    "OfficialLocaleRecord",
    "build_translation_prompt",
    "load_official_locale_index",
    "official_exact_translation",
    "official_translation_for_item",
]
