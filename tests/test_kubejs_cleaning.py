from __future__ import annotations

from pathlib import Path

import orjson

from translation_tool.core import kubejs_translator


def test_clean_kubejs_from_raw_splits_pending_and_final_outputs(tmp_path: Path) -> None:
    raw_root = tmp_path / "Output" / "kubejs" / "raw" / "kubejs"
    lang_root = raw_root / "assets" / "demo" / "lang"
    tooltip_root = raw_root / "client_scripts" / "tooltip"
    lang_root.mkdir(parents=True)
    tooltip_root.mkdir(parents=True)

    (lang_root / "en_us.json").write_bytes(orjson.dumps({"a": "A", "b": "B"}))
    (lang_root / "zh_cn.json").write_bytes(orjson.dumps({"a": "简中A"}))
    (tooltip_root / "tip.json").write_bytes(orjson.dumps({"tip": "Only English"}))

    result = kubejs_translator.clean_kubejs_from_raw(
        str(tmp_path),
        output_dir=str(tmp_path / "Output"),
    )

    pending_root = Path(result["pending_root"])
    final_root = Path(result["final_root"])

    assert result["groups"] == 1
    assert result["pending_lang_written"] == 1
    assert result["merged_lang_written"] == 1
    assert result["copied_other_jsons"] == 1
    assert orjson.loads((pending_root / "assets" / "demo" / "lang" / "en_us.json").read_bytes()) == {"b": "B"}
    assert orjson.loads((final_root / "assets" / "demo" / "lang" / "zh_tw.json").read_bytes()) == {"a": "簡中A"}
    assert (pending_root / "client_scripts" / "tooltip" / "tip.json").exists()
