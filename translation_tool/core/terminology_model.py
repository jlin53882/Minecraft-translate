"""術語命中的固定資料模型，以及機翻 prompt 與智慧校對 evidence 的 adapter。

職責分層：

- immutable terminology index：``official_locale.OfficialLocaleIndex``（載入後不可變）。
- matching resolver：``OfficialLocaleIndex.official_terminology_matches_for_item``
  負責從來源文字解析命中，詞界規則在 ``utils/text_matching.py``。
- 資料模型：本模組的 ``TerminologyMatch``，欄位固定，供兩條下游共用。
- prompt adapter：``prompt_term_pair``／``prompt_glossary_key``，輸出機翻 prompt 使用的文字。
- evidence adapter：``evidence_context_entry``，輸出智慧校對 official evidence 條目。

本模組不依賴 ``official_locale``，避免循環 import；來源命中以 duck typing 轉換。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AUTHORITY_OFFICIAL_MINECRAFT = "official_minecraft"
CONFIDENCE_HIGH = "high"

USAGE_FULL_TRANSLATION = "official_full_translation"
USAGE_PHRASE = "official_phrase"
USAGE_TERM = "official_term"
USAGE_COMPONENT = "compound_component"

_FULL_MATCH_TYPES = frozenset({"exact_key", "exact_text"})


@dataclass(frozen=True)
class TerminologyMatch:
    """一筆術語命中；機翻 prompt 與智慧校對 evidence 共用同一組欄位。

    - ``authority``：術語權威來源（目前為官方 Minecraft 語系）。
    - ``usage``：命中的用途（完整翻譯、官方片語、官方單詞、複合詞拆解出的單詞）。
    - ``match_type``：命中方式（exact_key／exact_text／official_phrase／source_substring）。
    - ``source_key``：官方語系 key；沒有可信 key 時為空字串。
    - ``evidence_id``：不依賴排序的穩定識別碼。
    """

    source_text: str
    target_text: str
    authority: str
    usage: str
    match_type: str
    confidence: str
    source_key: str
    evidence_id: str


def from_official_match(match: Any) -> TerminologyMatch:
    """把 ``OfficialTerminologyMatch`` 轉成固定欄位的 ``TerminologyMatch``。"""
    return TerminologyMatch(
        source_text=match.source_text,
        target_text=match.target_text,
        authority=AUTHORITY_OFFICIAL_MINECRAFT,
        usage=match.term_origin,
        match_type=match.match_type,
        confidence=CONFIDENCE_HIGH,
        source_key=match.key or "",
        evidence_id=match.stable_id,
    )


def prompt_term_pair(match: TerminologyMatch) -> tuple[str, str]:
    """機翻 prompt 使用的（來源詞, 目標詞）。"""
    return match.source_text, match.target_text.strip()


def prompt_glossary_key(match: TerminologyMatch) -> str:
    """只有完整翻譯命中才在 prompt 顯示 key；子字串與複合詞不顯示，避免誤導。"""
    return match.source_key if match.match_type in _FULL_MATCH_TYPES else ""


def evidence_limitations(match: TerminologyMatch) -> str:
    """智慧校對 evidence 的限制說明文字。"""
    if match.match_type in _FULL_MATCH_TYPES:
        return "官方 Minecraft 完整翻譯；高信心"
    if match.match_type == "official_phrase":
        return "官方 Minecraft 完整片語；高信心"
    if match.usage == USAGE_COMPONENT:
        return "官方 Minecraft 術語；由官方複合詞拆解；高信心"
    return "官方 Minecraft 術語；高信心"


def evidence_context_entry(match: TerminologyMatch) -> dict[str, Any]:
    """智慧校對 official evidence 條目。"""
    return {
        "id": match.evidence_id,
        "reference_type": "official_locale",
        "mod_id": "minecraft",
        "kind": "lang",
        "key": match.source_key,
        "mc_version": "bundled",
        "en_us": match.source_text,
        "zh_tw": match.target_text,
        "source_text": match.source_text,
        "target_text": match.target_text,
        "review_status": "reviewed",
        "source": "bundled_official_locale",
        "source_label": "官方 Minecraft",
        "match_type": match.match_type,
        "term_origin": match.usage,
        "confidence": match.confidence,
        "limitations": evidence_limitations(match),
    }


__all__ = [
    "AUTHORITY_OFFICIAL_MINECRAFT",
    "CONFIDENCE_HIGH",
    "TerminologyMatch",
    "evidence_context_entry",
    "evidence_limitations",
    "from_official_match",
    "prompt_glossary_key",
    "prompt_term_pair",
]
