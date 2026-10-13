"""學名的純文字判斷規則（不碰網路、設定或資料庫）。

只看「Genus species」的格式會把大量一般文字（Energy usage、Creeper dies）
誤判成學名，所以 :func:`is_species_candidate` 預設還要求 key 帶有學名提示
（例如 ``.latin``），或值本身有雜交／亞種／變種記號。
"""

from __future__ import annotations

import re

SPECIES_NAME_REGEX = re.compile(
    r"^[A-Z][a-z-]+ (?:× )?[a-z][a-z-]+"
    r"(?: (?:(?:subsp|ssp|var|f)\. )?[a-z][a-z-]+)?$"
)

# key 的單字（含 camelCase 拆開後）出現這些詞，代表該條目存放的是學名。
SPECIES_KEY_HINTS = frozenset(
    {"latin", "scientific", "binomial", "taxon", "taxonomy", "species", "sciname"}
)
# 雜交（×）與亞種／變種記號幾乎不會出現在一般介面文字。
TAXONOMIC_MARKERS = ("×", "var.", "subsp.", "ssp.", "f.")

_KEY_WORD_SPLIT = re.compile(r"(?<=[a-z])(?=[A-Z])|[^A-Za-z0-9]+")
_TAXONOMIC_MARKER = re.compile(r" (?:× |(?:subsp|ssp|var|f)\. )")


def normalize_species_name(name: str) -> str:
    """移除前後與重複空白，保留學名本身的大小寫。"""
    return " ".join(name.split())


def is_potential_species_name(name: str) -> bool:
    """判斷是否可能是物種名稱（只看格式）。"""
    if not isinstance(name, str):
        return False
    return bool(SPECIES_NAME_REGEX.fullmatch(normalize_species_name(name)))


def has_species_key_hint(key: str) -> bool:
    """key 是否像存放學名的條目，例如 ``block.mod.olive.latin``。"""
    words = {word.lower() for word in _KEY_WORD_SPLIT.split(key) if word}
    return not words.isdisjoint(SPECIES_KEY_HINTS)


def is_species_candidate(
    key: str, value: str, *, require_key_hint: bool = True
) -> bool:
    """判斷一筆條目是否可能是學名；``require_key_hint=False`` 時只看值的格式。"""
    if not is_potential_species_name(value):
        return False
    if not require_key_hint:
        return True
    return has_species_key_hint(key) or bool(
        _TAXONOMIC_MARKER.search(normalize_species_name(value))
    )
