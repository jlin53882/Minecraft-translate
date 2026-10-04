"""Small, dependency-free helpers for keeping secrets out of logs/errors."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_GOOGLE_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SECRET_FIELD_RE = re.compile(
    r'(?i)(["\']?(?:api[_-]?key|authorization|bearer|credential|password|secret|token)'
    r'["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|[^\s,;"\'}]+)'
)


def redact_text(value: Any, secrets: Iterable[str] = ()) -> str:
    """Return text safe for diagnostics, replacing keys and secret fields."""
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    text = _GOOGLE_KEY_RE.sub("[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return _SECRET_FIELD_RE.sub(r"\1[REDACTED]", text)


def redact_mapping(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact mapping values while preserving diagnostic structure."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(
                r"(?i)(api[_-]?key|authorization|bearer|credential|password|secret|token)",
                key_text,
            ):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_mapping(item, secrets)
        return result
    if isinstance(value, list):
        return [redact_mapping(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item, secrets) for item in value)
    return redact_text(value, secrets) if isinstance(value, str) else value
