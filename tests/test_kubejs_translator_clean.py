"""Focused tests for KubeJS cleanup and identity-based translation decisions."""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from translation_tool.core.kubejs_translator_clean import (
    _shielded_convert,
    clean_kubejs_from_raw_impl,
    deep_merge_3way_flat_impl,
    is_filled_text_impl,
    prune_en_by_tw_flat_impl,
)


def _convert(text: str) -> str:
    return text


def test_is_filled_text_rejects_empty_and_lang_references() -> None:
    assert not is_filled_text_impl("")
    assert not is_filled_text_impl("  \t")
    assert not is_filled_text_impl("{some.key}")
    assert not is_filled_text_impl(None)
    assert is_filled_text_impl("Hello world")


def test_three_way_merge_uses_tw_then_converted_cn_then_english() -> None:
    def convert(text: str) -> str:
        return f"converted:{text}"

    result = deep_merge_3way_flat_impl(
        {"tw": "TW"},
        {"tw": "CN", "cn": "CN"},
        {"tw": "English", "cn": "English"},
        safe_convert_text_fn=convert,
    )
    assert result == {"tw": "TW", "cn": "converted:CN"}


def test_prune_uses_exact_key_and_keeps_same_as_english() -> None:
    english = {"same": "Energy", "translated": "Book", "other": "Energy"}
    available = {"same": "Energy", "translated": "書"}
    assert prune_en_by_tw_flat_impl(english, available) == {
        "same": "Energy",
        "other": "Energy",
    }


def test_shielded_convert_calls_converter_when_no_special_tokens() -> None:
    assert _shielded_convert("plain", _convert) == "plain"


def test_clean_merges_existing_effective_tw_and_removes_stale_pending(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    lang = raw / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_bytes(orjson.dumps({"a": "Apple", "b": "Book"}))
    (lang / "zh_tw.json").write_bytes(orjson.dumps({"a": "apple-tw"}))
    final = tmp_path / "old-final" / "assets" / "demo" / "lang"
    final.mkdir(parents=True)
    (final / "zh_tw.json").write_bytes(orjson.dumps({"b": "book-tw", "removed": "old"}))

    def read_json(path: Path | None) -> dict:
        if path is None or not path.is_file():
            return {}
        return orjson.loads(path.read_bytes())

    def write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))

    pending = tmp_path / "pending"
    current_final = tmp_path / "final"
    result = clean_kubejs_from_raw_impl(
        str(tmp_path),
        raw_dir=str(raw),
        pending_root=str(pending),
        final_root=str(current_final),
        previous_final_root=str(tmp_path / "old-final"),
        read_json_dict_fn=read_json,
        write_json_fn=write_json,
        safe_convert_text_fn=_convert,
        log_debug_fn=lambda *args: None,
        log_info_fn=lambda *args: None,
    )

    assert result["previous_final_keys_preserved"] == 1
    assert not (pending / "assets/demo/lang/en_us.json").exists()
    assert read_json(current_final / "assets/demo/lang/zh_tw.json") == {
        "a": "apple-tw",
        "b": "book-tw",
    }


@pytest.fixture
def clean_args(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    pending = tmp_path / "pending"
    final = tmp_path / "final"

    def read_json(path: Path | None) -> dict:
        if path is None or not path.is_file():
            return {}
        try:
            return orjson.loads(path.read_bytes())
        except orjson.JSONDecodeError:
            return {}

    def write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))

    kwargs = {
        "base_dir": str(tmp_path),
        "raw_dir": str(raw),
        "pending_root": str(pending),
        "final_root": str(final),
        "read_json_dict_fn": read_json,
        "write_json_fn": write_json,
        "safe_convert_text_fn": _convert,
        "log_debug_fn": lambda *args: None,
        "log_info_fn": lambda *args: None,
    }
    return raw, pending, final, kwargs


def test_client_script_key_is_not_suppressed_by_matching_lang_key(clean_args) -> None:
    raw, pending, _, kwargs = clean_args
    scripts = raw / "client_scripts"
    lang = raw / "assets/demo/lang"
    scripts.mkdir(parents=True)
    lang.mkdir(parents=True)
    (scripts / "tooltips.json").write_bytes(
        orjson.dumps({"tooltips.js|minecraft:dirt.tooltip.0": "Dirt"})
    )
    (lang / "zh_tw.json").write_bytes(orjson.dumps({"minecraft:dirt": "泥土"}))

    clean_kubejs_from_raw_impl(**kwargs)

    script_pending = pending / "client_scripts/tooltips.json"
    assert orjson.loads(script_pending.read_bytes()) == {
        "tooltips.js|minecraft:dirt.tooltip.0": "Dirt"
    }


def test_clean_removes_stale_pending_but_preserves_user_edited_file(clean_args) -> None:
    raw, pending, _, kwargs = clean_args
    scripts = raw / "client_scripts"
    scripts.mkdir(parents=True)
    raw_script = scripts / "tooltip.json"
    raw_script.write_bytes(orjson.dumps({"tooltip.js|scene.1": "Text"}))

    clean_kubejs_from_raw_impl(**kwargs)
    output = pending / "client_scripts/tooltip.json"
    output.write_text('{"manual": "keep me"}', encoding="utf-8")
    raw_script.write_bytes(orjson.dumps({}))

    result = clean_kubejs_from_raw_impl(**kwargs)

    assert output.read_text(encoding="utf-8") == '{"manual": "keep me"}'
    assert result["write_conflicts"] >= 1
