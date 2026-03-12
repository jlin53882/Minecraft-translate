from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.plugins.shared.json_io import read_json_dict, write_json_dict
from translation_tool.plugins.shared.lang_path_rules import (
    compute_output_path,
    is_lang_code_segment,
)


def test_compute_output_path_renames_lang_folder_and_filename() -> None:
    in_dir = Path("/project/in")
    out_dir = Path("/project/out")
    src = in_dir / "quests" / "lang" / "en_us" / "en_us.json"

    result = compute_output_path(src, in_dir, out_dir, {"en_us", "ru_ru"})

    assert result == out_dir / "quests" / "lang" / "zh_tw" / "zh_tw.json"


def test_compute_output_path_keeps_filename_when_not_lang_code_file() -> None:
    in_dir = Path("/project/in")
    out_dir = Path("/project/out")
    src = in_dir / "quests" / "lang" / "ja_jp" / "custom_map.json"

    result = compute_output_path(src, in_dir, out_dir, {"en_us", "ru_ru"})

    assert result == out_dir / "quests" / "lang" / "zh_tw" / "custom_map.json"


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("en_us", True),
        ("zh_tw", True),
        ("EN_US", True),
        ("en-us", False),
        ("enu_s", False),
        ("assets", False),
    ],
)
def test_is_lang_code_segment_samples(segment: str, expected: bool) -> None:
    assert is_lang_code_segment(segment) is expected


def test_read_write_json_dict_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "sample.json"
    payload = {"k1": "v1", "k2": "測試"}

    write_json_dict(target, payload)
    loaded = read_json_dict(target)

    assert loaded == payload



def test_read_json_dict_raises_when_root_is_not_dict(tmp_path: Path) -> None:
    target = tmp_path / "list.json"
    target.write_text(json.dumps(["a", "b"]), encoding="utf-8")

    with pytest.raises(ValueError, match="object/dict"):
        read_json_dict(target)
