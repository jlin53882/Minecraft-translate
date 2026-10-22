"""供應商 Retry-After 的純解析：不含任何 retry／等待政策。

Gemini LM、智慧校對與 ChatGPT 共用同一套輸入解析，讓同一份 Retry-After 算出相同等待秒數；
各流程自己的上限、換模型、換 key 等決策仍留在各自的呼叫端。

解析規則
- 來源優先序：HTTP header ``Retry-After`` > Google ``RetryInfo.retryDelay``。
  header 存在但無法解析時，退回 body。
- header 接受非負十進位秒數（可含小數）與 HTTP-date；``retryDelay`` 接受 ``"34.9s"`` 或純數字。
- 小數秒不截斷（``0.5s`` 就是 0.5 秒）。
- 負數、``NaN``、``Infinity``、格式錯誤、非字串／數字型別一律視為無效，回傳 ``None``；
  過去的 HTTP-date 代表「可以立即重試」，回傳 ``0.0``。
- 本模組不設上限；是否拒絕過長的等待由呼叫端的政策決定。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

_RETRY_AFTER_HEADER = "Retry-After"


def _finite_non_negative(value: float) -> float | None:
    return value if math.isfinite(value) and value >= 0 else None


def parse_retry_seconds(value: object, *, allow_s_suffix: bool = False) -> float | None:
    """把秒數（數字或字串）解析成有限的非負秒數；無效回傳 ``None``。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return _finite_non_negative(float(value))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if allow_s_suffix and text.endswith("s"):
        text = text[:-1].strip()
    if not text:
        return None
    try:
        return _finite_non_negative(float(text))
    except ValueError:
        return None


def parse_retry_after_header_value(
    value: object, *, now: datetime | None = None
) -> float | None:
    """解析單一 ``Retry-After`` header 值（秒數或 HTTP-date）。"""
    seconds = parse_retry_seconds(value)
    if seconds is not None:
        return seconds
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        retry_at = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    delta = (retry_at - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(delta):
        return None
    return max(0.0, delta)


def _header_value(headers: object, name: str) -> object:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None and isinstance(headers, Mapping):
        lowered = name.lower()
        for key, candidate in headers.items():
            if isinstance(key, str) and key.lower() == lowered:
                return candidate
    return value


def parse_retry_info_seconds(body: object) -> float | None:
    """從 Google error body 的 ``RetryInfo.retryDelay`` 讀取有效秒數。"""
    error_value = body.get("error") if isinstance(body, dict) else None
    details = error_value.get("details") if isinstance(error_value, dict) else None
    if not isinstance(details, list):
        return None
    for detail in details:
        if not isinstance(detail, dict) or not str(detail.get("@type", "")).endswith(
            "RetryInfo"
        ):
            continue
        seconds = parse_retry_seconds(detail.get("retryDelay"), allow_s_suffix=True)
        if seconds is not None:
            return seconds
    return None


def parse_retry_after(
    headers: Any, body: object = None, *, now: datetime | None = None
) -> float | None:
    """合併 header 與 body 的 Retry-After，回傳有效秒數或 ``None``。"""
    header_seconds = parse_retry_after_header_value(
        _header_value(headers, _RETRY_AFTER_HEADER), now=now
    )
    if header_seconds is not None:
        return header_seconds
    return parse_retry_info_seconds(body)


def parse_response_retry_after(
    response: object, *, now: datetime | None = None
) -> float | None:
    """從 ``requests.Response`` 風格的物件讀取 Retry-After；body 不是 JSON 時只看 header。"""
    if response is None:
        return None
    try:
        body = response.json()  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        body = None
    return parse_retry_after(getattr(response, "headers", None), body, now=now)
