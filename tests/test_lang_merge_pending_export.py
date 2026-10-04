from __future__ import annotations

from pathlib import Path

import orjson

from translation_tool.core import lang_merge_content


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

    lang_merge_content.export_filtered_pending(str(pending_root), str(output_root), min_count=2)

    assert not stale_file.exists()
    assert (output_root / "a" / "keep.json").exists()
    assert not (output_root / "b" / "skip.json").exists()
