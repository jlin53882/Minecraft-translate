"""批次計時欄位契約：canonical 為毫秒，舊版秒數欄位只透過別名表讀取。"""

from translation_tool.core.batch_timing import (
    LEGACY_SECONDS_ALIASES,
    timing_ms,
    timing_seconds,
    with_legacy_seconds,
)


def test_canonical_ms_wins_over_legacy():
    event = {"api_ms": 1500, "api_seconds": 9}
    assert timing_seconds(event, "api_ms") == 1.5
    assert timing_ms(event, "api_ms") == 1500


def test_falls_back_to_legacy_aliases_in_order():
    assert timing_seconds({"api_elapsed_seconds": 2}, "api_ms") == 2.0
    assert timing_seconds({"api_seconds": 1, "api_elapsed_seconds": 2}, "api_ms") == 1.0


def test_missing_or_invalid_values_return_none():
    assert timing_seconds({}, "api_ms") is None
    assert timing_seconds({"api_ms": "x", "api_seconds": None}, "api_ms") is None
    assert timing_seconds({"api_ms": True}, "api_ms") is None
    assert timing_seconds({"api_ms": -5}, "api_ms") == 0.0


def test_with_legacy_seconds_adds_missing_aliases_only():
    event = with_legacy_seconds({"api_ms": 2500, "parse_seconds": 7})
    assert event["api_seconds"] == 2.5 and event["api_elapsed_seconds"] == 2.5
    assert event["parse_seconds"] == 7


def test_alias_table_is_well_formed():
    legacy = [name for names in LEGACY_SECONDS_ALIASES.values() for name in names]
    assert len(legacy) == len(set(legacy))
    assert all(key.endswith("_ms") for key in LEGACY_SECONDS_ALIASES)
    assert all(name.endswith("_seconds") for name in legacy)


def test_format_duration_reads_naturally():
    from translation_tool.core.batch_timing import format_duration, format_duration_ms

    assert format_duration_ms(30000) == "30s"
    assert format_duration_ms(1500) == "1.5s"
    assert format_duration_ms(850) == "850ms"
    assert format_duration_ms(0) == "0ms"
    assert format_duration(95) == "1m35s"
    assert format_duration(3725) == "1h02m05s"
    assert format_duration(None) == "--"
    assert format_duration_ms("?") == "--"


def test_batch_event_text_shows_seconds_not_raw_ms():
    from translation_tool.core.common_batch_executor import format_batch_event

    text = format_batch_event(
        {"kind": "retry_wait", "attempt": 1, "retry_number": 1, "wait_ms": 30000}
    )
    assert "30s" in text and "30000" not in text
