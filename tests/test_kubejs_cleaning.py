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
    (lang_root / "zh_cn.json").write_bytes(orjson.dumps({"a": "簡中A"}))
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
    assert orjson.loads(
        (pending_root / "assets" / "demo" / "lang" / "en_us.json").read_bytes()
    ) == {"b": "B"}
    assert orjson.loads(
        (final_root / "assets" / "demo" / "lang" / "zh_tw.json").read_bytes()
    ) == {"a": "簡中A"}
    assert (pending_root / "client_scripts" / "tooltip" / "tip.json").exists()


def test_clean_rerun_removes_stale_pending_lang_file(tmp_path: Path) -> None:
    raw = tmp_path / "Output" / "kubejs" / "raw" / "kubejs"
    lang = raw / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_bytes(orjson.dumps({"a": "Apple"}))

    first = kubejs_translator.clean_kubejs_from_raw(
        str(tmp_path), output_dir=str(tmp_path / "Output")
    )
    pending = Path(first["pending_root"]) / "assets" / "demo" / "lang" / "en_us.json"
    assert orjson.loads(pending.read_bytes()) == {"a": "Apple"}

    (lang / "zh_tw.json").write_bytes(orjson.dumps({"a": "蘋果"}))
    kubejs_translator.clean_kubejs_from_raw(
        str(tmp_path), output_dir=str(tmp_path / "Output")
    )

    assert not pending.exists()


def test_clean_rerun_removes_stale_client_script_pending_file(tmp_path: Path) -> None:
    raw = tmp_path / "Output" / "kubejs" / "raw" / "kubejs"
    lang = raw / "assets" / "demo" / "lang"
    scripts = raw / "client_scripts" / "tooltip"
    lang.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (scripts / "tip.json").write_bytes(
        orjson.dumps({"tooltips.js|minecraft:dirt.tooltip.0": "Dirt"})
    )

    first = kubejs_translator.clean_kubejs_from_raw(
        str(tmp_path), output_dir=str(tmp_path / "Output")
    )
    pending = Path(first["pending_root"]) / "client_scripts" / "tooltip" / "tip.json"
    assert pending.exists()

    (scripts / "tip.json").write_bytes(orjson.dumps({}))
    kubejs_translator.clean_kubejs_from_raw(
        str(tmp_path), output_dir=str(tmp_path / "Output")
    )

    assert not pending.exists()


def test_clean_does_not_deduplicate_pending_by_matching_text_value(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "Output" / "kubejs" / "raw" / "kubejs"
    lang = raw / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_bytes(orjson.dumps({"new_key": "Energy"}))
    final = (
        tmp_path / "Output" / "kubejs" / "完成" / "kubejs" / "assets" / "other" / "lang"
    )
    final.mkdir(parents=True)
    (final / "zh_tw.json").write_bytes(orjson.dumps({"old_key": "Energy"}))

    result = kubejs_translator.clean_kubejs_from_raw(
        str(tmp_path), output_dir=str(tmp_path / "Output")
    )

    pending = Path(result["pending_root"]) / "assets" / "demo" / "lang" / "en_us.json"
    assert orjson.loads(pending.read_bytes()) == {"new_key": "Energy"}


def test_client_tooltip_is_not_deduplicated_by_matching_lang_item_id(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "Output" / "kubejs" / "raw" / "kubejs"
    scripts = raw / "client_scripts"
    lang = raw / "assets" / "demo" / "lang"
    scripts.mkdir(parents=True)
    lang.mkdir(parents=True)
    (scripts / "tooltips.json").write_bytes(
        orjson.dumps({"tooltips.js|minecraft:dirt.tooltip.0": "Dirt"})
    )
    (lang / "zh_tw.json").write_bytes(orjson.dumps({"minecraft:dirt": "泥土"}))

    result = kubejs_translator.clean_kubejs_from_raw(
        str(tmp_path), output_dir=str(tmp_path / "Output")
    )

    pending = Path(result["pending_root"]) / "client_scripts" / "tooltips.json"
    assert orjson.loads(pending.read_bytes()) == {
        "tooltips.js|minecraft:dirt.tooltip.0": "Dirt"
    }
