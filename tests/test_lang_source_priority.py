"""同一語系同時有 .json 和 .lang 時，語系合併以 .json 為準。"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from translation_tool.core import lang_merge_content, lang_merge_pipeline, lang_merger
from translation_tool.core.lang_merge_io import register_lang_source


def _register(*paths: str) -> dict[str, str]:
    slots: dict[str, str] = {}
    for path in paths:
        register_lang_source(slots, path)
    return slots


@pytest.mark.parametrize(
    "order",
    [
        ("a/lang/zh_cn.json", "a/lang/zh_cn.lang"),
        ("a/lang/zh_cn.lang", "a/lang/zh_cn.json"),
        ("a/lang/zh_cn.lang", "a/lang/zh_cn.json"),
        ("a/lang/zh_cn.json", "a/lang/zh_cn.lang"),
    ],
)
def test_json_wins_over_lang_in_any_order(order):
    assert _register(*order)["zh_cn"].lower().endswith(".json")


def test_lang_is_used_when_there_is_no_json():
    assert _register("a/lang/en_us.lang") == {"en_us": "a/lang/en_us.lang"}


def test_same_suffix_keeps_the_later_path():
    assert _register("a/lang/zh_tw.json", "b/lang/zh_tw.json") == {
        "zh_tw": "b/lang/zh_tw.json"
    }


def test_other_languages_and_files_are_ignored():
    assert _register("a/lang/ja_jp.json", "a/lang/notes.json") == {}


def test_each_language_gets_its_own_slot():
    slots = _register("a/lang/en_us.json", "a/lang/zh_cn.lang", "a/lang/zh_tw.json")
    assert set(slots) == {"en_us", "zh_cn", "zh_tw"}


def _config() -> dict:
    return {
        "replace_rules_path": "replace_rules.json",
        "translator": {"parallel_execution_workers": 1},
        "lang_merger": {
            "pending_folder_name": "待翻譯",
            "pending_organized_folder_name": "待翻譯整理需翻譯",
            "filtered_pending_min_count": 1,
            "quarantine_folder_name": "skipped_json",
        },
        "lm_translator": {"patchouli": {"dir_names": ["patchouli_books"]}},
    }


def _tag(value, _rules):
    if isinstance(value, dict):
        return {k: _tag(v, _rules) for k, v in value.items()}
    return f"TW:{value}" if isinstance(value, str) else value


def _merge(monkeypatch, input_dir: Path, output_dir: Path) -> list[dict]:
    monkeypatch.setattr(lang_merger, "load_config", _config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])
    monkeypatch.setattr(lang_merge_content, "load_config", _config)
    for module in (lang_merge_content, lang_merge_pipeline):
        monkeypatch.setattr(module, "recursive_translate_dict", _tag)
        monkeypatch.setattr(module, "apply_replace_rules", _tag)
    return list(
        lang_merger.merge_zhcn_to_zhtw_from_folder(
            str(input_dir), str(output_dir), only_process_lang=True
        )
    )


def test_mod_with_json_and_lang_outputs_json_from_the_json_sources(
    tmp_path, monkeypatch
):
    lang_dir = tmp_path / "in" / "assets" / "demo" / "lang"
    lang_dir.mkdir(parents=True)
    (lang_dir / "en_us.json").write_bytes(orjson.dumps({"item.demo.a": "A"}))
    (lang_dir / "zh_cn.json").write_bytes(orjson.dumps({"item.demo.a": "简体甲"}))
    # 舊版 .lang 與 .json 並存：key 是 1.12 時代的寫法，且排序上會晚於 .json
    (lang_dir / "en_us.lang").write_text("item.demo.legacy=Legacy\n", encoding="utf-8")
    (lang_dir / "zh_cn.lang").write_text("item.demo.legacy=旧版\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    updates = _merge(monkeypatch, tmp_path / "in", out)

    assert all(not u.get("error") for u in updates)
    lang_out = out / "lang_output" / "assets" / "demo" / "lang"
    assert not list(lang_out.glob("*.lang"))
    data = orjson.loads((lang_out / "zh_tw.json").read_bytes())
    assert data == {"item.demo.a": "TW:简体甲"}


def test_mod_with_only_lang_files_still_produces_translations(tmp_path, monkeypatch):
    lang_dir = tmp_path / "in" / "assets" / "old" / "lang"
    lang_dir.mkdir(parents=True)
    (lang_dir / "en_us.lang").write_text("item.old.a=Apple\n", encoding="utf-8")
    (lang_dir / "zh_cn.lang").write_text("item.old.a=苹果\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    _merge(monkeypatch, tmp_path / "in", out)

    result = out / "lang_output" / "assets" / "old" / "lang" / "zh_tw.lang"
    assert result.is_file() and "苹果" in result.read_text(encoding="utf-8")
