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
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from translation_tool.core.lm_format_policy import (
    format_policy_for_items,
    normalize_legacy_prompt,
)
from translation_tool.core.terminology_model import (
    MATCH_STANDALONE,
    STANDALONE_ALLOWLIST,
    USAGE_STANDALONE,
    TerminologyMatch,
    from_official_match,
    prompt_glossary_key,
    prompt_term_pair,
)
from translation_tool.utils.app_paths import get_resource_root
from translation_tool.utils.cache_registry import register_cache
from translation_tool.utils.text_matching import (
    build_term_prefix_index,
    contains_term,
    matching_terms,
    normalize_term_text,
    term_canonical_key,
)

logger = logging.getLogger(__name__)

_OFFICIAL_RELATIVE_ROOT = Path("assets") / "official_locales" / "minecraft"
_LOCALE_FILES = ("en_us.json", "zh_cn.json", "zh_tw.json")
_MAX_PROMPT_TERMS = 240
_MAX_PROMPT_CHARS = 24_000
_MAX_TERMS_PER_ITEM = 6
_MINECRAFT_FORMAT_CODE_RE = re.compile(r"§[0-9a-fk-or]", re.IGNORECASE)
_TERM_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_DERIVED_TERM_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
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
class OfficialTerminologyMatch:
    """一筆可同時供翻譯提示詞與 Smart Review 使用的官方術語命中。"""

    source_text: str
    target_text: str
    key: str | None = None
    match_type: str = "source_substring"
    term_origin: str = "official_term"

    @property
    def stable_id(self) -> str:
        """以官方 key／來源／目標建立不依賴排序的 evidence id。"""
        identity = "\x00".join(
            (
                self.key or "",
                term_canonical_key(self.source_text),
                self.target_text.strip(),
            )
        )
        return (
            f"official-term:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
        )


@dataclass(frozen=True)
class OfficialLocaleIndex:
    """bundled 官方 Minecraft locale 的不可變記憶體索引，供機翻與智慧校對共用術語匹配結果。"""

    records_by_key: dict[str, OfficialLocaleRecord]
    unique_by_text_pair: dict[tuple[str, str], str]
    unique_tw_by_en: dict[str, str]
    revision: str
    available: bool = True
    name_like_terms: frozenset[str] = frozenset()
    unique_tw_by_normalized_en: dict[str, str] = field(default_factory=dict)
    unique_record_by_normalized_en: dict[str, OfficialLocaleRecord] = field(
        default_factory=dict
    )
    derived_terms_by_en: dict[str, tuple[str, str, str | None]] = field(
        default_factory=dict
    )
    terminology_terms: tuple[str, ...] = ()
    terminology_prefix_index: dict[str, tuple[tuple[str, str], ...]] = field(
        default_factory=dict
    )

    def lookup_exact(self, key: object, en_us: object, zh_cn: object) -> str | None:
        """只有在 key、英文與簡中完全匹配時回傳官方繁中。"""
        if not all(isinstance(value, str) for value in (key, en_us, zh_cn)):
            return None
        record = self.records_by_key.get(key)
        if record is None or (record.en_us, record.zh_cn) != (en_us, zh_cn):
            return None
        return record.zh_tw or None

    def lookup_unique_text(self, en_us: object, zh_cn: object) -> str | None:
        """只有官方對照無歧義時才回傳純文字映射。"""
        if not isinstance(en_us, str) or not isinstance(zh_cn, str):
            return None
        return self.unique_by_text_pair.get((en_us, zh_cn))

    def glossary_for_items(
        self, items: list[dict[str, Any]]
    ) -> list[tuple[str, str, str]]:
        """收集與目前批次相關的官方術語，供提示詞使用。"""
        terms: dict[tuple[str, str, str], tuple[str, str]] = {}
        for item in items:
            for official_match in self.official_terminology_matches_for_item(item):
                # Keep key-specific full translations distinguishable.  A
                # substring/compound term is deliberately shown without a
                # misleading full-entry key (see ``prompt_glossary_key``).
                match = from_official_match(official_match)
                source, target = prompt_term_pair(match)
                terms[
                    (
                        prompt_glossary_key(match),
                        term_canonical_key(source),
                        target,
                    )
                ] = (source, target)

        ordered = sorted(
            (
                (key, display_source, target)
                for (key, _canonical_source, _target), (
                    display_source,
                    target,
                ) in terms.items()
            ),
            key=lambda entry: (-len(entry[1]), entry[0], entry[1]),
        )
        return ordered[:_MAX_PROMPT_TERMS]

    def official_terminology_matches_for_item(
        self, item: dict[str, Any]
    ) -> list[OfficialTerminologyMatch]:
        """以固定優先順序解析一筆來源中的官方完整翻譯與術語。

        這是原本機翻提示詞與 Smart Review context 共用的唯一官方匹配入口。
        結果只包含目前來源真正命中的少量術語，不會把整份官方語系送給模型。
        """
        source_text = item.get("source_text") or item.get("text")
        if (
            not isinstance(source_text, str)
            or not source_text.strip()
            or not self.available
        ):
            return []

        normalized_source = normalize_terminology_text(source_text)
        source_key = item.get("path") or item.get("key")
        candidates: list[tuple[int, OfficialTerminologyMatch]] = []

        def add(
            source: str,
            target: str,
            *,
            key: str | None,
            match_type: str,
            term_origin: str,
        ) -> None:
            """加入通過詞界、目標非空與來源去重檢查的官方術語候選。"""
            source = normalize_terminology_text(source).strip()
            target = str(target).strip()
            if (
                not source
                or not target
                or term_canonical_key(source) == term_canonical_key(target)
            ):
                return
            if not _terminology_term_allowed(source_text, source, self, key=key):
                return
            priority = {
                "exact_key": 0,
                "exact_text": 1,
                "official_phrase": 2,
                MATCH_STANDALONE: 2,
                "source_substring": 3,
            }.get(match_type, 4)
            candidates.append(
                (
                    priority,
                    OfficialTerminologyMatch(
                        source,
                        target,
                        key=key,
                        match_type=match_type,
                        term_origin=term_origin,
                    ),
                )
            )

        record = (
            self.records_by_key.get(source_key) if isinstance(source_key, str) else None
        )
        if (
            record is not None
            and term_canonical_key(record.en_us)
            == term_canonical_key(normalized_source)
            and record.zh_tw.strip()
        ):
            add(
                record.en_us,
                record.zh_tw,
                key=record.key,
                match_type="exact_key",
                term_origin="official_full_translation",
            )

        full_target = self._unique_target_for_source(normalized_source)
        full_record = self._unique_record_for_source(normalized_source)
        if full_target:
            add(
                full_record.en_us if full_record is not None else normalized_source,
                full_target,
                key=full_record.key if full_record is not None else None,
                match_type="exact_text",
                term_origin="official_full_translation",
            )

        term_values = self.terminology_terms or tuple(
            dict.fromkeys(
                (
                    *self.unique_tw_by_en.keys(),
                    *(value[0] for value in self._derived_terms().values()),
                )
            )
        )
        prefix_index = self.terminology_prefix_index or build_term_prefix_index(
            term_values
        )
        for term in matching_terms(
            (normalized_source,), term_values, prefix_index=prefix_index
        ):
            canonical = term_canonical_key(term)
            if canonical == term_canonical_key(normalized_source):
                # 單獨成句預設不套用子字串／複合詞術語（``Max``、``Wood`` 等會誤導）；
                # 只有人工審核 allowlist 且仍與官方語系一致者放行。
                allowed = self._standalone_allowlist_definition(canonical)
                if allowed is not None:
                    display, target, term_key = allowed
                    add(
                        display,
                        target,
                        key=term_key,
                        match_type=MATCH_STANDALONE,
                        term_origin=USAGE_STANDALONE,
                    )
                continue
            definition = self._term_definition(term)
            if definition is None:
                continue
            display, target, term_key, origin = definition
            token_count = len(_TERM_TOKEN_RE.findall(display))
            add(
                display,
                target,
                key=term_key,
                match_type="official_phrase" if token_count > 1 else "source_substring",
                term_origin=("official_phrase" if token_count > 1 else origin),
            )

        # One canonical source must not produce two competing targets.  An
        # exact key is allowed to win because it is the highest-priority
        # evidence; otherwise an ambiguous global term is omitted.
        by_source: dict[str, list[tuple[int, OfficialTerminologyMatch]]] = {}
        for priority, match in candidates:
            by_source.setdefault(term_canonical_key(match.source_text), []).append(
                (priority, match)
            )
        filtered: list[tuple[int, OfficialTerminologyMatch]] = []
        for values in by_source.values():
            exact = [value for value in values if value[1].match_type == "exact_key"]
            if exact:
                filtered.append(min(exact, key=lambda value: value[0]))
                continue
            targets = {value[1].target_text for value in values}
            if len(targets) == 1:
                filtered.append(min(values, key=lambda value: value[0]))

        selected: list[tuple[int, OfficialTerminologyMatch]] = []
        for priority, match in sorted(
            filtered,
            key=lambda value: (
                value[0],
                -len(term_canonical_key(value[1].source_text)),
                term_canonical_key(value[1].source_text),
            ),
        ):
            if any(
                contains_term(selected_match.source_text, match.source_text)
                for _selected_priority, selected_match in selected
            ):
                continue
            selected.append((priority, match))
            if len(selected) >= _MAX_TERMS_PER_ITEM:
                break
        return [match for _priority, match in selected]

    def _unique_target_for_source(self, source: str) -> str | None:
        """依正規化英文來源尋找唯一且不歧義的官方繁中目標。"""
        canonical = term_canonical_key(source)
        target = self.unique_tw_by_normalized_en.get(canonical)
        if target:
            return target
        exact = self.unique_tw_by_en.get(source)
        if exact:
            return exact
        # 已載入索引已包含所有不歧義的正規化來源；只有手動建立的相容索引
        # 才需要使用下方的舊式備援掃描。
        if self.unique_tw_by_normalized_en:
            return None
        matches = {
            value
            for raw_source, value in self.unique_tw_by_en.items()
            if term_canonical_key(raw_source) == canonical
        }
        return next(iter(matches)) if len(matches) == 1 else None

    def _unique_record_for_source(self, source: str) -> OfficialLocaleRecord | None:
        """依正規化英文來源尋找唯一對應的官方 locale record。"""
        canonical = term_canonical_key(source)
        record = self.unique_record_by_normalized_en.get(canonical)
        if record is not None:
            return record
        # 已載入索引已記錄所有不歧義的正規化來源；若找不到，掃描整份
        # locale 不可能產生有效的唯一完整翻譯，且會造成不必要的成本。
        if self.unique_record_by_normalized_en:
            return None
        records = sorted(
            (
                record
                for record in self.records_by_key.values()
                if term_canonical_key(record.en_us) == canonical
            ),
            key=lambda value: value.key,
        )
        return records[0] if records else None

    def _standalone_allowlist_definition(
        self, canonical: str
    ) -> tuple[str, str, str | None] | None:
        """單獨成句時，回傳 allowlist 內且與官方推導一致的（來源, 目標, key）。

        官方語系若已有同名完整翻譯，會由 exact_text 處理，這裡不重複放行；
        推導目標與審核值不同（語系更新或歧義）時不放行。
        """
        expected = STANDALONE_ALLOWLIST.get(canonical)
        if expected is None or self._unique_target_for_source(canonical):
            return None
        derived = self._derived_terms().get(canonical)
        if derived is None or derived[1] != expected:
            return None
        return derived

    def _derived_terms(self) -> dict[str, tuple[str, str, str | None]]:
        """取得預先計算或由官方完整翻譯拆出的複合術語表。"""
        if self.derived_terms_by_en:
            return self.derived_terms_by_en
        if not self.records_by_key:
            return {}
        return _derive_component_terms(
            tuple(self.records_by_key.values()),
            self.unique_tw_by_normalized_en
            or {
                term_canonical_key(source): target
                for source, target in self.unique_tw_by_en.items()
            },
        )

    def _term_definition(self, term: str) -> tuple[str, str, str | None, str] | None:
        """將一個匹配到的官方術語解析成顯示來源、目標、key 與術語來源。"""
        canonical = term_canonical_key(term)
        target = self._unique_target_for_source(term)
        if target:
            record = self._unique_record_for_source(term)
            return (
                record.en_us if record is not None else term,
                target,
                record.key if record is not None else None,
                "official_term",
            )
        derived = self._derived_terms().get(canonical)
        if derived is None:
            return None
        display, target, key = derived
        return display, target, key, "compound_component"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _empty_index() -> OfficialLocaleIndex:
    """建立標記為 unavailable 的空官方語系索引。"""
    return OfficialLocaleIndex({}, {}, {}, "missing", available=False)


def _strip_minecraft_format_codes(value: str) -> str:
    """移除 Minecraft 格式碼以便比較純文字術語。"""
    return _MINECRAFT_FORMAT_CODE_RE.sub("", value)


def normalize_terminology_text(value: str) -> str:
    """正規化來源與術語文字後套用共用術語規劃。"""
    return normalize_term_text(_strip_minecraft_format_codes(value))


def _terminology_term_allowed(
    source: str,
    term: str,
    index: OfficialLocaleIndex,
    *,
    key: str | None = None,
) -> bool:
    """避免在句子或連字號上下文中強制套用泛用 UI 詞。"""
    normalized_source = normalize_terminology_text(source)
    normalized_term = normalize_terminology_text(term).strip()
    if term_canonical_key(normalized_source) == term_canonical_key(normalized_term):
        return True
    # A multi-word phrase carries enough context to be safe even when its
    # source key is from a tooltip or a custom module namespace.
    if len(_TERM_TOKEN_RE.findall(normalized_term)) > 1:
        return True
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
    """公開正式翻譯與評估共用的術語上下文篩選。"""
    return _terminology_term_allowed(
        source,
        term,
        index or load_official_locale_index(),
        key=key,
    )


def _terminology_tokens(value: str) -> tuple[str, ...]:
    """回傳可用於官方複合詞拆解的英文字詞。"""
    return tuple(
        token.casefold()
        for token in _TERM_TOKEN_RE.findall(normalize_terminology_text(value))
    )


def _remove_known_translation(value: str, target: str) -> str | None:
    """從官方目標文字移除一個已知術語，找不到時不猜測。"""
    if not target:
        return None
    index = value.casefold().find(target.casefold())
    if index < 0:
        return None
    return value[:index] + value[index + len(target) :]


def _clean_derived_target(value: str) -> str:
    """清理由官方複合對照消去後留下的目標詞。"""
    return value.strip(" \t\r\n.,:;!?()[]{}\"'“”‘’")


def _derive_component_terms(
    records: Sequence[OfficialLocaleRecord],
    unique_targets: dict[str, str],
) -> dict[str, tuple[str, str, str | None]]:
    """從互相對照的官方複合名稱推導穩定的單詞術語。

    官方語系不一定提供 ``Netherite`` 或 ``Block`` 的獨立 value；但同一
    namespace 內通常有多個可比較的複合名稱。這裡只在同一條官方記錄中，
    其他詞已有唯一官方目標、且消去後只剩一個未知詞與一個目標片段時推導，
    並要求來源 key 屬於 Minecraft 名稱型 namespace，避免從一般句子猜術語。
    """
    known: dict[str, str] = {
        canonical: target.strip()
        for canonical, target in unique_targets.items()
        if len(_terminology_tokens(canonical)) == 1
        and isinstance(target, str)
        and target.strip()
        and canonical != term_canonical_key(target)
    }
    derived: dict[str, tuple[str, str, str | None]] = {}
    candidate_keys: dict[str, dict[str, list[str]]] = {}
    candidate_display: dict[str, str] = {}

    for _round in range(8):
        candidate_keys.clear()
        candidate_display.clear()
        for record in sorted(records, key=lambda value: value.key):
            namespace = record.key.split(".", 1)[0]
            if namespace not in _NAME_LIKE_KEY_PREFIXES:
                continue
            tokens = _terminology_tokens(record.en_us)
            if len(tokens) < 2 or len(tokens) > 6:
                continue
            remaining = record.zh_tw.strip()
            unknown: list[tuple[str, str]] = []
            recognized = 0
            for raw_token, canonical in zip(
                _TERM_TOKEN_RE.findall(normalize_terminology_text(record.en_us)),
                tokens,
                strict=True,
            ):
                if canonical in _DERIVED_TERM_STOPWORDS:
                    continue
                target = known.get(canonical)
                if target:
                    reduced = _remove_known_translation(remaining, target)
                    if reduced is not None:
                        remaining = reduced
                        recognized += 1
                        continue
                unknown.append((canonical, raw_token))
            unknown_keys = {canonical for canonical, _raw in unknown}
            if recognized == 0 or len(unknown) != 1 or len(unknown_keys) != 1:
                continue
            canonical, raw_token = unknown[0]
            target = _clean_derived_target(remaining)
            if not target or target.casefold() == canonical:
                continue
            if len(target) > 40:
                continue
            candidate_keys.setdefault(canonical, {}).setdefault(target, []).append(
                record.key
            )
            candidate_display.setdefault(canonical, raw_token)

        additions = 0
        for canonical, target_candidates in candidate_keys.items():
            if not target_candidates:
                continue
            ranked_targets = sorted(
                target_candidates.items(),
                key=lambda value: (-len(value[1]), value[0]),
            )
            target, keys = ranked_targets[0]
            # Require repeated official evidence before inventing a component
            # mapping; one sentence can contain a context-specific translation.
            if len(keys) < 2:
                continue
            if len(ranked_targets) > 1:
                second_count = len(ranked_targets[1][1])
                if len(keys) <= second_count:
                    continue
            existing = known.get(canonical)
            if existing is not None and existing != target:
                continue
            if existing is None:
                known[canonical] = target
                additions += 1
            # Prefer a name-like namespace key as the evidence identity. The
            # candidates are already restricted to that namespace, so the
            # first stable key is sufficient and deterministic.
            derived[canonical] = (candidate_display[canonical], target, min(keys))
        if additions == 0:
            break
    return derived


def _load_index(root: Path) -> OfficialLocaleIndex:
    """載入官方語系索引並在資源根目錄間快取結果。"""
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
    normalized_en_values: dict[str, set[str]] = {}
    normalized_records: dict[str, list[OfficialLocaleRecord]] = {}
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
        normalized_source = term_canonical_key(en_us)
        normalized_en_values.setdefault(normalized_source, set()).add(zh_tw)
        normalized_records.setdefault(normalized_source, []).append(records[key])

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
    unique_en_normalized = {
        source: next(iter(targets))
        for source, targets in normalized_en_values.items()
        if len(targets) == 1
    }
    unique_records_normalized = {
        source: min(values, key=lambda value: value.key)
        for source, values in normalized_records.items()
        if source in unique_en_normalized and values
    }
    derived_terms = _derive_component_terms(records.values(), unique_en_normalized)
    terminology_terms = tuple(
        dict.fromkeys(
            (
                *unique_en.keys(),
                *(value[0] for value in derived_terms.values()),
            )
        )
    )
    name_like_terms = {
        term_canonical_key(record.en_us)
        for record in records.values()
        if term_canonical_key(record.en_us) in unique_en_normalized
        and record.key.split(".", 1)[0] in _NAME_LIKE_KEY_PREFIXES
    }
    name_like_terms.update(derived_terms)
    revision = ":".join(_file_sha256(locale_root / name)[:16] for name in _LOCALE_FILES)
    return OfficialLocaleIndex(
        records,
        unique_pairs,
        unique_en,
        revision,
        name_like_terms=frozenset(name_like_terms),
        unique_tw_by_normalized_en=unique_en_normalized,
        unique_record_by_normalized_en=unique_records_normalized,
        derived_terms_by_en=derived_terms,
        terminology_terms=terminology_terms,
        terminology_prefix_index=build_term_prefix_index(terminology_terms),
    )


@lru_cache(maxsize=4)
def load_official_locale_index(root: str | None = None) -> OfficialLocaleIndex:
    """依資源根目錄載入並快取一次官方語系索引。"""
    return _load_index(Path(root) if root else get_resource_root())


def clear_official_locale_cache() -> None:
    """清除官方語系索引的 lru_cache。"""
    load_official_locale_index.cache_clear()


register_cache("core.official_locale.index", clear_official_locale_cache)


def official_translation_for_item(item: dict[str, Any]) -> str | None:
    """在 LLM 請求前解析帶有可選 zh_cn 資料的項目。"""
    zh_cn = item.get("zh_cn") or item.get("official_zh_cn")
    if zh_cn is None:
        return None
    return load_official_locale_index().lookup_exact(
        item.get("path") or item.get("key"),
        item.get("source_text") or item.get("text"),
        zh_cn,
    )


def official_exact_translation(key: object, en_us: object, zh_cn: object) -> str | None:
    """提供所有合併與翻譯種類共用的鍵值精確查找。"""
    return load_official_locale_index().lookup_exact(key, en_us, zh_cn)


def build_translation_prompt(
    base_prompt: str,
    items: list[dict[str, Any]],
    *,
    profile: str,
    include_official: bool = True,
    include_moddb_reviewed: bool = True,
    item_ids: Sequence[str] | None = None,
) -> str:
    """在基礎提示詞後加入繁中與目前批次的穩定識別碼術語指令。"""
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
        item_ids=item_ids,
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
    """只保留目前批次中出現且沒有歧義的已審核術語。"""
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
    """找出單一來源項目中出現的官方與已審核術語對照。"""
    source_text = item.get("source_text") or item.get("text")
    if not isinstance(source_text, str) or not source_text.strip():
        return []
    normalized_source = normalize_terminology_text(source_text)
    candidates: dict[str, tuple[str, set[str]]] = {}

    if include_official and index.available:
        for match in index.official_terminology_matches_for_item(item):
            canonical = term_canonical_key(match.source_text)
            display, targets = candidates.setdefault(
                canonical, (match.source_text, set())
            )
            targets.add(match.target_text.strip())
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
    item_ids: Sequence[str] | None = None,
) -> list[str]:
    """把術語對照放在模型必須翻譯的實際負載識別碼旁。"""
    lines = [
        "\n【逐筆必用術語（只適用於同一 payload id）】",
        "每一行的來源詞若出現在該 id 的 value，譯文必須採用同一行的目標詞。",
    ]
    count = 0
    stable_ids = (
        tuple(str(item_id) for item_id in item_ids)
        if item_ids is not None
        else tuple(str(index) for index in range(len(items)))
    )
    if len(stable_ids) != len(items):
        raise ValueError("術語提示的 item_ids 數量必須與 items 相同")
    for item_id, item in zip(stable_ids, items, strict=True):
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
    """取得單一翻譯項目的快取術語規劃。"""
    return _item_terminology_terms(
        item,
        index or load_official_locale_index(),
        include_official=include_official,
        include_moddb_reviewed=include_moddb_reviewed,
    )


def official_terminology_matches_for_item(
    item: dict[str, Any], *, index: OfficialLocaleIndex | None = None
) -> list[OfficialTerminologyMatch]:
    """取得原本機翻與 Smart Review 共用的官方命中結果。"""
    return (
        index or load_official_locale_index()
    ).official_terminology_matches_for_item(item)


def terminology_matches_for_item(
    item: dict[str, Any], *, index: OfficialLocaleIndex | None = None
) -> list[TerminologyMatch]:
    """取得固定欄位的術語命中（``TerminologyMatch``），供 prompt 與 evidence adapter 使用。"""
    return [
        from_official_match(match)
        for match in official_terminology_matches_for_item(item, index=index)
    ]


def terminology_issues(
    item: dict[str, Any],
    translated_text: str,
    *,
    include_official: bool = True,
    include_moddb_reviewed: bool = True,
    terms: Sequence[tuple[str, str]] | None = None,
) -> list[str]:
    """以邊界啟發式回報缺少的權威術語，不代表完整語意準確率。"""
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
        if not contains_term(translated_text, target)
    ]


__all__ = [
    "OfficialLocaleIndex",
    "OfficialLocaleRecord",
    "OfficialTerminologyMatch",
    "build_translation_prompt",
    "load_official_locale_index",
    "normalize_terminology_text",
    "official_exact_translation",
    "official_terminology_matches_for_item",
    "official_translation_for_item",
    "terminology_issues",
    "terminology_matches_for_item",
    "terminology_term_allowed",
    "terminology_terms_for_item",
]
