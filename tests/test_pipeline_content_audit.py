from __future__ import annotations

import json
from pathlib import Path

from app.services_impl.pipelines.bundle_service import build_bundle_staging
from translation_tool.core.pipeline_content_audit import classify_other_output_assets


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
