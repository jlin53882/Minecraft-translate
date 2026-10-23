"""翻譯步驟的終態判定共用契約。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DONE_STATUS = "DONE"
CANCELLED_STATUS = "CANCELLED"
_FAILURE_STATUSES = frozenset({"ABORTED", "ERROR", "FAILED"})


def normalized_translation_status(result: Any) -> str | None:
    """讀取翻譯器回傳的狀態；缺少或空白狀態一律回傳 ``None``。"""
    if isinstance(result, Mapping):
        raw_status = result.get("status")
    else:
        raw_status = getattr(result, "status", None)
    if not isinstance(raw_status, str):
        return None
    normalized = raw_status.strip().upper()
    return normalized or None


def translation_stop_reason(result: Any) -> str | None:
    """回傳阻止後續注入的原因；只有明確 ``DONE`` 會回傳 ``None``。"""
    status = normalized_translation_status(result)
    cancelled = (
        bool(result.get("cancelled"))
        if isinstance(result, Mapping)
        else bool(getattr(result, "cancelled", False))
    )
    if cancelled or status == CANCELLED_STATUS:
        return "cancelled"
    if status == DONE_STATUS:
        return None
    if status is None:
        return "translation_status_unknown"
    if status in _FAILURE_STATUSES:
        return "translation_failed"
    return "translation_incomplete"


__all__ = [
    "CANCELLED_STATUS",
    "DONE_STATUS",
    "normalized_translation_status",
    "translation_stop_reason",
]
