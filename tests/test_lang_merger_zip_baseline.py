from __future__ import annotations

import zipfile
from pathlib import Path

import orjson

from translation_tool.core import lang_merger


PENDING_DIR = "待翻譯"
FILTERED_DIR = "待翻譯整理需翻譯"


def _write_zip_fixture(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "assets/demo/lang/en_us.json",
            orjson.dumps(
                {
                    "item.demo.title": "English Title",
                    "item.demo.pending": "Only English",
                }
            ),
        )
        zf.writestr(
            "assets/demo/lang/zh_cn.json",
            orjson.dumps(
                {
                    "item.demo.title": "简体说明",
                    "item.demo.pending": "Only English",
                }
            ),
        )
        zf.writestr(
            "assets/demo/docs/zh_cn.extra.json",
            orjson.dumps({"title": "简体内容", "body": "Only English"}),
        )


def _fake_config() -> dict:
    return {
        "replace_rules_path": "replace_rules.json",
        "translator": {
            "parallel_execution_workers": 1,
        },
        "lang_merger": {
            "pending_folder_name": PENDING_DIR,
            "pending_organized_folder_name": FILTERED_DIR,
            "filtered_pending_min_count": 1,
            "quarantine_folder_name": "skipped_json",
        },
        "lm_translator": {
            "patchouli": {"dir_names": ["patchouli_books"]},
        },
    }


def _fake_recursive_translate_dict(value, _rules):
    if isinstance(value, dict):
        return {k: _fake_recursive_translate_dict(v, _rules) for k, v in value.items()}
    if isinstance(value, list):
        return [_fake_recursive_translate_dict(v, _rules) for v in value]
    if isinstance(value, str):
        return f"TW:{value}"
    return value


def _fake_apply_replace_rules(value, _rules):
    if isinstance(value, str):
        return f"TW:{value}"
    return value


def test_merge_zip_baseline_fixture_outputs_are_stable(tmp_path: Path, monkeypatch) -> None:
    zip_path = tmp_path / "fixture.zip"
    output_dir = tmp_path / "out"
    _write_zip_fixture(zip_path)

    monkeypatch.setattr(lang_merger, "load_config", _fake_config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])
    monkeypatch.setattr(lang_merger, "recursive_translate_dict", _fake_recursive_translate_dict)
    monkeypatch.setattr(lang_merger, "apply_replace_rules", _fake_apply_replace_rules)

    updates = list(lang_merger.merge_zhcn_to_zhtw_from_zip(str(zip_path), str(output_dir), False))

    assert updates[0]["log"].startswith("分析 ZIP 檔案")
    assert all(not update.get("error", False) for update in updates)
    assert updates[-1]["progress"] == 1.0

    zh_tw_path = output_dir / "assets" / "demo" / "lang" / "zh_tw.json"
    pending_path = output_dir / PENDING_DIR / "assets" / "demo" / "lang" / "en_us.json"
    filtered_pending_path = output_dir / FILTERED_DIR / "assets" / "demo" / "lang" / "en_us.json"
    localized_json_path = output_dir / "assets" / "demo" / "docs" / "zh_tw.extra.json"

    assert zh_tw_path.exists()
    assert pending_path.exists()
    assert filtered_pending_path.exists()
    assert localized_json_path.exists()

    zh_tw_data = orjson.loads(zh_tw_path.read_bytes())
    pending_data = orjson.loads(pending_path.read_bytes())
    filtered_pending_data = orjson.loads(filtered_pending_path.read_bytes())
    localized_data = orjson.loads(localized_json_path.read_bytes())

    assert zh_tw_data == {"item.demo.title": "TW:简体说明"}
    assert pending_data == {"item.demo.pending": "Only English"}
    assert filtered_pending_data == {"item.demo.pending": "Only English"}
    assert localized_data == {"title": "TW:简体内容", "body": "TW:Only English"}

    all_json_outputs = {p.relative_to(output_dir).as_posix() for p in output_dir.rglob("*.json")}
    assert all_json_outputs == {
        "assets/demo/docs/zh_tw.extra.json",
        "assets/demo/lang/zh_tw.json",
        f"{FILTERED_DIR}/assets/demo/lang/en_us.json",
        f"{PENDING_DIR}/assets/demo/lang/en_us.json",
    }

    assert not any(output_dir.rglob("*.reason.txt"))
    assert not any(output_dir.rglob("*.detail.txt"))
