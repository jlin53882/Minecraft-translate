"""Pure Minecraft translation token analysis shared by storage and UI layers."""

from __future__ import annotations

import re
from collections import Counter

from translation_tool.translation_db.models import QualityFilter, QualityIssueDelta

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
    return _token_issues_from_counts(format_tokens(source), format_tokens(translated))


def repair_input_issues(
    source: str, previous_translation: str
) -> tuple[bool, tuple[str, ...], bool]:
    """Return whether a repair row is eligible, hard issues, and mixed-newline flag.

    Real LF count mismatches are eligible for repair. CRLF is naturally counted
    as one LF token by ``format_tokens``; literal ``\\n`` remains separate.
    """
    wanted = format_tokens(source)
    previous = format_tokens(previous_translation)
    newline_mismatch = wanted["\n"] != previous["\n"]
    wanted.pop("\n", None)
    previous.pop("\n", None)
    hard_issues = tuple(_token_issues_from_counts(wanted, previous))
    return (
        newline_mismatch or bool(hard_issues),
        hard_issues,
        bool(newline_mismatch and hard_issues),
    )


def repair_output_issues(source: str, translated: str) -> list[str]:
    """Validate repair output while allowing only real LF count differences."""
    wanted, actual = format_tokens(source), format_tokens(translated)
    wanted.pop("\n", None)
    actual.pop("\n", None)
    return _token_issues_from_counts(wanted, actual)


def _token_issues_from_counts(wanted: Counter[str], actual: Counter[str]) -> list[str]:
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


def token_issue_deltas(
    source: str, before: str, after: str
) -> tuple[QualityIssueDelta, ...]:
    """Return structural missing/extra token counts before and after an edit."""
    wanted = format_tokens(source)
    before_tokens = format_tokens(before)
    after_tokens = format_tokens(after)
    return _token_issue_deltas_from_counts(wanted, before_tokens, after_tokens)


def _token_issue_deltas_from_counts(
    wanted: Counter[str],
    before_tokens: Counter[str],
    after_tokens: Counter[str],
) -> tuple[QualityIssueDelta, ...]:
    before_missing = wanted - before_tokens
    after_missing = wanted - after_tokens
    before_extra = before_tokens - wanted
    after_extra = after_tokens - wanted
    deltas = []
    for direction, old_counts, new_counts in (
        ("missing", before_missing, after_missing),
        ("extra", before_extra, after_extra),
    ):
        for token in sorted(set(old_counts) | set(new_counts)):
            old_count, new_count = old_counts[token], new_counts[token]
            if old_count != new_count:
                deltas.append(QualityIssueDelta(token, direction, old_count, new_count))
    return tuple(deltas)


def token_quality_comparison(
    source: str, before: str, after: str
) -> tuple[list[str], list[str], tuple[QualityIssueDelta, ...]]:
    """Analyze the old and new text once each for a batch preview."""
    wanted = format_tokens(source)
    before_tokens = format_tokens(before)
    after_tokens = format_tokens(after)
    return (
        _token_issues_from_counts(wanted, before_tokens),
        _token_issues_from_counts(wanted, after_tokens),
        _token_issue_deltas_from_counts(wanted, before_tokens, after_tokens),
    )


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
