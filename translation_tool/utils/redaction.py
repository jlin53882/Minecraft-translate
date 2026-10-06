"""Small, dependency-free helpers for keeping secrets out of logs/errors."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterable, Mapping
from typing import Any

_GOOGLE_KEY_RE = re.compile(r"\b(?:AIza[0-9A-Za-z_-]{20,}|AQ\.[0-9A-Za-z_-]+)\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SECRET_FIELD_RE = re.compile(
    r'(?i)(["\']?(?:api[_-]?key|authorization|bearer|credential|password|secret|token)'
    r'["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|[^\s,;"\'}]+)'
)


# 已知的機密字串（使用者設定的 API 金鑰）：不論格式，只要原樣出現在文字中就遮蔽。
# 由 config_manager 載入設定時登錄；太短的字串不登錄（避免把一般文字誤遮蔽）。
_MIN_SECRET_LEN = 8
_known_secrets: set[str] = set()
_known_lock = threading.Lock()


def register_secrets(secrets: Iterable[str]) -> None:
    """登錄已知的機密字串（累加；舊值保留，因為舊金鑰仍可能出現在殘留的日誌或例外中）。"""
    cleaned = {
        s for s in secrets if isinstance(s, str) and len(s.strip()) >= _MIN_SECRET_LEN
    }
    if not cleaned:
        return
    with _known_lock:
        _known_secrets.update(s.strip() for s in cleaned)


def known_secrets() -> frozenset[str]:
    with _known_lock:
        return frozenset(_known_secrets)


def _mask_known(text: str, extra: Iterable[str] = ()) -> str:
    # 長的先換，避免短字串是長字串的一部分時留下殘片
    for secret in sorted(
        {*known_secrets(), *(str(s) for s in extra if s)}, key=len, reverse=True
    ):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def redact_secrets(value: Any) -> str:
    """高精確度遮蔽：已知金鑰、Google API key 格式與 Bearer token。

    給「所有輸出都會經過」的全域出口（日誌檔、UI 日誌、提示訊息）使用；不套用欄位名稱規則，
    以免把一般訊息裡的 ``token: 123`` 之類文字誤遮蔽。
    """
    text = _mask_known(str(value))
    text = _GOOGLE_KEY_RE.sub("[REDACTED]", text)
    return _BEARER_RE.sub("Bearer [REDACTED]", text)


# ``%(message)s`` 的 percent-style 規格：可帶旗標、寬度、精度與型別（``%(message)-80s``、``%(message).200s``…）
_MESSAGE_FIELD_RE = re.compile(r"%\(message\)[-+ #0]*\d*(?:\.\d+)?[sra]")


def with_task_tag(fmt: str) -> str:
    """在 ``%(message)…`` 前面插入任務標籤 ``%(task_tag)s``（已含時不重複）。

    ``task_tag`` 由 ``ui_mirror`` 安裝的 LogRecord 工廠填入：任務執行中是
    ``[task=<任務名稱>/<識別>] ``，沒有任務時是空字串。這樣同時執行多個任務時，
    ``app.log`` 的每一行都看得出屬於哪一個任務，核心流程不需要自己知道 UI／任務。

    ``%(message)s`` 之外，帶寬度／精度／旗標的合法寫法（``%(message)-80s``）也會插入；
    沒有 ``%(message)`` 欄位的格式不動。
    """
    if "%(task_tag)" in fmt:
        return fmt
    match = _MESSAGE_FIELD_RE.search(fmt)
    if match is None:
        return fmt
    return f"{fmt[: match.start()]}%(task_tag)s{fmt[match.start() :]}"


def tag_continuation_lines(text: str, tag: str) -> str:
    """多行輸出（多行訊息、traceback）的每一個實體行都帶上任務標籤。

    ``logging`` 以記錄為單位輸出，一筆記錄可能有很多行（``logger.exception`` 的
    traceback、含換行的訊息）；只有第一行有標籤的話，``grep '[task=…]'`` 會漏掉後面的行。
    第一行已經由格式帶標籤（``%(task_tag)s``），這裡補其餘的行；格式沒有帶標籤時不動。
    """
    if not tag or "\n" not in text:
        return text
    first, *rest = text.split("\n")
    if tag not in first:
        return text
    return "\n".join(
        [
            first,
            *(
                f"{tag.rstrip()} {line}".rstrip() if line else tag.rstrip()
                for line in rest
            ),
        ]
    )


class RedactingFormatter(logging.Formatter):
    """格式化後（含 traceback）再遮蔽機密，確保任何 handler 輸出都不含金鑰。

    同時自動在格式中加上任務標籤（見 ``with_task_tag``），讓 log 檔與終端機的每一行
    都標示它屬於哪個任務。
    """

    def __init__(self, fmt: str | None = None, *args: Any, **kwargs: Any) -> None:
        if fmt is not None and kwargs.get("style", "%") == "%":
            fmt = with_task_tag(fmt)
        super().__init__(fmt, *args, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        # 工廠安裝之前建立的記錄（或第三方直接建立的 LogRecord）沒有這些欄位
        for field in ("task_id", "task_name", "task_tag"):
            if not hasattr(record, field):
                setattr(record, field, "")
        text = redact_secrets(super().format(record))
        return tag_continuation_lines(text, record.task_tag)


def redact_text(value: Any, secrets: Iterable[str] = ()) -> str:
    """Return text safe for diagnostics, replacing keys and secret fields."""
    text = _mask_known(str(value), secrets)
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
