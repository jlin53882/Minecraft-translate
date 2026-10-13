"""pack.mcmeta 內容規則：整數 pack_format、supported_formats、預設與自訂值。"""

from __future__ import annotations

import json
import zipfile

import pytest

from translation_tool.core.output_bundler import (
    BUNDLE_STATE_VERSION,
    bundle_outputs_generator,
)
from translation_tool.core.pack_mcmeta import (
    build_pack_mcmeta,
    describe_pack_format,
    parse_pack_format,
    repair_source_pack_mcmeta,
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


def test_user_max_across_1_21_9_boundary_writes_hybrid_metadata():
    assert build_pack_mcmeta("d", 34, 999) == {
        "pack": {
            "pack_format": 34,
            "description": "d",
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 999},
            "min_format": 34,
            "max_format": 999,
        }
    }


def test_user_max_below_boundary_has_no_min_max_format():
    pack = build_pack_mcmeta("d", 34, 64)["pack"]
    assert "min_format" not in pack and "max_format" not in pack
    assert pack["supported_formats"]["max_inclusive"] == 64
    assert "min_format" in build_pack_mcmeta("d", 34, 65)["pack"]
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
    assert describe_pack_format(34, 60) == "pack_format 34，supported_formats 34–60"
    assert describe_pack_format(34, 999) == (
        "pack_format 34，supported_formats 34–999，min_format 34，max_format 999"
    )


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


GENERATED = build_pack_mcmeta("UI 說明", 34)


def _repair(pack: object) -> tuple[dict | None, list[str]]:
    content, notes = repair_source_pack_mcmeta(
        json.dumps({"pack": pack}, ensure_ascii=False), GENERATED
    )
    return (None if content is None else json.loads(content)["pack"]), notes


def test_repair_keeps_valid_source_byte_for_byte():
    text = json.dumps(build_pack_mcmeta("d", 34), indent=4)
    assert repair_source_pack_mcmeta(text, GENERATED) == (text, [])


@pytest.mark.parametrize(
    "pack",
    [
        {"description": "d", "min_format": [69, 0], "max_format": 75},
        {"description": "d", "pack_format": 15},
        {
            "description": "d",
            "pack_format": 34,
            "supported_formats": [34, 46],
        },
        {"description": {"text": "x"}, "pack_format": 34, "supported_formats": 34},
    ],
)
def test_repair_accepts_all_valid_forms_unchanged(pack):
    fixed, notes = _repair(pack)
    assert fixed == pack and notes == []


def test_repair_turns_quoted_numbers_into_integers_and_keeps_other_keys():
    fixed, notes = _repair(
        {
            "description": "mine",
            "pack_format": "34",
            "supported_formats": {"min_inclusive": "34", "max_inclusive": "46"},
            "overlays": {"entries": [{"formats": [1, 2], "directory": "o"}]},
        }
    )
    assert fixed["pack_format"] == 34 and type(fixed["pack_format"]) is int
    assert fixed["supported_formats"] == {"min_inclusive": 34, "max_inclusive": 46}
    assert fixed["overlays"] == {"entries": [{"formats": [1, 2], "directory": "o"}]}
    assert fixed["description"] == "mine" and notes


def test_repair_fixes_min_max_format_strings():
    fixed, _ = _repair(
        {"description": "d", "min_format": "69", "max_format": ["75", "0"]}
    )
    assert (fixed["min_format"], fixed["max_format"]) == (69, [75, 0])


@pytest.mark.parametrize("bad", ["abc", "3.4", True, None, -1, [], {}, [1, "x"]])
def test_repair_drops_unusable_pack_format_and_fills_version_from_ui(bad):
    fixed, notes = _repair({"description": "d", "pack_format": bad})
    assert fixed["pack_format"] == 34
    assert fixed["supported_formats"] == GENERATED["pack"]["supported_formats"]
    assert any("無法使用" in n for n in notes) or any("補上" in n for n in notes)


def test_repair_drops_reversed_supported_formats_range():
    fixed, notes = _repair(
        {
            "description": "d",
            "pack_format": 34,
            "supported_formats": {"min_inclusive": 46, "max_inclusive": 34},
        }
    )
    assert "supported_formats" not in fixed and fixed["pack_format"] == 34
    assert any("supported_formats" in n for n in notes)


def test_repair_fills_version_fields_when_pack_is_empty():
    fixed, notes = _repair({})
    assert fixed == {
        "description": "UI 說明",
        "pack_format": 34,
        "supported_formats": {"min_inclusive": 34, "max_inclusive": 46},
    }
    assert len(notes) == 2


def test_repair_requires_both_min_and_max_format():
    fixed, _ = _repair({"description": "d", "min_format": 69})
    assert fixed["pack_format"] == 34 and "min_format" not in fixed


def test_repair_fills_missing_description():
    fixed, notes = _repair({"pack_format": 34})
    assert fixed["description"] == "UI 說明" and any("description" in n for n in notes)


@pytest.mark.parametrize("text", ["{not json", "[]", '{"pack": 1}', "{}"])
def test_repair_rejects_unusable_files(text):
    content, notes = repair_source_pack_mcmeta(text, GENERATED)
    assert content is None and len(notes) == 1


def _bundle(tmp_path, **kwargs):
    out = tmp_path / "out.zip"
    updates = list(bundle_outputs_generator(str(tmp_path / "src"), str(out), **kwargs))
    with zipfile.ZipFile(out) as zf:
        return json.loads(zf.read("pack.mcmeta").decode("utf-8")), updates


@pytest.fixture
def src_dir(tmp_path):
    src = tmp_path / "src"
    (src / "lang").mkdir(parents=True)
    (src / "lang" / "zh_tw.json").write_text('{"k": "v"}', encoding="utf-8")
    return src


def test_valid_source_pack_mcmeta_is_kept_unchanged(tmp_path, src_dir):
    custom = '{"pack":{"description":"mine","pack_format":15}}'
    (src_dir / "pack.mcmeta").write_text(custom, encoding="utf-8")
    data, updates = _bundle(tmp_path, pack_format=34)
    assert data == json.loads(custom)
    assert not any("已修正" in u.get("log", "") for u in updates)


def test_source_pack_mcmeta_with_quoted_numbers_is_repaired_not_copied(
    tmp_path, src_dir
):
    custom = '{"pack":{"description":"mine","pack_format":"34","filter":{"block":[]}}}'
    (src_dir / "pack.mcmeta").write_text(custom, encoding="utf-8")
    data, updates = _bundle(tmp_path, pack_format=34)
    assert data["pack"]["pack_format"] == 34
    assert type(data["pack"]["pack_format"]) is int
    assert data["pack"]["filter"] == {"block": []}  # 其他自訂欄位保留
    assert any("已修正" in u.get("log", "") for u in updates)


def test_source_pack_mcmeta_without_version_gets_ui_version(tmp_path, src_dir):
    (src_dir / "pack.mcmeta").write_text('{"pack": {}}', encoding="utf-8")
    data, _ = _bundle(tmp_path, description="UI", pack_format=34)
    assert data["pack"]["pack_format"] == 34 and data["pack"]["description"] == "UI"


@pytest.mark.parametrize("content", ["{not json", "[]", "{}"])
def test_unusable_source_pack_mcmeta_falls_back_to_generated(
    tmp_path, src_dir, content
):
    (src_dir / "pack.mcmeta").write_text(content, encoding="utf-8")
    data, updates = _bundle(tmp_path, pack_format=34)
    assert data["pack"]["pack_format"] == 34
    assert any("無法使用" in u.get("log", "") for u in updates)


def test_undecodable_source_pack_mcmeta_falls_back_to_generated(tmp_path, src_dir):
    (src_dir / "pack.mcmeta").write_bytes(b"\xff\xfe\x00bad")
    data, _ = _bundle(tmp_path, pack_format=34)
    assert data["pack"]["supported_formats"]["min_inclusive"] == 34


def test_old_state_sidecar_forces_one_rebuild_then_reuses(tmp_path, src_dir):
    out = tmp_path / "out.zip"

    def run():
        return list(bundle_outputs_generator(str(src_dir), str(out), pack_format=34))

    run()
    state_file = tmp_path / "out.zip.bundle-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["version"] == BUNDLE_STATE_VERSION and BUNDLE_STATE_VERSION >= 2
    state["version"] = 1
    state_file.write_text(json.dumps(state), encoding="utf-8")
    assert not any("沿用" in u.get("log", "") for u in run())
    assert any("沿用" in u.get("log", "") for u in run())
