"""版本輸入換算：1.20.4 這種區間中間的版本、整數、空白與無效輸入。"""

from __future__ import annotations

import itertools
import json

import pytest

from translation_tool.core import pack_format_lookup as lookup
from translation_tool.core.pack_format_lookup import (
    QUICK_VERSIONS,
    explain_version_input,
    lookup_version,
    parse_version,
    resolve_version_input,
)
from translation_tool.core.pack_mcmeta import DEFAULT_PACK_FORMAT


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.21.1", 34),
        ("1.21", 34),
        ("1.20.4", 22),  # 區間 1.20.3～1.20.4 的中間值
        ("1.20.3", 22),
        ("1.20.2", 18),
        ("1.20.6", 32),
        ("1.19.4", 13),
        ("1.19.2", 9),
        ("1.16.5", 6),
        ("1.9", 2),  # 不能被 1.19 誤判
        ("1.19", 9),
        ("1.8.9", 1),
        ("1.21.8", 64),
        ("1.21.10", 69),
        ("1.21.11", 75),
        ("26.1", 84),
        ("26.1.2", 84),
    ],
)
def test_version_maps_to_pack_format(version, expected):
    assert lookup_version(version).pack_format == expected


def test_versions_compare_numerically_not_as_strings():
    assert parse_version("1.9") < parse_version("1.19") < parse_version("1.21.1")
    assert parse_version("1.21.10") > parse_version("1.21.9")


def test_every_table_row_is_ordered_and_contiguous_enough():
    rows = lookup.load_table()
    assert rows
    for prev, cur in itertools.pairwise(rows):
        assert prev.low <= prev.high < cur.low
        assert prev.pack_format < cur.pack_format
    assert all(v for v in QUICK_VERSIONS if lookup_version(v))


@pytest.mark.parametrize("text", ["abc", "1.", "1..2", "1.2.3.4", "v1.21", "1.２1"])
def test_invalid_version_text_is_rejected(text):
    with pytest.raises(ValueError):
        resolve_version_input(text)


def test_version_newer_than_table_suggests_pack_format():
    with pytest.raises(ValueError, match="pack_format"):
        lookup_version("27.1")


def test_version_in_a_gap_or_older_than_table_is_rejected():
    with pytest.raises(ValueError):
        lookup_version("1.5.2")
    with pytest.raises(ValueError):
        lookup_version("1.21.12")


def test_blank_uses_default_and_integer_is_pack_format():
    assert resolve_version_input("").pack_format == DEFAULT_PACK_FORMAT == 34
    assert resolve_version_input(None).pack_format == 34
    assert resolve_version_input(" 42 ").pack_format == 42
    with pytest.raises(ValueError):
        resolve_version_input("0")
    with pytest.raises(ValueError):
        resolve_version_input("10000")


def test_explain_reports_written_fields_or_error():
    pack_format, message, valid = explain_version_input("1.20.4")
    assert (pack_format, valid) == (22, True)
    assert "1.20.3～1.20.4" in message and "supported_formats 22–46" in message
    pack_format, message, valid = explain_version_input("69")
    assert (pack_format, valid) == (69, True) and "min_format 69" in message
    pack_format, message, valid = explain_version_input("oops")
    assert pack_format is None and valid is False and message


def test_missing_or_broken_table_falls_back_to_empty(tmp_path, monkeypatch):
    broken = tmp_path / "table.json"
    broken.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(lookup, "TABLE_FILE", broken)
    lookup.load_table.cache_clear()
    try:
        assert lookup.load_table() == ()
        with pytest.raises(ValueError, match="pack_format"):
            lookup_version("1.21.1")
        assert resolve_version_input("34").pack_format == 34
    finally:
        lookup.load_table.cache_clear()


def test_table_file_is_valid_json_rows():
    rows = json.loads(lookup.TABLE_FILE.read_text(encoding="utf-8"))
    assert all(type(r["pack_format"]) is int for r in rows)


def test_max_input_blank_integer_version_and_invalid():
    from translation_tool.core.pack_format_lookup import resolve_max_input

    assert resolve_max_input("") is None
    assert resolve_max_input(None) is None
    assert resolve_max_input(" 999 ") == 999
    assert resolve_max_input("1.21.4") == 46
    with pytest.raises(ValueError):
        resolve_max_input("abc")
    with pytest.raises(ValueError):
        resolve_max_input("10000")


def test_explain_with_user_max_and_invalid_max():
    pack_format, message, valid = explain_version_input("1.21.1", "999")
    assert (pack_format, valid) == (34, True)
    assert "supported_formats 34–999" in message
    pack_format, message, valid = explain_version_input("1.21.1", "10")
    assert pack_format is None and valid is False and "不能小於" in message
    _pf, message, valid = explain_version_input("1.19.2", "999")
    assert valid and "最大版本不會寫入" in message
