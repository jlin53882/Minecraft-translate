"""Local calendar ranges resolved once to UTC half-open SQLite bounds."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def _utc_text(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .replace(tzinfo=None)
        .isoformat(sep=" ", timespec="seconds")
    )


def format_taipei_time(value: str | None) -> str:
    """Display SQLite UTC timestamps in the user's configured Taiwan time zone."""
    if not value:
        return "未知"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M")


def local_date_bounds_to_utc(start: date, end_inclusive: date) -> tuple[str, str]:
    """Convert inclusive Taipei local dates into a UTC ``[start, end)`` range."""
    if end_inclusive < start:
        raise ValueError("時間區間無效：起始日期不得晚於結束日期")
    start_local = datetime.combine(start, time.min, TAIPEI)
    end_local = datetime.combine(end_inclusive + timedelta(days=1), time.min, TAIPEI)
    return _utc_text(start_local), _utc_text(end_local)


def quick_date_bounds(
    preset: str, *, now: datetime | None = None
) -> tuple[str, str] | None:
    """Resolve today/yesterday/recent calendar dates from one fixed local instant."""
    if preset == "all":
        return None
    current = (now or datetime.now(UTC)).astimezone(TAIPEI)
    today = current.date()
    if preset == "today":
        start = end = today
    elif preset == "yesterday":
        start = end = today - timedelta(days=1)
    elif preset == "7d":
        start, end = today - timedelta(days=6), today
    elif preset == "30d":
        start, end = today - timedelta(days=29), today
    else:
        raise ValueError(f"未知快速時間範圍：{preset}")
    return local_date_bounds_to_utc(start, end)


def custom_date_bounds(start_text: str, end_text: str) -> tuple[str, str] | None:
    """Parse inclusive ISO local dates. Both bounds are required when one is set."""
    start_text, end_text = start_text.strip(), end_text.strip()
    if not start_text and not end_text:
        return None
    if not start_text or not end_text:
        raise ValueError("自訂時間區間需要同時填入起始與結束日期")
    try:
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
    except ValueError as exc:
        raise ValueError("日期格式請使用 YYYY-MM-DD") from exc
    return local_date_bounds_to_utc(start, end)
