from __future__ import annotations

import zipfile
from pathlib import Path

import orjson

from translation_tool.core import lang_merge_content


def test_patch_localized_content_json_converts_zh_cn_json_to_pretty_zh_tw(tmp_path: Path) -> None:
    zip_path = tmp_path / "fixture.zip"
    out_path = tmp_path / "out" / "assets" / "demo" / "docs" / "zh_tw.extra.json"

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "assets/demo/docs/zh_cn.extra.json",
            orjson.dumps({"title": "简体内容", "body": "Only English"}),
        )

    with zipfile.ZipFile(zip_path, "r") as zf:
        result = lang_merge_content._patch_localized_content_json(
            zf,
            "assets/demo/docs/zh_cn.extra.json",
            str(out_path),
            rules=[],
            log_prefix="[test]",
            output_dir=str(tmp_path / "out"),
        )

    assert result["success"] is True
    payload = orjson.loads(out_path.read_bytes())
    assert payload == {"title": "簡體內容", "body": "Only English"}


def test_patch_localized_content_json_quarantines_invalid_json(tmp_path: Path, monkeypatch) -> None:
    zip_path = tmp_path / "fixture.zip"
    calls: list[dict] = []

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("assets/demo/docs/zh_cn.extra.json", "{not-json")

    monkeypatch.setattr(
        lang_merge_content,
        "quarantine_copy_from_zip",
        lambda **kwargs: calls.append(kwargs),
    )

    with zipfile.ZipFile(zip_path, "r") as zf:
        result = lang_merge_content._patch_localized_content_json(
            zf,
            "assets/demo/docs/zh_cn.extra.json",
            str(tmp_path / "out" / "assets" / "demo" / "docs" / "zh_tw.extra.json"),
            rules=[],
            log_prefix="[test]",
            output_dir=str(tmp_path / "out"),
        )

    assert result == {"success": True, "pending_count": 0}
    assert len(calls) == 1
    assert calls[0]["zip_path"] == "assets/demo/docs/zh_cn.extra.json"
