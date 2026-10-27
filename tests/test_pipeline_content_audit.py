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
        record["reason"] == "unsupported_other_output_format"
        for record in records
    )


def test_bundle_manifest_records_emitted_pending_and_excluded(tmp_path: Path):
    merge_output = tmp_path / "merge"
    (merge_output / "assets/demo/lang").mkdir(parents=True)
    (merge_output / "assets/demo/lang/zh_tw.json").write_text(
        '{"demo.key": "示例"}', encoding="utf-8"
    )
    (merge_output / "data/demo/advancements/start.json").parent.mkdir(
        parents=True
    )
    (merge_output / "data/demo/advancements/start.json").write_text(
        '{"display": {"title": {"text": "Literal"}}}', encoding="utf-8"
    )
    (merge_output / "patchouli_output/待翻譯/assets/demo/_en_us").mkdir(
        parents=True
    )
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
