from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from translation_tool.core import lang_merger


def test_collapse_lang_lines_merges_backslash_continuations() -> None:
    text = "key1=Hello\\\n world\nkey2=Done"

    lines = lang_merger.collapse_lang_lines(text)

    assert lines == ["key1=Helloworld", "key2=Done"]


def test_parse_and_dump_lang_text_round_trip() -> None:
    raw = "alpha=一號\nbeta=二號"

    parsed = lang_merger.parse_lang_text(raw)
    dumped = lang_merger.dump_lang_text(parsed)

    assert parsed == {"alpha": "一號", "beta": "二號"}
    assert lang_merger.parse_lang_text(dumped) == parsed


def test_parse_lang_text_appends_missing_equals_line_to_previous_key() -> None:
    raw = "title=第一行\n第二行續寫\nfooter=結尾"

    parsed = lang_merger.parse_lang_text(raw)

    assert parsed["title"] == "第一行\n第二行續寫"
    assert parsed["footer"] == "結尾"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("assets/mod/lang/en_us.lang", True),
        ("assets/mod/lang/zh_cn.lang", True),
        ("assets/mod/patchouli_books/en_us/entry.lang", False),
        ("data/mod/lang/en_us.lang", True),
        ("assets/mod/lang/en_us.json", False),
    ],
)
def test_is_mc_standard_lang_path_samples(path: str, expected: bool) -> None:
    assert lang_merger.is_mc_standard_lang_path(path) is expected


def test_export_filtered_pending_keeps_only_threshold_files_and_cleans_output(tmp_path: Path) -> None:
    pending_root = tmp_path / "pending"
    output_root = tmp_path / "filtered"

    keep_file = pending_root / "a" / "keep.json"
    skip_file = pending_root / "b" / "skip.json"
    stale_file = output_root / "old" / "stale.json"

    keep_file.parent.mkdir(parents=True, exist_ok=True)
    skip_file.parent.mkdir(parents=True, exist_ok=True)
    stale_file.parent.mkdir(parents=True, exist_ok=True)

    keep_file.write_bytes(orjson.dumps([{"k": 1}, {"k": 2}], option=orjson.OPT_INDENT_2))
    skip_file.write_bytes(orjson.dumps([{"k": 1}], option=orjson.OPT_INDENT_2))
    stale_file.write_text("stale", encoding="utf-8")

    lang_merger.export_filtered_pending(str(pending_root), str(output_root), min_count=2)

    assert not stale_file.exists()
    assert (output_root / "a" / "keep.json").exists()
    assert not (output_root / "b" / "skip.json").exists()
