"""批次計時欄位的 canonical 名稱與舊欄位別名。

事件與 metrics 一律以毫秒的 ``*_ms`` 為 canonical；舊版（智慧校對）輸出的 ``*_seconds`` 欄位
仍存在於既有歷史資料與事件，讀取端透過 ``timing_seconds`` 取值，不再各自維護名稱對照。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

TIMING_SCHEMA_VERSION: Final = 1

# canonical 毫秒欄位 -> 依優先順序嘗試的舊版秒數欄位。
LEGACY_SECONDS_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "setup_ms": ("setup_seconds",),
    "selection_ms": ("selection_seconds",),
    "planning_ms": ("planning_seconds",),
    "prompt_build_ms": ("preparation_seconds",),
    "quota_wait_ms": ("rate_limit_wait_seconds",),
    "retry_backoff_ms": ("retry_wait_seconds",),
    "network_retry_wait_ms": ("network_retry_wait_seconds",),
    "api_ms": ("api_seconds", "api_elapsed_seconds"),
    "response_parse_ms": ("parse_seconds",),
    "history_write_ms": ("result_write_seconds",),
    "run_record_ms": ("run_record_seconds",),
    "validation_repair_api_ms": ("validation_repair_api_seconds",),
    "validation_repair_parse_ms": ("validation_repair_parse_seconds",),
    "batch_ms": ("batch_seconds",),
    "total_ms": ("duration_seconds", "total_duration_seconds"),
    "elapsed_ms": ("elapsed_seconds",),
    "wait_ms": ("wait_seconds",),
    "retry_after_ms": ("retry_after_seconds",),
}


def _number(value: Any) -> float | None:
    """可轉成非負數的值回傳 float，其餘回傳 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def timing_seconds(source: Mapping[str, Any], canonical_ms: str) -> float | None:
    """從 ``source`` 取得某個計時欄位的秒數：先讀 canonical ``*_ms``，再讀舊版秒數別名。"""
    ms = _number(source.get(canonical_ms))
    if ms is not None:
        return ms / 1000
    for legacy in LEGACY_SECONDS_ALIASES.get(canonical_ms, ()):
        seconds = _number(source.get(legacy))
        if seconds is not None:
            return seconds
    return None


def timing_ms(source: Mapping[str, Any], canonical_ms: str) -> int | None:
    """同 ``timing_seconds``，但以整數毫秒回傳。"""
    seconds = timing_seconds(source, canonical_ms)
    return None if seconds is None else round(seconds * 1000)


def with_legacy_seconds(event: Mapping[str, Any]) -> dict[str, Any]:
    """回傳補上舊版 ``*_seconds`` 別名的事件複本（只補缺的欄位，不覆蓋既有值）。"""
    result = dict(event)
    # 舊版 resume 事件把實際等待時間放在 elapsed_seconds；canonical 事件將它
    # 與批次經過時間區分為 waited_ms，因此這個別名必須優先於 elapsed_ms。
    if str(result.get("kind") or "").endswith("_resume"):
        waited_ms = _number(result.get("waited_ms"))
        if waited_ms is not None:
            result.setdefault("elapsed_seconds", round(waited_ms / 1000, 3))
    for canonical, legacy_names in LEGACY_SECONDS_ALIASES.items():
        ms = _number(result.get(canonical))
        if ms is None:
            continue
        for legacy in legacy_names:
            result.setdefault(legacy, round(ms / 1000, 3))
    return result


def format_duration(seconds: float | None) -> str:
    """把耗時格式化成給人看的文字：不足 1 秒用毫秒，其餘用秒（30s、1.5s），超過 1 分鐘用分秒。

    沒有數值時回傳 ``--``。例如 30000 ms 顯示為 ``30s``，850 ms 顯示為 ``850ms``。
    """
    value = _number(seconds)
    if value is None:
        return "--"
    if value < 1:
        return f"{round(value * 1000)}ms"
    if value < 60:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text}s"
    total = round(value)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


def format_duration_ms(milliseconds: Any) -> str:
    """``format_duration`` 的毫秒版本；``None``／非數值回傳 ``--``。"""
    value = _number(milliseconds)
    return format_duration(None if value is None else value / 1000)
