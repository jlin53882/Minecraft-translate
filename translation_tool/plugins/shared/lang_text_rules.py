from __future__ import annotations

import re

# Match minecraft-like color/format tokens, e.g. &a §l
_FMT_RE = re.compile(r"(?:&|§)[0-9a-fk-or]", re.IGNORECASE)


def _strip_fmt(s: str) -> str:
    """Remove inline formatting tokens (e.g. &a / §l) from text."""
    return _FMT_RE.sub("", s)


def is_already_zh(s: str) -> bool:
    """Heuristic: after format-strip, if text has CJK and little/no English, treat as already zh."""
    t = _strip_fmt(s).strip()
    if not t:
        return True
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", t))
    if not has_cjk:
        return False
    letters = len(re.findall(r"[A-Za-z]", t))
    return letters <= 2
