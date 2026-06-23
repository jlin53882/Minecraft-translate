"""lang_merger folder 模式的單元測試。

用途：測試 merge_zhcn_to_zhtw_from_folder() 輸出與 ZIP 版本一致。
"""
from __future__ import annotations

import os
from pathlib import Path

import orjson
import pytest

from translation_tool.core import (
    lang_merger,
    lang_merge_content,
    lang_merge_pipeline,
)


PENDING_DIR = "待翻譯"
FILTERED_DIR = "待翻譯整理需翻譯"


def _write_folder_fixture(folder: Path) -> None:
    """建立與 ZIP fixture 等價的資料夾結構。"""
    (folder / "assets" / "demo" / "lang").mkdir(parents=True, exist_ok=True)
    (folder / "assets" / "demo" / "docs").mkdir(parents=True, exist_ok=True)

    (folder / "assets" / "demo" / "lang" / "en_us.json").write_bytes(
        orjson.dumps({
            "item.demo.title": "English Title",
            "item.demo.pending": "Only English",
        })
    )
    (folder / "assets" / "demo" / "lang" / "zh_cn.json").write_bytes(
        orjson.dumps({
            "item.demo.title": "简体说明",
            "item.demo.pending": "Only English",
        })
    )
    (folder / "assets" / "demo" / "docs" / "zh_cn.extra.json").write_bytes(
        orjson.dumps({"title": "简体内容", "body": "Only English"})
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


def test_merge_folder_basic_outputs_stable(tmp_path: Path, monkeypatch) -> None:
    """測試資料夾模式的輸出結構與 ZIP 版本一致。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    _write_folder_fixture(input_dir)

    monkeypatch.setattr(lang_merger, "load_config", _fake_config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])
    monkeypatch.setattr(lang_merge_content, "load_config", _fake_config)
    monkeypatch.setattr(lang_merge_content, "recursive_translate_dict", _fake_recursive_translate_dict)
    monkeypatch.setattr(lang_merge_pipeline, "recursive_translate_dict", _fake_recursive_translate_dict)
    monkeypatch.setattr(lang_merge_content, "apply_replace_rules", _fake_apply_replace_rules)
    monkeypatch.setattr(lang_merge_pipeline, "apply_replace_rules", _fake_apply_replace_rules)

    updates = list(
        lang_merger.merge_zhcn_to_zhtw_from_folder(str(input_dir), str(output_dir), only_process_lang=False)
    )

    assert updates[0]["log"].startswith("分析資料夾")
    assert all(not update.get("error", False) for update in updates)
    assert updates[-1]["progress"] == 1.0

    zh_tw_path = output_dir / "lang_output" / "assets" / "demo" / "lang" / "zh_tw.json"
    pending_path = output_dir / "lang_output" / PENDING_DIR / "assets" / "demo" / "lang" / "en_us.json"
    filtered_pending_path = output_dir / "lang_output" / FILTERED_DIR / "assets" / "demo" / "lang" / "en_us.json"
    localized_json_path = output_dir / "assets" / "demo" / "docs" / "zh_tw.extra.json"

    assert zh_tw_path.exists(), f"缺少 {zh_tw_path}"
    assert pending_path.exists(), f"缺少 {pending_path}"
    assert filtered_pending_path.exists(), f"缺少 {filtered_pending_path}"
    assert localized_json_path.exists(), f"缺少 {localized_json_path}"

    zh_tw_data = orjson.loads(zh_tw_path.read_bytes())
    pending_data = orjson.loads(pending_path.read_bytes())
    filtered_pending_data = orjson.loads(filtered_pending_path.read_bytes())
    localized_data = orjson.loads(localized_json_path.read_bytes())

    assert zh_tw_data == {"item.demo.title": "TW:简体说明"}
    assert pending_data == {"item.demo.pending": "Only English"}
    assert filtered_pending_data == {"item.demo.pending": "Only English"}
    assert localized_data == {"title": "TW:简体内容", "body": "TW:Only English"}


def test_merge_folder_only_process_lang(tmp_path: Path, monkeypatch) -> None:
    """測試 only_process_lang=True 時只處理 lang 檔案。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    _write_folder_fixture(input_dir)

    monkeypatch.setattr(lang_merger, "load_config", _fake_config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])
    monkeypatch.setattr(lang_merge_content, "load_config", _fake_config)
    monkeypatch.setattr(lang_merge_content, "recursive_translate_dict", _fake_recursive_translate_dict)
    monkeypatch.setattr(lang_merge_pipeline, "recursive_translate_dict", _fake_recursive_translate_dict)
    monkeypatch.setattr(lang_merge_content, "apply_replace_rules", _fake_apply_replace_rules)
    monkeypatch.setattr(lang_merge_pipeline, "apply_replace_rules", _fake_apply_replace_rules)

    updates = list(
        lang_merger.merge_zhcn_to_zhtw_from_folder(str(input_dir), str(output_dir), only_process_lang=True)
    )

    assert all(not update.get("error", False) for update in updates)
    assert updates[-1]["progress"] == 1.0

    zh_tw_path = output_dir / "lang_output" / "assets" / "demo" / "lang" / "zh_tw.json"
    assert zh_tw_path.exists()

    localized_json_path = output_dir / "assets" / "demo" / "docs" / "zh_tw.extra.json"
    assert not localized_json_path.exists(), "only_process_lang=True 時不應產生 localized 檔案"


def test_merge_folder_missing_input_dir(tmp_path: Path, monkeypatch) -> None:
    """測試輸入資料夾不存在時的處理。"""
    input_dir = tmp_path / "nonexistent"
    output_dir = tmp_path / "out"

    monkeypatch.setattr(lang_merger, "load_config", _fake_config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])

    updates = list(
        lang_merger.merge_zhcn_to_zhtw_from_folder(str(input_dir), str(output_dir), only_process_lang=False)
    )

    assert updates[-1]["progress"] == 1.0
    assert not updates[-1].get("error", False)
