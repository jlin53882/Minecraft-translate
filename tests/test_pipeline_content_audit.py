from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services_impl.pipelines.bundle_service import build_bundle_staging
from translation_tool.core.guide_translation import (
    GUIDE_MARKER,
    guide_output_rel,
    normalize_guideme_default_locale_rel,
)
from translation_tool.core.pipeline_content_audit import (
    _pending_guide_rel,
    classify_other_output_assets,
)


def test_other_output_text_is_emitted_or_queued_without_silent_drop(tmp_path: Path):
    merge_output = tmp_path / "merge"
    other_assets = merge_output / "other_output" / "assets" / "demo" / "guide"
    other_assets.mkdir(parents=True)
    localized = other_assets / "localized.md"
    localized.write_text("# 中文指南", encoding="utf-8")
    english = other_assets / "english.md"
    english.write_text("# English guide", encoding="utf-8")
    unsupported = other_assets / "unknown.json"
    unsupported.write_text('{"text": "English"}', encoding="utf-8")

    records = classify_other_output_assets(merge_output)

    assert {record["status"] for record in records} == {"emitted", "pending"}
    assert (merge_output / "assets/demo/guide/localized.md").read_text(
        encoding="utf-8"
    ) == "# 中文指南"
    assert (
        merge_output / "patchouli_output/待翻譯/assets/demo/guide/_en_us/english.md"
    ).is_file()
    assert unsupported.is_file()
    assert any(
        record["reason"] == "unsupported_other_output_format" for record in records
    )


def test_pending_guide_uses_one_locale_root_for_relative_links():
    relative = Path("assets/demo/ae2guide/ae2-mechanics/page.md")

    assert _pending_guide_rel(relative) == Path(
        "assets/demo/ae2guide/_en_us/ae2-mechanics/page.md"
    )


def test_mi_guidebook_uses_one_locale_root_for_relative_links(tmp_path: Path):
    relative = Path("assets/demo/mi_guidebook/io_guide/page.md")

    assert guide_output_rel(relative, tmp_path) == Path(
        "assets/demo/mi_guidebook/_zh_tw/io_guide/page.md"
    )


def test_default_guideme_root_uses_registered_guide_folder(tmp_path: Path):
    """GuideME's default ``guides/<namespace>/<id>`` root owns the locale."""

    source_root = tmp_path / "source"
    index = source_root / (
        "assets/extended_industrialization/guides/"
        "extended_industrialization/guide/index.md"
    )
    index.parent.mkdir(parents=True)
    index.write_text("# Guide", encoding="utf-8")

    relative = Path(
        "assets/extended_industrialization/guides/"
        "extended_industrialization/guide/machines/batching_multiblocks.md"
    )
    assert guide_output_rel(relative, source_root) == Path(
        "assets/extended_industrialization/guides/"
        "extended_industrialization/guide/_zh_tw/machines/batching_multiblocks.md"
    )


@pytest.mark.parametrize(
    "legacy, canonical",
    [
        (
            Path("assets/demo/guides/_en_us/demo/guide/machines/page.md"),
            Path("assets/demo/guides/demo/guide/_en_us/machines/page.md"),
        ),
        (
            Path("assets/demo/guides/demo/guide/machines/_en_us/page.md"),
            Path("assets/demo/guides/demo/guide/_en_us/machines/page.md"),
        ),
    ],
)
def test_legacy_default_guideme_locale_is_normalized_before_translation(
    tmp_path: Path, legacy: Path, canonical: Path
):
    assert normalize_guideme_default_locale_rel(legacy) == canonical
    locale_index = canonical.parts.index("_en_us")
    expected_output = Path(
        *canonical.parts[:locale_index],
        "_zh_tw",
        *canonical.parts[locale_index + 1 :],
    )
    assert guide_output_rel(legacy, tmp_path) == expected_output


@pytest.mark.parametrize(
    "legacy_rel",
    [
        Path("assets/demo/guides/_zh_tw/demo/guide/machines/page.md"),
        Path("assets/demo/guides/demo/guide/machines/_zh_tw/page.md"),
    ],
)
def test_bundle_normalizes_legacy_default_guideme_locale_paths(
    tmp_path: Path, legacy_rel: Path
):
    source = tmp_path / "legacy-output"
    legacy = source / legacy_rel
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# 批次多方塊結構\n", encoding="utf-8")

    staging = tmp_path / "staging"
    build_bundle_staging([str(source)], str(staging))

    canonical = staging / "assets/demo/guides/demo/guide/_zh_tw/machines/page.md"
    assert canonical.read_text(encoding="utf-8") == "# 批次多方塊結構\n"
    assert not (staging / legacy_rel).exists()


def test_lm_writer_and_bundle_keep_default_guideme_locale_at_content_root(
    tmp_path: Path,
):
    """A fresh LM write and a bundle both use the GuideME content-root contract."""

    from translation_tool.core import lm_translator

    source_root = tmp_path / "source"
    guide_root = source_root / "assets/demo/guides/demo/guide"
    (guide_root / "machines").mkdir(parents=True)
    (guide_root / "index.md").write_text("# Guide\n", encoding="utf-8")
    guide = guide_root / "machines/page.md"
    guide.write_text(
        "# Batching Multiblocks\n\n"
        '<Recipe id="demo:removed_recipe" />\n'
        '<RecipeFor id="demo:machine" />\n',
        encoding="utf-8",
    )

    translated = tmp_path / "translated"
    doc = {
        GUIDE_MARKER: True,
        "source": guide.read_text(encoding="utf-8"),
        "ranges": [[1, 1]],
        "prefixes": [""],
        "originals": ["# Batching Multiblocks"],
        "blocks": ["# 批次多方塊結構"],
    }
    lm_translator._write_directory_outputs(
        {str(guide): doc}, {str(guide)}, source_root, translated, False
    )

    localized = translated / "assets/demo/guides/demo/guide/_zh_tw/machines/page.md"
    assert localized.read_text(encoding="utf-8") == (
        "# 批次多方塊結構\n\n"
        '<Recipe id="demo:removed_recipe" />\n'
        '<RecipeFor id="demo:machine" />\n'
    )

    staging = tmp_path / "staging"
    build_bundle_staging([str(source_root), str(translated)], str(staging))
    assert (
        staging / "assets/demo/guides/demo/guide/_zh_tw/machines/page.md"
    ).read_text(encoding="utf-8") == localized.read_text(encoding="utf-8")
    assert not (
        staging / "assets/demo/guides/_zh_tw/demo/guide/machines/page.md"
    ).exists()


def test_pending_default_guideme_root_keeps_relative_links(tmp_path: Path):
    """Pending extraction uses the same default GuideME content-root contract."""

    other_root = tmp_path / "other_output"
    index = other_root / (
        "assets/extended_industrialization/guides/"
        "extended_industrialization/guide/index.md"
    )
    index.parent.mkdir(parents=True)
    index.write_text("# Guide", encoding="utf-8")

    relative = Path(
        "assets/extended_industrialization/guides/"
        "extended_industrialization/guide/machines/batching_multiblocks.md"
    )
    assert _pending_guide_rel(relative, other_root) == Path(
        "assets/extended_industrialization/guides/"
        "extended_industrialization/guide/_en_us/machines/batching_multiblocks.md"
    )


def test_bundle_mirrors_guide_support_for_localized_relative_references(tmp_path: Path):
    """Locale relocation must not break ImportStructure/image references."""
    source = tmp_path / "source"
    translated = tmp_path / "translated"

    neutral_guide = source / "assets/demo/ae2guide/ae2-mechanics/page.md"
    neutral_guide.parent.mkdir(parents=True)
    neutral_guide.write_text(
        '<ImportStructure src="../assets/blocks/export_bus.snbt" />\n'
        "![diagram](../assets/images/network.png)\n",
        encoding="utf-8",
    )
    (source / "assets/demo/ae2guide/assets/blocks/export_bus.snbt").parent.mkdir(
        parents=True
    )
    (source / "assets/demo/ae2guide/assets/blocks/export_bus.snbt").write_bytes(
        b"structure"
    )
    image = source / "assets/demo/ae2guide/assets/images/network.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    translated_guide = translated / "assets/demo/ae2guide/_zh_tw/ae2-mechanics/page.md"
    translated_guide.parent.mkdir(parents=True)
    translated_guide.write_text(
        '<ImportStructure src="../assets/blocks/export_bus.snbt" />\n'
        "![diagram](../assets/images/network.png)\n",
        encoding="utf-8",
    )

    staging = tmp_path / "staging"
    stats = build_bundle_staging(
        [str(source), str(translated)],
        str(staging),
    )

    mirrored_root = staging / "assets/demo/ae2guide/_zh_tw/assets"
    assert (mirrored_root / "blocks/export_bus.snbt").read_bytes() == b"structure"
    assert (mirrored_root / "images/network.png").read_bytes() == b"image"
    assert stats["guide_support_mirrored"] == 2
    assert stats["guide_support_missing"] == 0
    manifest = json.loads(
        (tmp_path / "translation_output_manifest.json").read_text(encoding="utf-8")
    )
    support_records = {
        record["output"]: record
        for record in manifest["records"]
        if record["reason"] == "localized_guide_support"
    }
    assert (
        support_records["assets/demo/ae2guide/_zh_tw/assets/blocks/export_bus.snbt"][
            "status"
        ]
        == "emitted"
    )
    assert (
        support_records["assets/demo/ae2guide/_zh_tw/assets/images/network.png"][
            "status"
        ]
        == "emitted"
    )


def test_incremental_guide_support_matches_clean_rebuild_after_change_and_delete(
    tmp_path: Path,
):
    """Repeated staging must not retain stale localized support files."""
    source = tmp_path / "source"
    translated = tmp_path / "translated"
    guide = source / "assets/demo/guides/demo/guide/chapter/page.md"
    guide.parent.mkdir(parents=True)
    guide.write_text(
        '<ImportStructure src="../assets/blocks/machine.snbt" />\n',
        encoding="utf-8",
    )
    support = source / "assets/demo/guides/demo/guide/assets/blocks/machine.snbt"
    support.parent.mkdir(parents=True)
    support.write_bytes(b"A")
    localized = translated / "assets/demo/guides/demo/guide/_zh_tw/chapter/page.md"
    localized.parent.mkdir(parents=True)
    localized.write_text(
        '<ImportStructure src="../assets/blocks/machine.snbt" />\n',
        encoding="utf-8",
    )

    incremental = tmp_path / "incremental"
    build_bundle_staging([str(source), str(translated)], str(incremental))
    mirrored = (
        incremental / "assets/demo/guides/demo/guide/_zh_tw/assets/blocks/machine.snbt"
    )
    assert mirrored.read_bytes() == b"A"

    support.write_bytes(b"B")
    changed_stats = build_bundle_staging(
        [str(source), str(translated)], str(incremental)
    )
    assert mirrored.read_bytes() == b"B"
    assert changed_stats["guide_support_missing"] == 0

    support.unlink()
    deleted_stats = build_bundle_staging(
        [str(source), str(translated)], str(incremental)
    )
    assert deleted_stats["guide_support_missing"] == 1
    assert not mirrored.exists()
    assert not any(
        record["reason"] == "localized_guide_support"
        for record in json.loads(
            (tmp_path / "translation_output_manifest.json").read_text(encoding="utf-8")
        )["records"]
    )

    clean = tmp_path / "clean"
    clean_stats = build_bundle_staging([str(source), str(translated)], str(clean))
    assert (
        clean_stats["guide_support_missing"] == deleted_stats["guide_support_missing"]
    )
    incremental_files = {
        path.relative_to(incremental).as_posix(): path.read_bytes()
        for path in incremental.rglob("*")
        if path.is_file()
    }
    clean_files = {
        path.relative_to(clean).as_posix(): path.read_bytes()
        for path in clean.rglob("*")
        if path.is_file()
    }
    assert incremental_files == clean_files


def test_bundle_mirrors_support_for_default_guideme_root(tmp_path: Path):
    """Default GuideME roots keep support files beside the localized root."""

    source = tmp_path / "source"
    root = source / "assets/demo/guides/demo/guide"
    (root / "machines").mkdir(parents=True)
    (root / "index.md").write_text("# Guide", encoding="utf-8")
    (root / "machines/page.md").write_text(
        '<ImportStructure src="../assets/blocks/machine.snbt" />\n',
        encoding="utf-8",
    )
    (root / "assets/blocks/machine.snbt").parent.mkdir(parents=True)
    (root / "assets/blocks/machine.snbt").write_bytes(b"machine")

    translated = tmp_path / "translated"
    localized = translated / "assets/demo/guides/demo/guide/_zh_tw/machines/page.md"
    localized.parent.mkdir(parents=True)
    localized.write_text(
        '<ImportStructure src="../assets/blocks/machine.snbt" />\n',
        encoding="utf-8",
    )

    staging = tmp_path / "staging"
    stats = build_bundle_staging([str(source), str(translated)], str(staging))

    assert (
        staging / "assets/demo/guides/demo/guide/_zh_tw/assets/blocks/machine.snbt"
    ).read_bytes() == b"machine"
    assert stats["guide_support_mirrored"] == 1
    assert stats["guide_support_missing"] == 0


def test_bundle_mirrors_explicit_patchouli_locale_support(tmp_path: Path):
    """Explicit ``en_us``/``zh_tw`` books use the same support-file contract."""
    source = tmp_path / "source"
    translated = tmp_path / "translated"

    source_guide = source / "assets/demo/patchouli_books/book/en_us/chapters/page.md"
    source_guide.parent.mkdir(parents=True)
    source_guide.write_text(
        '<ImportStructure src="../assets/assembly.snbt" />\n',
        encoding="utf-8",
    )
    support = source / "assets/demo/patchouli_books/book/en_us/assets/assembly.snbt"
    support.parent.mkdir(parents=True)
    support.write_bytes(b"assembly")

    translated_guide = (
        translated / "assets/demo/patchouli_books/book/zh_tw/chapters/page.md"
    )
    translated_guide.parent.mkdir(parents=True)
    translated_guide.write_text(
        '<ImportStructure src="../assets/assembly.snbt" />\n',
        encoding="utf-8",
    )

    staging = tmp_path / "staging"
    stats = build_bundle_staging(
        [str(source), str(translated)],
        str(staging),
    )

    assert (
        staging / "assets/demo/patchouli_books/book/zh_tw/assets/assembly.snbt"
    ).read_bytes() == b"assembly"
    assert stats["guide_support_mirrored"] == 1
    assert stats["guide_support_missing"] == 0


def test_bundle_mirrors_zh_cn_guide_support_for_zh_tw_output(tmp_path: Path):
    """A zh_cn-only source still supplies shared binary guide assets."""
    source = tmp_path / "source"
    translated = tmp_path / "translated"

    source_guide = source / "assets/demo/guides/_zh_cn/chapter/page.md"
    source_guide.parent.mkdir(parents=True)
    source_guide.write_text(
        '<ImportStructure src="../assets/assembly.snbt" />\n',
        encoding="utf-8",
    )
    support = source / "assets/demo/guides/_zh_cn/assets/assembly.snbt"
    support.parent.mkdir(parents=True)
    support.write_bytes(b"assembly-zh-cn")

    translated_guide = translated / "assets/demo/guides/_zh_tw/chapter/page.md"
    translated_guide.parent.mkdir(parents=True)
    translated_guide.write_text(
        '<ImportStructure src="../assets/assembly.snbt" />\n',
        encoding="utf-8",
    )

    staging = tmp_path / "staging"
    stats = build_bundle_staging([str(source), str(translated)], str(staging))

    assert (
        staging / "assets/demo/guides/_zh_tw/assets/assembly.snbt"
    ).read_bytes() == b"assembly-zh-cn"
    assert stats["guide_support_mirrored"] == 1
    assert stats["guide_support_missing"] == 0


def test_mixed_guide_text_is_queued_instead_of_marked_emitted(tmp_path: Path):
    merge_output = tmp_path / "merge"
    other_assets = merge_output / "other_output" / "assets" / "demo"
    other_assets.mkdir(parents=True)
    localized = other_assets / "localized.md"
    localized.write_text("# 中文標題\n\n這是中文內容。", encoding="utf-8")
    mixed = other_assets / "mixed.mdx"
    mixed.write_text(
        "# 中文標題\n\nThis paragraph still needs translation.",
        encoding="utf-8",
    )

    records = classify_other_output_assets(merge_output)

    by_name = {Path(record["source"]).name: record for record in records}
    assert by_name["localized.md"]["status"] == "emitted"
    assert by_name["mixed.mdx"]["status"] == "pending"
    assert (
        merge_output / "patchouli_output/待翻譯/assets/demo/_en_us/mixed.mdx"
    ).is_file()


def test_bundle_manifest_records_emitted_pending_and_excluded(tmp_path: Path):
    merge_output = tmp_path / "merge"
    (merge_output / "assets/demo/lang").mkdir(parents=True)
    (merge_output / "assets/demo/lang/zh_tw.json").write_text(
        '{"demo.key": "示例"}', encoding="utf-8"
    )
    (merge_output / "data/demo/advancements/start.json").parent.mkdir(parents=True)
    (merge_output / "data/demo/advancements/start.json").write_text(
        '{"display": {"title": {"text": "Literal"}}}', encoding="utf-8"
    )
    (merge_output / "patchouli_output/待翻譯/assets/demo/_en_us").mkdir(parents=True)
    (merge_output / "patchouli_output/待翻譯/assets/demo/_en_us/guide.md").write_text(
        "# English", encoding="utf-8"
    )

    staging = tmp_path / "staging"
    build_bundle_staging([str(merge_output)], str(staging))

    manifest = json.loads(
        (tmp_path / "translation_output_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["resource_pack_boundary"] == "assets_only"
    assert manifest["status_counts"]["emitted"] == 1
    assert manifest["status_counts"]["pending"] == 1
    assert manifest["status_counts"]["excluded"] == 1
    assert not (staging / "data").exists()


def test_bundle_manifest_keeps_reclassified_assets_emitted(tmp_path: Path):
    merge_output = tmp_path / "merge"
    source = merge_output / "other_output/assets/demo/guide/localized.md"
    source.parent.mkdir(parents=True)
    source.write_text("# 中文指南", encoding="utf-8")

    records = classify_other_output_assets(merge_output)
    assert records[0]["status"] == "emitted"

    build_bundle_staging([str(merge_output)], str(tmp_path / "staging"))

    manifest = json.loads(
        (tmp_path / "translation_output_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status_counts"]["emitted"] == 1
    assert manifest["status_counts"].get("excluded", 0) == 0


def test_bundle_manifest_does_not_duplicate_classified_pending_across_sources(
    tmp_path: Path,
):
    merge_output = tmp_path / "merge"
    source = merge_output / "other_output/assets/demo/guide.md"
    source.parent.mkdir(parents=True)
    source.write_text("# English guide", encoding="utf-8")
    classify_other_output_assets(merge_output)

    build_bundle_staging(
        [str(merge_output), str(merge_output / "patchouli_output")],
        str(tmp_path / "staging"),
    )

    manifest = json.loads(
        (tmp_path / "translation_output_manifest.json").read_text(encoding="utf-8")
    )
    pending = [
        record for record in manifest["records"] if record["status"] == "pending"
    ]
    assert len(pending) == 1
    assert pending[0]["reason"] == "english_text_queued_for_guide_translation"


def test_bundle_manifest_does_not_depend_on_common_root_order(tmp_path: Path):
    """重排 custom bundle sources 不應重複記錄已分類的輸出。"""
    merge_output = tmp_path / "merge"
    source = merge_output / "other_output/assets/demo/guide.md"
    source.parent.mkdir(parents=True)
    source.write_text("# English guide", encoding="utf-8")
    content_manifest = tmp_path / "translation_content_manifest.json"
    classify_other_output_assets(
        merge_output,
        content_manifest_path=content_manifest,
    )

    build_bundle_staging(
        [str(merge_output / "patchouli_output"), str(merge_output)],
        str(tmp_path / "staging"),
        content_manifest_path=str(content_manifest),
    )

    manifest = json.loads(
        (tmp_path / "translation_output_manifest.json").read_text(encoding="utf-8")
    )
    pending = [
        record for record in manifest["records"] if record["status"] == "pending"
    ]
    assert len(pending) == 1
    assert pending[0]["reason"] == "english_text_queued_for_guide_translation"


def test_pipeline_manifest_path_is_shared_by_merge_and_bundle(tmp_path: Path):
    """正式輸出層級不同時，打包仍要讀到合併階段的 provenance manifest。"""
    output_root = tmp_path / "output"
    merge_output = output_root / "locale_sort" / "_整理輸出"
    source = merge_output / "other_output" / "assets" / "demo" / "guide.md"
    source.parent.mkdir(parents=True)
    source.write_text("# English guide", encoding="utf-8")
    content_manifest = output_root / "translation_content_manifest.json"

    records = classify_other_output_assets(
        merge_output,
        content_manifest_path=content_manifest,
    )
    assert records[0]["status"] == "pending"
    assert content_manifest.is_file()

    staging = output_root / "_打包暫存"
    build_bundle_staging(
        [str(merge_output)],
        str(staging),
        content_manifest_path=content_manifest,
    )

    manifest = json.loads(
        (output_root / "translation_output_manifest.json").read_text(encoding="utf-8")
    )
    assert any(
        record["status"] == "pending"
        and record["reason"] == "english_text_queued_for_guide_translation"
        for record in manifest["records"]
    )


def test_content_manifest_accumulates_across_merge_stages(tmp_path: Path):
    """Dual mode 的多個 merge 階段不得互相覆蓋 provenance。"""
    merge_output = tmp_path / "merge"
    content_manifest = tmp_path / "translation_content_manifest.json"

    first = merge_output / "other_output/assets/demo/first.md"
    first.parent.mkdir(parents=True)
    first.write_text("# 中文指南", encoding="utf-8")
    first_records = classify_other_output_assets(
        merge_output,
        content_manifest_path=content_manifest,
    )
    assert first_records[0]["reason"] == "localized_text_preserved"

    second = merge_output / "other_output/assets/demo/second.md"
    second.write_text("# English guide", encoding="utf-8")
    classify_other_output_assets(
        merge_output,
        content_manifest_path=content_manifest,
    )

    manifest = json.loads(content_manifest.read_text(encoding="utf-8"))
    reasons = {record["reason"] for record in manifest["records"]}
    assert "localized_text_preserved" in reasons
    assert "english_text_queued_for_guide_translation" in reasons
