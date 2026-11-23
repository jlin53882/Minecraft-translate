"""Markdown 流水線（ModDB）：掃描、同步、輸出 overlay、來源探索。"""

from __future__ import annotations

from pathlib import Path

import pytest

from translation_tool.translation_db.md_pipeline import (
    MdPipelineError,
    MdPipelineOptions,
    discover_md_source_folders,
    run_md_pipeline,
    scan_md_sources,
)
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.schema import KIND_GUIDE

EN = (
    "# Title\n\n"
    "This is the first paragraph of the guide and you can read it.\n\n"
    "This is the second paragraph which explains how the machine works.\n"
)
CN = "# 标题\n\n第一段。\n\n第二段。\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def db(tmp_path):
    handle = TranslationDB(tmp_path / "t.db")
    yield handle
    handle.close()


def _fake_runner(db, scan, options, session):
    """把每個缺譯條目寫成「譯:原文」，模擬 LM 翻譯 phase。"""
    from translation_tool.translation_db.models import ScanItem  # noqa: F401

    for item in scan.items:
        db.write_back(
            options.version,
            [_wb(item, "譯:" + item.en_us)],
            fill_other_versions=False,
        )
    return {"status": "DONE", "written": len(scan.items)}


def _wb(item, text):
    from translation_tool.translation_db.resolver import WriteBackItem

    return WriteBackItem(item.kind, item.mod_id, item.key, item.en_us, text)


def test_scan_uses_resource_pack_identity(tmp_path):
    _write(tmp_path / "assets" / "demo" / "guide" / "intro.md", EN)

    result = scan_md_sources(tmp_path)

    assert result.items and {i.kind for i in result.items} == {KIND_GUIDE}
    assert {i.mod_id for i in result.items} == {"demo"}
    assert result.files[0].out_rel == "assets/demo/guide/_zh_tw/intro.md"
    assert all("#" in i.key for i in result.items)


def test_scan_folder_without_assets_uses_scope_id(tmp_path):
    src = tmp_path / "My Pack"
    _write(src / "pack" / "en_us" / "guide.md", EN)

    result = scan_md_sources(src)

    assert {i.mod_id for i in result.items} == {"md-my-pack"}
    assert result.files[0].out_rel == "pack/zh_tw/guide.md"


def test_zh_cn_is_read_only_when_enabled_and_aligned(tmp_path):
    _write(tmp_path / "pack" / "en_us" / "guide.md", EN)
    _write(tmp_path / "pack" / "zh_cn" / "guide.md", CN)

    on = scan_md_sources(tmp_path, read_zh_cn=True)
    off = scan_md_sources(tmp_path, read_zh_cn=False)

    assert any(i.zh_cn for i in on.items) and on.zh_cn_used_files == 1
    assert not any(i.zh_cn for i in off.items) and off.zh_cn_used_files == 0


def test_misaligned_zh_cn_is_reported_not_used(tmp_path):
    _write(tmp_path / "pack" / "en_us" / "guide.md", EN)
    _write(tmp_path / "pack" / "zh_cn" / "guide.md", "# 标题\n\n只有一段。\n")

    result = scan_md_sources(tmp_path)

    assert result.zh_cn_used_files == 0
    assert any(i["reason"] == "misaligned" for i in result.issues)


def test_dry_run_does_not_write_db_or_output(tmp_path, db):
    src = tmp_path / "in"
    _write(src / "assets" / "demo" / "guide" / "intro.md", EN)
    out = tmp_path / "out"

    report = run_md_pipeline(
        db, src, MdPipelineOptions(version="1.21.1", output_root=str(out))
    )

    assert report.ingest is not None and report.ingest.new_entries > 0
    assert db.load_mod("demo") == []
    assert not out.exists()


def test_full_run_translates_and_publishes_resource_pack(tmp_path, db):
    src = tmp_path / "in"
    _write(src / "assets" / "demo" / "guide" / "intro.md", EN)
    out = tmp_path / "out"
    options = MdPipelineOptions(
        version="1.21.1",
        dry_run=False,
        publish=True,
        output_root=str(out),
        translate_missing=True,
    )

    report = run_md_pipeline(db, src, options, translation_runner=_fake_runner)

    final = (out / "assets" / "demo" / "guide" / "_zh_tw" / "intro.md").read_text(
        encoding="utf-8"
    )
    assert (
        "譯:This is the first paragraph of the guide and you can read it." in final
        and "# Title" in final
    )
    assert (out / "pack.mcmeta").is_file()
    assert report.output.files_written == 1
    assert (src / "assets" / "demo" / "guide" / "intro.md").read_text(
        encoding="utf-8"
    ) == EN


def test_existing_db_translation_is_reused_without_runner(tmp_path, db):
    src = tmp_path / "in"
    _write(src / "assets" / "demo" / "guide" / "intro.md", EN)
    scan = scan_md_sources(src)
    for item in scan.items:
        db.write_back(
            "1.21.1", [_wb(item, "庫:" + item.en_us)], fill_other_versions=False
        )
    out = tmp_path / "out"

    run_md_pipeline(
        db,
        src,
        MdPipelineOptions(
            version="1.21.1", dry_run=False, publish=True, output_root=str(out)
        ),
    )

    final = (out / "assets" / "demo" / "guide" / "_zh_tw" / "intro.md").read_text(
        encoding="utf-8"
    )
    assert "庫:This is the first paragraph of the guide and you can read it." in final


def test_manually_edited_output_is_not_overwritten(tmp_path, db):
    src = tmp_path / "in"
    guide = _write(src / "pack" / "en_us" / "guide.md", EN)
    out = tmp_path / "out"
    options = MdPipelineOptions(
        version="1.21.1",
        dry_run=False,
        publish=True,
        output_root=str(out),
        translate_missing=True,
    )
    run_md_pipeline(db, src, options, translation_runner=_fake_runner)
    target = out / "pack" / "zh_tw" / "guide.md"
    target.write_text("我手動修好的\n", encoding="utf-8")
    guide.write_text(EN + "\nThird paragraph here.\n", encoding="utf-8")

    report = run_md_pipeline(db, src, options, translation_runner=_fake_runner)

    assert target.read_text(encoding="utf-8") == "我手動修好的\n"
    assert report.output.skipped_manual_edit == 1


def test_output_inside_source_is_rejected(tmp_path, db):
    src = tmp_path / "in"
    _write(src / "pack" / "en_us" / "guide.md", EN)

    with pytest.raises(MdPipelineError):
        run_md_pipeline(
            db,
            src,
            MdPipelineOptions(version="1.21.1", output_root=str(src / "out")),
        )


def test_publish_requires_output_and_non_dry_run():
    with pytest.raises(MdPipelineError):
        MdPipelineOptions(version="1.21.1", publish=True)
    with pytest.raises(MdPipelineError):
        MdPipelineOptions(version="1.21.1", publish=True, output_root="x")


def test_discover_lists_pack_resource_folders(tmp_path):
    (tmp_path / "kubejs" / "assets").mkdir(parents=True)
    (tmp_path / "resourcepacks" / "a" / "assets").mkdir(parents=True)
    (tmp_path / "resourcepacks" / "no_assets").mkdir(parents=True)
    (tmp_path / "saves").mkdir()

    found = discover_md_source_folders(tmp_path)

    assert [Path(p).relative_to(tmp_path.resolve()).as_posix() for p in found] == [
        "kubejs",
        "resourcepacks/a",
    ]
    plain = tmp_path / "plain"
    plain.mkdir()
    assert discover_md_source_folders(plain) == [str(plain.resolve())]
