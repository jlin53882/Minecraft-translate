from __future__ import annotations

import os
import zipfile

import flet as ft
import pytest

from app.views.pipeline.extra_bundle_sources import (
    build_extra_source_controls,
    refresh_extra_source_preview,
)
from translation_tool.core.bundle_sources import (
    RAW_ATTACHMENT_MODE,
    RESOURCE_PACK_MODE,
    ExtraBundleSource,
    normalize_extra_bundle_sources,
    serialize_extra_bundle_sources,
)
from translation_tool.core.output_bundler import (
    bundle_outputs_generator,
    inspect_extra_bundle_sources,
)


def _run(src, output, *, extra=None):
    return list(
        bundle_outputs_generator(
            str(src),
            str(output),
            extra_folders=extra,
            force_rebuild=True,
        )
    )


def _errors(updates):
    return [item for item in updates if item.get("error")]


def test_raw_attachment_preserves_mixed_files_nested_paths_and_bytes(tmp_path):
    src = tmp_path / "staging"
    src.mkdir()
    attachments = tmp_path / "guides"
    (attachments / "nested").mkdir(parents=True)
    (attachments / "readme.md").write_text("A, } and ]", encoding="utf-8")
    (attachments / "nested" / "guide.txt").write_text("guide", encoding="utf-8")
    binary = b"\x00\x01\xff\x89PNG\r\n"
    (attachments / "nested" / "image.png").write_bytes(binary)
    (attachments / "empty.snbt").write_bytes(b"")
    output = tmp_path / "pack.zip"

    updates = _run(
        src,
        output,
        extra=[
            {
                "source": str(attachments),
                "mode": RAW_ATTACHMENT_MODE,
                "recursive": True,
                "target": "attachments",
            }
        ],
    )

    assert not _errors(updates)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("attachments/readme.md") == b"A, } and ]"
        assert archive.read("attachments/nested/guide.txt") == b"guide"
        assert archive.read("attachments/nested/image.png") == binary
        assert archive.read("attachments/empty.snbt") == b""
        assert archive.namelist().count("pack.mcmeta") == 1
        assert "guides/readme.md" not in archive.namelist()

    result = next(item["bundle_result"] for item in updates if "bundle_result" in item)
    assert result["raw_files"] == 4
    assert result["raw_bytes"] == len(b"A, } and ]") + len(b"guide") + len(binary)


def test_raw_attachment_non_recursive_keeps_only_direct_files(tmp_path):
    src = tmp_path / "staging"
    src.mkdir()
    folder = tmp_path / "source"
    folder.mkdir()
    (folder / "top.md").write_text("top", encoding="utf-8")
    (folder / "nested").mkdir()
    (folder / "nested" / "deep.md").write_text("deep", encoding="utf-8")
    output = tmp_path / "pack.zip"

    updates = _run(
        src,
        output,
        extra=[
            ExtraBundleSource(
                str(folder), RAW_ATTACHMENT_MODE, recursive=False, target="raw"
            )
        ],
    )

    assert not _errors(updates)
    with zipfile.ZipFile(output) as archive:
        assert "raw/top.md" in archive.namelist()
        assert "raw/nested/deep.md" not in archive.namelist()


def test_legacy_extra_folder_still_uses_resource_pack_assets_rule(tmp_path):
    src = tmp_path / "staging"
    (src / "assets" / "base" / "lang").mkdir(parents=True)
    (src / "assets" / "base" / "lang" / "zh_tw.json").write_text("{}", encoding="utf-8")
    extra = tmp_path / "extra"
    (extra / "assets" / "addon" / "lang").mkdir(parents=True)
    (extra / "assets" / "addon" / "lang" / "zh_tw.json").write_text(
        "{}", encoding="utf-8"
    )
    (extra / "ignored.md").write_text("not a resource-pack entry", encoding="utf-8")
    output = tmp_path / "pack.zip"

    updates = _run(src, output, extra=[str(extra)])

    assert not _errors(updates)
    with zipfile.ZipFile(output) as archive:
        assert "assets/base/lang/zh_tw.json" in archive.namelist()
        assert "assets/addon/lang/zh_tw.json" in archive.namelist()
        assert "ignored.md" not in archive.namelist()


def test_raw_duplicate_target_aborts_without_replacing_existing_zip(tmp_path):
    src = tmp_path / "staging"
    src.mkdir()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "same.md").write_text("first", encoding="utf-8")
    (second / "same.md").write_text("second", encoding="utf-8")
    output = tmp_path / "pack.zip"

    assert not _errors(_run(src, output))
    original = output.read_bytes()
    updates = _run(
        src,
        output,
        extra=[
            {"source": str(first), "mode": RAW_ATTACHMENT_MODE, "target": "dup"},
            {"source": str(second), "mode": RAW_ATTACHMENT_MODE, "target": "dup"},
        ],
    )

    assert _errors(updates)
    assert output.read_bytes() == original
    assert not (tmp_path / "pack.zip.tmp").exists()


@pytest.mark.parametrize(
    "target", ["../escape", "C:/escape", "/escape", r"\\server\escape"]
)
def test_raw_target_rejects_path_escape_or_absolute(target, tmp_path):
    src = tmp_path / "staging"
    src.mkdir()
    folder = tmp_path / "source"
    folder.mkdir()
    (folder / "x.txt").write_text("x", encoding="utf-8")

    updates = _run(
        src,
        tmp_path / "pack.zip",
        extra=[{"source": str(folder), "mode": RAW_ATTACHMENT_MODE, "target": target}],
    )

    assert _errors(updates)
    assert any("路徑" in item["log"] for item in _errors(updates))


def test_raw_missing_source_is_an_error_not_a_success(tmp_path):
    src = tmp_path / "staging"
    src.mkdir()
    output = tmp_path / "pack.zip"

    updates = _run(
        src,
        output,
        extra=[
            {
                "source": str(tmp_path / "missing"),
                "mode": RAW_ATTACHMENT_MODE,
            }
        ],
    )

    assert _errors(updates)
    assert not output.exists()


def test_raw_symlink_is_rejected_when_platform_allows_symlink_creation(tmp_path):
    src = tmp_path / "staging"
    src.mkdir()
    folder = tmp_path / "source"
    folder.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        os.symlink(outside, folder / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("此 Windows 執行環境沒有建立 symlink 的權限")

    updates = _run(
        src,
        tmp_path / "pack.zip",
        extra=[{"source": str(folder), "mode": RAW_ATTACHMENT_MODE}],
    )
    assert _errors(updates)
    assert any("符號連結" in item["log"] for item in _errors(updates))


def test_extra_source_serialization_is_backward_compatible(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    legacy = normalize_extra_bundle_sources([str(source)])[0]
    modern = normalize_extra_bundle_sources(
        [
            {
                "path": str(source),
                "mode": RAW_ATTACHMENT_MODE,
                "recursive": False,
                "target_root": "docs",
            }
        ]
    )[0]

    assert legacy.mode == RESOURCE_PACK_MODE
    assert modern == ExtraBundleSource(str(source), RAW_ATTACHMENT_MODE, False, "docs")
    assert serialize_extra_bundle_sources([str(source), modern]) == [
        str(source),
        {
            "source": str(source),
            "mode": RAW_ATTACHMENT_MODE,
            "recursive": False,
            "target": "docs",
        },
    ]


def test_extra_source_preview_reports_files_bytes_and_invalid_source(tmp_path):
    folder = tmp_path / "source"
    folder.mkdir()
    (folder / "a.md").write_bytes(b"abc")
    valid = inspect_extra_bundle_sources(
        [{"source": str(folder), "mode": RAW_ATTACHMENT_MODE, "target": "docs"}]
    )[0]
    invalid = inspect_extra_bundle_sources(
        [{"source": str(tmp_path / "missing"), "mode": RAW_ATTACHMENT_MODE}]
    )[0]

    assert valid["valid"] is True
    assert valid["files"] == 1
    assert valid["bytes"] == 3
    assert invalid["valid"] is False


def test_resource_pack_preview_warns_when_source_has_no_assets(tmp_path):
    source = tmp_path / "smart-review.chunks"
    source.mkdir()
    (source / "chunk-0001.json").write_text("{}", encoding="utf-8")

    report = inspect_extra_bundle_sources([str(source)])[0]

    assert report["valid"] is True
    assert report["files"] == 0
    assert "改用原樣附加" in report["warning"]

    controls = build_extra_source_controls(
        on_pick=lambda _event=None: None,
        on_manual_add=lambda _event=None: None,
        picker_label="新增資料夾",
    )
    refresh_extra_source_preview(controls, [str(source)], lambda _value: None)
    assert "改用原樣附加" in controls.preview_text.value


def test_empty_extra_source_list_does_not_reserve_large_ui_area(tmp_path):
    controls = build_extra_source_controls(
        on_pick=lambda _event=None: None,
        on_manual_add=lambda _event=None: None,
        picker_label="新增資料夾",
        list_height=100,
    )

    assert controls.source_list_container.visible is False
    assert controls.source_list_container.height == 0
    assert controls.source_list_container.content is None
    refresh_extra_source_preview(controls, [], lambda _value: None)
    assert controls.source_list_container.visible is False
    assert controls.source_list_container.height == 0
    assert controls.source_list_container.content is None

    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.md").write_text("guide", encoding="utf-8")
    refresh_extra_source_preview(
        controls,
        [{"source": str(source), "mode": RAW_ATTACHMENT_MODE}],
        lambda _value: None,
    )
    assert controls.source_list_container.visible is True
    assert controls.source_list_container.height == 108
    assert controls.source_list_container.content is controls.source_list
    assert controls.source_list_container.padding == 4
    assert isinstance(controls.source_list, ft.ListView)
    assert controls.source_list.height == 100
    assert controls.source_list.expand is False


def test_extra_source_target_field_has_bounded_web_layout():
    controls = build_extra_source_controls(
        on_pick=lambda _event=None: None,
        on_manual_add=lambda _event=None: None,
        picker_label="新增資料夾",
    )

    target_rows = [
        item
        for item in controls.content.controls
        if isinstance(item, ft.Row) and controls.target_field in item.controls
    ]

    assert len(target_rows) == 1
    assert target_rows[0].wrap is False
    assert controls.target_field.expand is None
    assert controls.target_field.width == 360
