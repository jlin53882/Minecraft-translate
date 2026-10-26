"""替換規則的靜態診斷：找出彼此矛盾或不會生效的規則（純邏輯，不碰 UI）。

診斷只提出「可疑」的規則，不阻擋儲存。規則清單的第 N 條（1 起算）即畫面「#」欄的編號。

偵測項目（``kind``）：

- ``duplicate_from``：與另一條規則的原詞完全相同。
- ``self_reference``：譯詞含有自己的原詞（固定文字規則）。
- ``cross_reference``：譯詞含有另一條規則的原詞（至少 2 字），兩條規則的用詞互相矛盾。
- ``edge_space``：原詞或譯詞前後有空白。
- ``converted_source``：原詞含有 s2twp 轉換後會變成別的字的字，替換發生在轉換之後，
  所以這個原詞不會出現在文字裡（例如「岩」會先被轉成「巖」）。

含正則符號的原詞（``.?*[]()\\``）不做字串包含判斷，只做重複與空白檢查。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

_REGEX_CHARS = ".?*[]()\\"
#: 短於這個長度的原詞不參與「交叉引用」，單字幾乎一定被別的譯詞包含，只會產生雜訊
MIN_CROSS_LENGTH = 2

DUPLICATE_FROM = "duplicate_from"
SELF_REFERENCE = "self_reference"
CROSS_REFERENCE = "cross_reference"
EDGE_SPACE = "edge_space"
CONVERTED_SOURCE = "converted_source"


@dataclass(frozen=True, slots=True)
class RuleIssue:
    """一條規則的一項問題。``other`` 是相關規則的索引（0 起算），沒有則為 None。"""

    kind: str
    message: str
    other: int | None = None


def _is_fixed(text: str) -> bool:
    return not any(ch in text for ch in _REGEX_CHARS)


def _changed_chars(source: str, converted: str) -> str:
    """列出被轉換的字（「岩→巖」）；長度不同時退回整段。"""
    if len(source) != len(converted):
        return f"{source}→{converted}"
    pairs = [f"{a}→{b}" for a, b in zip(source, converted, strict=True) if a != b]
    return "、".join(dict.fromkeys(pairs))


def _contained_froms(
    text: str, by_length: Mapping[int, Mapping[str, list[int]]]
) -> list[tuple[str, list[int]]]:
    """``text`` 中出現的所有原詞（依長度枚舉子字串，成本與規則數無關）。"""
    found = []
    size = len(text)
    for length, table in by_length.items():
        if length > size:
            continue
        for start in range(size - length + 1):
            indexes = table.get(text[start : start + length])
            if indexes is not None:
                found.append((text[start : start + length], indexes))
    return found


def diagnose_rules(
    rules: Sequence[Mapping[str, object]],
    convert: Callable[[str], str] | None = None,
) -> dict[int, list[RuleIssue]]:
    """診斷整份規則；回傳 ``{規則索引: [RuleIssue, ...]}``（沒有問題的規則不在內）。

    ``convert`` 為 s2twp 之類的文字轉換函式；省略時不檢查 ``converted_source``。
    1325 條規則約數毫秒，規則數增加時成本大致線性成長。
    """
    issues: dict[int, list[RuleIssue]] = {}

    def add(idx: int, issue: RuleIssue) -> None:
        issues.setdefault(idx, []).append(issue)

    froms = [str(r.get("from") or "") for r in rules]
    tos = [str(r.get("to") or "") for r in rules]

    first_seen: dict[str, int] = {}
    # {原詞長度: {原詞: [規則索引]}}，只收固定文字且夠長的原詞
    by_length: dict[int, dict[str, list[int]]] = {}
    for idx, src in enumerate(froms):
        if not src.strip():
            continue
        if src in first_seen:
            add(
                idx,
                RuleIssue(
                    DUPLICATE_FROM,
                    f"原詞與規則 #{first_seen[src] + 1} 相同",
                    first_seen[src],
                ),
            )
        else:
            first_seen[src] = idx
        if _is_fixed(src) and len(src) >= MIN_CROSS_LENGTH:
            by_length.setdefault(len(src), {}).setdefault(src, []).append(idx)

    for idx, (src, dst) in enumerate(zip(froms, tos, strict=True)):
        if not src.strip():
            continue
        _check_rule(idx, src, dst, by_length, add)
        if convert is not None and _is_fixed(src):
            converted = convert(src)
            if converted != src:
                add(
                    idx,
                    RuleIssue(
                        CONVERTED_SOURCE,
                        f"原詞含 s2twp 會轉換的字（{_changed_chars(src, converted)}），"
                        "替換時文字已轉換，這條規則不會命中",
                    ),
                )
    return issues


def _check_rule(idx, src, dst, by_length, add) -> None:
    """單一規則的空白、自我引用與交叉引用檢查。"""
    if src != src.strip() or dst != dst.strip():
        side = "原詞" if src != src.strip() else "譯詞"
        add(idx, RuleIssue(EDGE_SPACE, f"{side}前後有空白"))
    if not _is_fixed(src) or not dst:
        return
    if src in dst:
        add(idx, RuleIssue(SELF_REFERENCE, f"譯詞含有自己的原詞「{src}」"))
    reported: set[int] = set()
    for word, owners in _contained_froms(dst, by_length):
        for other in owners:
            if other == idx or word == src or other in reported:
                continue
            reported.add(other)
            add(
                idx,
                RuleIssue(
                    CROSS_REFERENCE,
                    f"譯詞含有規則 #{other + 1} 的原詞「{word}」",
                    other,
                ),
            )
