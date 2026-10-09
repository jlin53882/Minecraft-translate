"""Pure Minecraft translation token analysis shared by storage and UI layers."""

from __future__ import annotations

import re
from collections import Counter

from translation_tool.translation_db.models import QualityFilter

_SCAN_RE = re.compile(
    r"§[0-9a-fk-orA-FK-OR]|%(?:\d+\$)?[sdfxXeEgGcb%]|\$\(|\{\d*\}|\\n|\n"
)
_TOOLTIP_PREFIX = "$(t:"
_TOOLTIP_TOKEN = "$(t:…)"


def _macro_end(text: str, start: int) -> int | None:
    depth = 1
    for i in range(start, len(text)):
        char = text[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    first = text.find(")", start)
    return None if first < 0 else first + 1


def _iter_tokens(text: str):
    pos = 0
    while True:
        match = _SCAN_RE.search(text, pos)
        if match is None:
            return
        if match.group(0) == "$(":
            end = _macro_end(text, match.end())
            if end is None:
                pos = match.end()
                continue
            yield text[match.start() : end]
            pos = end
        else:
            yield match.group(0)
            pos = match.end()


def format_tokens(text: str) -> Counter[str]:
    """Count placeholders, Minecraft codes, Patchouli macros and line breaks."""
    tokens: Counter[str] = Counter()
    for raw in _iter_tokens(text or ""):
        token = raw.replace("\r", "")
        if token.startswith(_TOOLTIP_PREFIX):
            token = _TOOLTIP_TOKEN
        tokens[token] += 1
    return tokens


def token_issues(source: str, translated: str) -> list[str]:
    """Return stable Traditional Chinese missing/extra token notes."""
    wanted, actual = format_tokens(source), format_tokens(translated)
    issues: list[str] = []
    for token in sorted(set(wanted) | set(actual)):
        delta = actual[token] - wanted[token]
        if delta:
            name = "提示文字 $(t:…)" if token == _TOOLTIP_TOKEN else token
            if token == "\n":
                name = "換行"
            elif token == "\\n":
                name = "字面 \\n"
            issues.append(
                f"{'多了' if delta > 0 else '少了'} {abs(delta)} 個「{name}」"
            )
    return issues


def whitespace_note(text: str) -> str:
    """Describe leading/trailing whitespace without modifying stored text."""
    if not text or text == text.strip():
        return ""
    return "譯文前後有空白或換行，儲存時會原樣保留"


def token_category(token: str) -> str:
    if token.startswith("§"):
        return "minecraft"
    if token.startswith("$("):
        return "patchouli"
    if token.startswith("%") or (token.startswith("{") and token.endswith("}")):
        return "placeholder"
    if token in ("\n", "\\n"):
        return "newline"
    return "other"


def quality_state(source: str, translated: str) -> tuple[str, tuple[str, ...], str]:
    """Return status, diagnostics and whitespace warning for one effective value."""
    if not source:
        return "unknown_source", (), whitespace_note(translated)
    if not translated:
        return "missing_translation", (), ""
    issues = tuple(token_issues(source, translated))
    note = whitespace_note(translated)
    if issues:
        return "mismatch", issues, note
    return ("whitespace" if note else "consistent"), issues, note


def matches_quality(source: str, translated: str, criteria: QualityFilter) -> bool:
    if not criteria.active:
        return True
    _, _, whitespace = quality_state(source, translated)
    status = criteria.status
    if status == "all":
        status = "mismatch"
    if status == "unknown_source":
        return not source
    if status == "missing_translation":
        return bool(source) and not translated
    if status == "whitespace":
        return bool(translated) and bool(whitespace)
    if status == "consistent":
        return bool(source and translated) and format_tokens(source) == format_tokens(
            translated
        )
    if status != "mismatch" or not source or not translated:
        return False

    wanted, actual = format_tokens(source), format_tokens(translated)
    missing = wanted - actual
    extra = actual - wanted
    direction = criteria.direction
    if direction not in {"all", "missing", "extra"}:
        raise ValueError(f"未知 token 不一致方向：{direction}")
    category = criteria.token_category
    if category not in {"all", "placeholder", "minecraft", "patchouli", "newline"}:
        raise ValueError(f"未知 token 類別：{category}")

    def selected(counter: Counter[str]) -> bool:
        return any(
            count > 0 and (category == "all" or token_category(token) == category)
            for token, count in counter.items()
        )

    return (direction in {"all", "missing"} and selected(missing)) or (
        direction in {"all", "extra"} and selected(extra)
    )
