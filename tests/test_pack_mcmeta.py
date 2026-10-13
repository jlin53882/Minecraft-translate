"""pack.mcmeta 內容規則：整數 pack_format、supported_formats、預設與自訂值。"""

from __future__ import annotations

import json
import zipfile

import pytest

from translation_tool.core.output_bundler import (
    BUNDLE_STATE_VERSION,
    bundle_outputs_generator,
    preview_pack_mcmeta,
)
from translation_tool.core.pack_mcmeta import (
    build_pack_mcmeta,
    describe_pack_format,
    pack_description_text,
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


def test_preview_matches_what_the_bundler_writes(tmp_path, src_dir):
    from translation_tool.core.output_bundler import preview_pack_mcmeta

    cases = [
        None,
        '{"pack":{"description":"mine","pack_format":15}}',
        '{"pack":{"description":"mine","pack_format":"34","filter":{"block":[]}}}',
        '{"pack": {}}',
        "{not json",
    ]
    for index, custom in enumerate(cases):
        work = tmp_path / f"case{index}"
        work.mkdir()
        (work / "lang").mkdir()
        (work / "lang" / "zh_tw.json").write_text('{"k": "v"}', encoding="utf-8")
        if custom is not None:
            (work / "pack.mcmeta").write_text(custom, encoding="utf-8")
        text, source, _notes, used = preview_pack_mcmeta(
            str(work), None, "UI", 34, None
        )
        out = work / "out.zip"
        list(
            bundle_outputs_generator(
                str(work), str(out), description="UI", pack_format=34
            )
        )
        with zipfile.ZipFile(out) as zf:
            assert json.loads(zf.read("pack.mcmeta")) == json.loads(text)
        assert (source is None) == (custom is None)
        assert used == (custom is not None and custom != "{not json")


def test_repair_drops_min_max_format_when_max_below_min():
    fixed, notes = _repair({"description": "d", "min_format": 75, "max_format": 69})
    assert "min_format" not in fixed and "max_format" not in fixed
    assert fixed["pack_format"] == 34 and any("max_format" in n for n in notes)


def test_repair_compares_list_form_min_max_format():
    fixed, _ = _repair(
        {
            "description": "d",
            "pack_format": 34,
            "min_format": [75, 1],
            "max_format": [75, 0],
        }
    )
    assert "min_format" not in fixed


def test_repair_resets_supported_formats_that_exclude_pack_format():
    fixed, notes = _repair(
        {
            "description": "d",
            "pack_format": 34,
            "supported_formats": {"min_inclusive": 46, "max_inclusive": 64},
        }
    )
    assert fixed["pack_format"] == 34
    assert fixed["supported_formats"] == {"min_inclusive": 34, "max_inclusive": 46}
    assert any("不包含 pack_format" in n for n in notes)


@pytest.mark.parametrize(
    "supported", [34, [30, 40], {"min_inclusive": 34, "max_inclusive": 34}]
)
def test_repair_keeps_supported_formats_containing_pack_format(supported):
    pack = {"description": "d", "pack_format": 34, "supported_formats": supported}
    fixed, notes = _repair(pack)
    assert fixed == pack and notes == []


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("plain", "plain"),
        ({"text": "a", "extra": [{"text": "b"}]}, "ab"),
        (["x", {"text": "y"}], "xy"),
    ],
)
def test_pack_description_text_flattens_text_components(description, expected):
    text = json.dumps({"pack": {"description": description}})
    assert pack_description_text(text) == expected


def test_pack_description_text_returns_none_when_unreadable():
    assert pack_description_text("版本或 pack_format 無效") is None


def test_source_pack_files_are_not_added_again_as_numbered_copies(tmp_path, src_dir):
    (src_dir / "pack.mcmeta").write_text(
        '{"pack":{"description":"d","pack_format":"34"}}', encoding="utf-8"
    )
    (src_dir / "pack.png").write_bytes(b"\x89PNG")
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "pack.mcmeta").write_text('{"bad', encoding="utf-8")
    out = tmp_path / "out.zip"
    list(
        bundle_outputs_generator(
            str(src_dir),
            str(out),
            description="d",
            pack_format=34,
            extra_folders=[str(extra / "pack.mcmeta")],
        )
    )
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert not [n for n in names if n.startswith("pack_")]
    assert names.count("pack.mcmeta") == 1 and names.count("pack.png") == 1


@pytest.mark.parametrize(
    ("low", "high"),
    [
        ([75, 1], 75),  # 整數上限涵蓋該主版本所有次版本
        ([75, 1], [75]),
        (75, [75, 3]),
        ([75], [75, 0]),
        ([75, 2], [75, 2]),
        (74, 75),
    ],
)
def test_repair_keeps_min_max_format_that_overlap(low, high):
    pack = {"description": "d", "min_format": low, "max_format": high}
    fixed, notes = _repair(pack)
    assert fixed == pack and notes == []


@pytest.mark.parametrize(
    ("low", "high"),
    [(76, 75), ([75, 2], [75, 1]), ([75, 1], 74), (76, [75])],
)
def test_repair_drops_min_max_format_that_do_not_overlap(low, high):
    fixed, _ = _repair({"description": "d", "min_format": low, "max_format": high})
    assert "min_format" not in fixed and "max_format" not in fixed


def test_repair_adds_min_max_format_to_pack_format_only_new_source():
    fixed, notes = _repair({"description": "d", "pack_format": 69})
    assert "pack_format" not in fixed  # 只支援 65 以上，不留舊欄位
    assert fixed["min_format"] == 69 and fixed["max_format"] == 9999
    assert any("min_format" in n for n in notes)


def test_repair_removes_a_lone_min_or_max_format():
    fixed, _ = _repair({"description": "d", "pack_format": 34, "max_format": 80})
    assert "max_format" not in fixed and fixed["pack_format"] == 34


def _bundle_zip(tmp_path, src, **kwargs):
    out = tmp_path / "out.zip"
    list(bundle_outputs_generator(str(src), str(out), **kwargs))
    with zipfile.ZipFile(out) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_pack_files_inside_an_extra_folder_become_the_pack_files(tmp_path, src_dir):
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "pack.mcmeta").write_text(
        '{"pack":{"description":"extra","pack_format":15}}', encoding="utf-8"
    )
    (extra / "pack.png").write_bytes(b"EXTRA-PNG")
    files = _bundle_zip(tmp_path, src_dir, pack_format=34, extra_folders=[str(extra)])
    assert json.loads(files["pack.mcmeta"])["pack"]["description"] == "extra"
    assert files["pack.png"] == b"EXTRA-PNG"
    assert not [n for n in files if n.startswith("pack_")]


def test_pack_files_in_a_subfolder_of_an_extra_folder_are_found(tmp_path, src_dir):
    extra = tmp_path / "extra"
    (extra / "child").mkdir(parents=True)
    (extra / "child" / "pack.png").write_bytes(b"CHILD-PNG")
    files = _bundle_zip(tmp_path, src_dir, pack_format=34, extra_folders=[str(extra)])
    assert files["pack.png"] == b"CHILD-PNG"


def test_root_subfolder_pack_files_are_used_not_renamed(tmp_path, src_dir):
    (src_dir / "root").mkdir()
    (src_dir / "root" / "pack.mcmeta").write_text(
        '{"pack":{"description":"from root","pack_format":15}}', encoding="utf-8"
    )
    (src_dir / "root" / "pack.png").write_bytes(b"ROOT-PNG")
    files = _bundle_zip(tmp_path, src_dir, pack_format=34)
    assert json.loads(files["pack.mcmeta"])["pack"]["description"] == "from root"
    assert files["pack.png"] == b"ROOT-PNG"
    assert not [n for n in files if n.startswith("pack_")]


def test_nested_pack_png_is_still_packed_normally(tmp_path, src_dir):
    (src_dir / "assets").mkdir()
    (src_dir / "assets" / "pack.png").write_bytes(b"NESTED")
    files = _bundle_zip(tmp_path, src_dir, pack_format=34)
    assert files["assets/pack.png"] == b"NESTED"


def test_preview_finds_pack_files_in_extra_folder(tmp_path, src_dir):
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "pack.mcmeta").write_text(
        '{"pack":{"description":"extra","pack_format":15}}', encoding="utf-8"
    )
    text, source, _notes, used = preview_pack_mcmeta(
        str(src_dir), [str(extra)], "ui", 34
    )
    assert used and source == str(extra / "pack.mcmeta")
    assert json.loads(text)["pack"]["description"] == "extra"


def test_repair_removes_old_fields_when_only_new_formats_are_supported():
    fixed, notes = _repair(
        {
            "description": "d",
            "pack_format": 69,
            "supported_formats": 69,
            "min_format": 69,
            "max_format": 75,
        }
    )
    assert "pack_format" not in fixed and "supported_formats" not in fixed
    assert (fixed["min_format"], fixed["max_format"]) == (69, 75)
    assert any("已移除" in n and "supported_formats" in n for n in notes)


def test_repair_adds_old_fields_when_the_range_starts_below_65():
    fixed, notes = _repair({"description": "d", "min_format": 34, "max_format": 75})
    assert fixed["pack_format"] == 34
    assert fixed["supported_formats"] == {"min_inclusive": 34, "max_inclusive": 64}
    assert (fixed["min_format"], fixed["max_format"]) == (34, 75)
    assert notes


def test_repair_adds_new_fields_when_supported_formats_reach_65():
    fixed, _ = _repair(
        {
            "description": "d",
            "pack_format": 34,
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 70},
        }
    )
    assert (fixed["min_format"], fixed["max_format"]) == (34, 70)


def test_repair_keeps_existing_min_max_format_on_an_old_only_pack():
    pack = {
        "description": "d",
        "pack_format": 34,
        "supported_formats": {"min_inclusive": 34, "max_inclusive": 46},
        "min_format": 34,
        "max_format": 46,
    }
    fixed, notes = _repair(pack)
    assert fixed == pack and notes == []


def test_repair_keeps_valid_hybrid_and_old_only_sources_unchanged():
    hybrid = {
        "description": "d",
        "pack_format": 34,
        "supported_formats": {"min_inclusive": 34, "max_inclusive": 64},
        "min_format": 34,
        "max_format": 9999,
    }
    for pack in (hybrid, {"description": "d", "pack_format": 34}):
        fixed, notes = _repair(pack)
        assert fixed == pack and notes == []


def test_integer_max_format_uses_the_games_minor_ceiling():
    from translation_tool.core.pack_mcmeta import _ANY_MINOR

    assert _ANY_MINOR == 0x7FFFFFFF


def test_repair_drops_old_fields_whose_range_contradicts_the_new_range():
    fixed, notes = _repair(
        {
            "description": "d",
            "pack_format": 34,
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 64},
            "min_format": 69,
            "max_format": 75,
        }
    )
    assert "pack_format" not in fixed and "supported_formats" not in fixed
    assert (fixed["min_format"], fixed["max_format"]) == (69, 75)
    assert any("不一致" in n for n in notes)


def test_repair_realigns_supported_formats_with_the_new_range():
    fixed, notes = _repair(
        {
            "description": "d",
            "pack_format": 34,
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 46},
            "min_format": 34,
            "max_format": 75,
        }
    )
    assert fixed["supported_formats"] == {"min_inclusive": 34, "max_inclusive": 64}
    assert fixed["pack_format"] == 34 and fixed["max_format"] == 75
    assert any("supported_formats" in n for n in notes)


def test_repair_moves_pack_format_into_the_new_range():
    fixed, _ = _repair(
        {
            "description": "d",
            "pack_format": 20,
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 64},
            "min_format": 34,
            "max_format": 75,
        }
    )
    assert fixed["pack_format"] == 34


def _repair_with_overlays(pack: dict, entries: list) -> tuple[dict, list[str]]:
    content, notes = repair_source_pack_mcmeta(
        json.dumps({"pack": pack, "overlays": {"entries": entries}}), GENERATED
    )
    return json.loads(content)["overlays"]["entries"], notes


NEW_PACK = {"description": "d", "min_format": 69, "max_format": 75}


def test_overlay_formats_for_new_only_range_become_min_max_format():
    entries, notes = _repair_with_overlays(
        NEW_PACK, [{"directory": "alt", "formats": [69, 75]}]
    )
    assert entries == [{"directory": "alt", "min_format": 69, "max_format": 75}]
    assert any(n.startswith("overlays.entries[0]") for n in notes)


def test_overlay_crossing_64_65_gets_both_field_groups():
    entries, _ = _repair_with_overlays(
        NEW_PACK, [{"directory": "x", "min_format": 34, "max_format": 75}]
    )
    assert entries[0]["formats"] == {"min_inclusive": 34, "max_inclusive": 64}
    assert (entries[0]["min_format"], entries[0]["max_format"]) == (34, 75)


def test_overlay_old_only_entry_keeps_formats_and_gets_min_max_format():
    entries, notes = _repair_with_overlays(
        NEW_PACK, [{"directory": "x", "formats": [34, 46]}]
    )
    assert entries == [
        {"directory": "x", "formats": [34, 46], "min_format": 34, "max_format": 46}
    ]
    assert notes


def test_overlay_old_only_entry_keeps_its_min_max_format():
    entry = {"directory": "x", "formats": [34, 46], "min_format": 34, "max_format": 46}
    entries, notes = _repair_with_overlays(NEW_PACK, [entry])
    assert entries == [entry] and notes == []


def test_every_overlay_gets_formats_when_any_overlay_covers_old_versions():
    entries, _ = _repair_with_overlays(
        NEW_PACK,
        [
            {"directory": "old", "formats": [34, 46]},
            {"directory": "new", "min_format": 69, "max_format": 75},
        ],
    )
    assert all("formats" in e and "min_format" in e for e in entries)
    assert entries[1]["formats"] == {"min_inclusive": 69, "max_inclusive": 75}


def test_overlay_formats_are_removed_when_no_overlay_covers_old_versions():
    entries, _ = _repair_with_overlays(
        NEW_PACK,
        [
            {"directory": "a", "formats": [69, 75], "min_format": 69, "max_format": 75},
            {"directory": "b", "min_format": 70, "max_format": 75},
        ],
    )
    assert all("formats" not in e for e in entries)


@pytest.mark.parametrize(
    "entry",
    [
        {"directory": "x", "formats": "invalid"},
        {"directory": "x", "min_format": 80, "max_format": 70},
        {"directory": "x"},
        {"directory": "x", "formats": [46, 34]},
    ],
)
def test_unrepairable_overlay_makes_the_whole_source_fall_back(entry):
    content, notes = repair_source_pack_mcmeta(
        json.dumps({"pack": NEW_PACK, "overlays": {"entries": [entry]}}), GENERATED
    )
    assert content is None
    assert len(notes) == 1 and "overlays.entries[0]" in notes[0]
    assert "無法安全修復" in notes[0]


def test_non_object_overlay_entry_makes_the_source_fall_back():
    content, notes = repair_source_pack_mcmeta(
        json.dumps({"pack": NEW_PACK, "overlays": {"entries": ["x"]}}), GENERATED
    )
    assert content is None and "無法安全修復" in notes[0]


def test_bundle_falls_back_to_ui_settings_for_unrepairable_overlay(tmp_path, src_dir):
    (src_dir / "pack.mcmeta").write_text(
        json.dumps(
            {
                "pack": NEW_PACK,
                "overlays": {"entries": [{"directory": "x", "formats": "bad"}]},
            }
        ),
        encoding="utf-8",
    )
    data, updates = _bundle(tmp_path, pack_format=34)
    assert data == build_pack_mcmeta("", 34) | {"pack": data["pack"]}
    assert "overlays" not in data and data["pack"]["pack_format"] == 34
    assert any("無法安全修復" in u.get("log", "") for u in updates)


def test_overlay_entry_gets_no_pack_format_and_other_keys_survive():
    entries, _ = _repair_with_overlays(
        NEW_PACK, [{"directory": "x", "formats": 34, "custom": {"a": 1}}]
    )
    assert "pack_format" not in entries[0] and entries[0]["custom"] == {"a": 1}
    assert entries[0]["formats"] == 34


def test_overlay_quoted_numbers_are_repaired():
    entries, notes = _repair_with_overlays(
        NEW_PACK, [{"directory": "a", "formats": ["34", "46"]}]
    )
    assert entries[0]["formats"] == [34, 46] and entries[0]["min_format"] == 34
    assert notes


def test_valid_overlays_leave_the_source_byte_for_byte():
    text = json.dumps(
        {
            "pack": NEW_PACK,
            "overlays": {
                "entries": [
                    {
                        "directory": "a",
                        "formats": {"min_inclusive": 69, "max_inclusive": 75},
                        "min_format": 69,
                        "max_format": 75,
                    },
                    {
                        "directory": "b",
                        "formats": [34, 46],
                        "min_format": 34,
                        "max_format": 46,
                    },
                    {
                        "directory": "c",
                        "formats": {"min_inclusive": 34, "max_inclusive": 64},
                        "min_format": 34,
                        "max_format": 9999,
                    },
                ]
            },
        },
        indent=4,
    )
    assert repair_source_pack_mcmeta(text, GENERATED) == (text, [])


def test_bundle_and_preview_agree_on_repaired_overlays(tmp_path, src_dir):
    (src_dir / "pack.mcmeta").write_text(
        json.dumps(
            {
                "pack": NEW_PACK,
                "overlays": {"entries": [{"directory": "alt", "formats": [69, 75]}]},
            }
        ),
        encoding="utf-8",
    )
    text, _source, _notes, used = preview_pack_mcmeta(str(src_dir), None, "ui", 34)
    data, updates = _bundle(tmp_path, pack_format=34)
    assert used and json.loads(text) == data
    assert data["overlays"]["entries"][0] == {
        "directory": "alt",
        "min_format": 69,
        "max_format": 75,
    }
    assert any("overlays.entries[0]" in u.get("log", "") for u in updates)
