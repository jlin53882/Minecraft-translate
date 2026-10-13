"""pack.mcmeta 內容規則：整數 pack_format、supported_formats、預設與自訂值。"""

from __future__ import annotations

import json
import zipfile

import pytest

from translation_tool.core.output_bundler import bundle_outputs_generator
from translation_tool.core.pack_mcmeta import (
    build_pack_mcmeta,
    describe_pack_format,
    parse_pack_format,
)


def test_1_21_1_template_matches_spec():
    assert build_pack_mcmeta("說明", 34) == {
        "pack": {
            "pack_format": 34,
            "description": "說明",
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 46},
        }
    }


@pytest.mark.parametrize("pack_format", [1, 15, 17])
def test_before_1_20_2_has_single_number_only(pack_format):
    assert build_pack_mcmeta("d", pack_format) == {
        "pack": {"pack_format": pack_format, "description": "d"}
    }


@pytest.mark.parametrize("pack_format", [18, 22, 32, 34, 42, 46])
def test_supported_formats_are_integers_since_1_20_2(pack_format):
    supported = build_pack_mcmeta("d", pack_format)["pack"]["supported_formats"]
    assert supported["min_inclusive"] == pack_format
    assert supported["max_inclusive"] == 46
    assert all(type(v) is int for v in supported.values())


@pytest.mark.parametrize("pack_format", [55, 63, 64])
def test_1_21_5_to_1_21_8_range_ends_at_last_supported_formats_version(pack_format):
    supported = build_pack_mcmeta("d", pack_format)["pack"]["supported_formats"]
    assert supported == {"min_inclusive": pack_format, "max_inclusive": 64}


@pytest.mark.parametrize("pack_format", [65, 69, 75, 84])
def test_1_21_9_and_later_use_min_max_format_without_pack_format(pack_format):
    pack = build_pack_mcmeta("d", pack_format)["pack"]
    assert pack == {
        "description": "d",
        "min_format": pack_format,
        "max_format": 9999,
    }
    assert type(pack["min_format"]) is int and type(pack["max_format"]) is int


@pytest.mark.parametrize(
    ("text", "expected"), [("", None), ("  ", None), (None, None), (" 34 ", 34)]
)
def test_parse_pack_format_accepts_blank_and_integers(text, expected):
    assert parse_pack_format(text) == expected


@pytest.mark.parametrize("text", ["abc", "3.4", "-1", "0", "10000", "３４"])
def test_parse_pack_format_rejects_non_positive_integers(text):
    with pytest.raises(ValueError):
        parse_pack_format(text)


def test_describe_pack_format():
    assert describe_pack_format(34) == "pack_format 34，supported_formats 34–46"
    assert describe_pack_format(15) == "pack_format 15"
    assert describe_pack_format(69) == "min_format 69，max_format 9999"


def test_bundle_without_description_or_version_still_writes_valid_pack_mcmeta(
    tmp_path,
):
    (tmp_path / "lang").mkdir()
    (tmp_path / "lang" / "zh_tw.json").write_text('{"k": "v"}', encoding="utf-8")
    out = tmp_path / "out.zip"
    list(bundle_outputs_generator(str(tmp_path), str(out)))
    with zipfile.ZipFile(out) as zf:
        data = json.loads(zf.read("pack.mcmeta").decode("utf-8"))
    assert data["pack"]["pack_format"] == 34
    assert data["pack"]["supported_formats"] == {
        "min_inclusive": 34,
        "max_inclusive": 46,
    }


def test_user_max_replaces_default_max_inclusive():
    assert build_pack_mcmeta("d", 34, 999) == {
        "pack": {
            "pack_format": 34,
            "description": "d",
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 999},
        }
    }
    assert build_pack_mcmeta("d", 34, 42)["pack"]["supported_formats"] == {
        "min_inclusive": 34,
        "max_inclusive": 42,
    }


def test_user_max_equal_to_pack_format_is_allowed():
    supported = build_pack_mcmeta("d", 34, 34)["pack"]["supported_formats"]
    assert supported == {"min_inclusive": 34, "max_inclusive": 34}


def test_user_max_below_pack_format_is_rejected():
    with pytest.raises(ValueError, match="不能小於"):
        build_pack_mcmeta("d", 34, 33)


def test_user_max_replaces_max_format_since_1_21_9():
    pack = build_pack_mcmeta("d", 69, 120)["pack"]
    assert pack == {"description": "d", "min_format": 69, "max_format": 120}


def test_user_max_is_ignored_before_1_20_2():
    assert build_pack_mcmeta("d", 15, 999) == build_pack_mcmeta("d", 15)


def test_describe_with_user_max():
    assert describe_pack_format(34, 999) == "pack_format 34，supported_formats 34–999"


def test_bundle_writes_user_max(tmp_path):
    (tmp_path / "lang").mkdir()
    (tmp_path / "lang" / "zh_tw.json").write_text('{"k": "v"}', encoding="utf-8")
    out = tmp_path / "out.zip"
    list(
        bundle_outputs_generator(
            str(tmp_path), str(out), pack_format=34, max_pack_format=999
        )
    )
    with zipfile.ZipFile(out) as zf:
        data = json.loads(zf.read("pack.mcmeta").decode("utf-8"))
    assert data["pack"]["supported_formats"]["max_inclusive"] == 999
    assert type(data["pack"]["supported_formats"]["max_inclusive"]) is int


def test_changing_only_max_rebuilds_zip(tmp_path):
    (tmp_path / "lang").mkdir()
    (tmp_path / "lang" / "zh_tw.json").write_text('{"k": "v"}', encoding="utf-8")
    out = tmp_path / "out.zip"

    def run(max_pack_format):
        return list(
            bundle_outputs_generator(
                str(tmp_path), str(out), pack_format=34, max_pack_format=max_pack_format
            )
        )

    run(None)
    again = run(None)
    assert any("沿用" in u.get("log", "") for u in again)
    changed = run(100)
    assert not any("沿用" in u.get("log", "") for u in changed)
