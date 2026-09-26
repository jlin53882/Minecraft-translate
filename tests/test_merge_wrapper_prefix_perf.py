"""包裝前綴偵測只算一次 (2026-09-26 效能修正)。

原本 _process_single_mod 每個 mod、process_content_or_copy_file_impl 每個內容檔
都會掃一次全部檔名來偵測包裝前綴,整體為 O(mod 數 × 檔案數) 與 O(檔案數²)。
改為 lang_merger 呼叫端算一次再用 wrapper_prefix 傳入。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from translation_tool.core import lang_merge_content_copy, lang_merge_pipeline
from translation_tool.core.lang_merge_content_copy import detect_content_wrapper_prefix
from translation_tool.core.lang_merge_pipeline import detect_mod_wrapper_prefix
from translation_tool.core.lang_merger import (
    merge_zhcn_to_zhtw_from_folder,
    merge_zhcn_to_zhtw_from_zip,
)

_STANDARD = {"assets", "book", "patchouli_books", "resources"}


def _old_mod_prefix(names):
    """修正前 _process_single_mod 內的原始邏輯 (逐字保留作為對照)。"""
    _wp = None
    if names:
        _tops = set(
            n.replace("\\", "/").split("/")[0]
            for n in names
            if n.replace("\\", "/").split("/")[0]
        )
        if len(_tops) == 1:
            _candidate = list(_tops)[0]
            if _candidate not in _STANDARD:
                _wp = _candidate + "/"
    return _wp


def _old_content_prefix(names):
    """修正前 process_content_or_copy_file_impl 內的原始邏輯 (逐字保留作為對照)。"""
    _wp = None
    if names:
        _tops = set(
            n.replace("\\", "/").split("/")[0]
            for n in names
            if n.replace("\\", "/").split("/")[0]
        )
        if len(_tops) == 1:
            _candidate = list(_tops)[0] + "/"
            if names[0].startswith(_candidate):
                _wp = _candidate
    return _wp


_CASES = [
    [],
    ["MyPack/assets/foo/lang/zh_cn.json", "MyPack/assets/foo/x.png"],
    ["mypack/assets/foo/lang/zh_cn.json"],
    ["assets/foo/lang/zh_cn.json", "assets/bar/lang/en_us.json"],
    ["resources/foo/lang/zh_cn.json"],
    ["A_extracted/a/lang/zh_cn.json", "B_extracted/b/lang/zh_cn.json"],
    ["MyPack\\assets\\foo\\lang\\zh_cn.json", "MyPack/assets/foo/x.png"],
    ["pack.mcmeta"],
    ["/abs/lead/slash.json", "abs/other.json"],
]


@pytest.mark.parametrize("names", _CASES)
def test_detect_helpers_match_original_logic(names):
    assert detect_mod_wrapper_prefix(names) == _old_mod_prefix(names)
    assert detect_content_wrapper_prefix(names) == _old_content_prefix(names)


def _make_pack(root: Path, mods: int = 3) -> None:
    for i in range(mods):
        base = root / "MyPack" / "assets" / f"mod{i}"
        (base / "lang").mkdir(parents=True)
        (base / "lang" / "zh_cn.json").write_text(
            json.dumps({f"k{i}": "苹果"}, ensure_ascii=False), encoding="utf-8"
        )
        (base / "lang" / "en_us.json").write_text(
            json.dumps({f"k{i}": "Apple", f"e{i}": "Only"}), encoding="utf-8"
        )
        (base / "models").mkdir()
        (base / "models" / "m.json").write_text("{}", encoding="utf-8")


def _forbid_per_task_scan(monkeypatch):
    """worker 內若又自行掃描檔名 (未收到 wrapper_prefix) 就直接失敗。"""

    def _boom(_names):
        raise AssertionError("worker 不應再自行掃描全部檔名偵測包裝前綴")

    monkeypatch.setattr(lang_merge_pipeline, "detect_mod_wrapper_prefix", _boom)
    monkeypatch.setattr(lang_merge_content_copy, "detect_content_wrapper_prefix", _boom)


@pytest.mark.parametrize("only_lang", [True, False])
def test_folder_merge_detects_prefix_once_and_strips(tmp_path, monkeypatch, only_lang):
    src = tmp_path / "in"
    _make_pack(src)
    _forbid_per_task_scan(monkeypatch)
    out = tmp_path / "out"

    updates = list(merge_zhcn_to_zhtw_from_folder(str(src), str(out), only_lang))

    assert not any(u.get("error") for u in updates)
    for i in range(3):
        assert (
            out / "lang_output" / "assets" / f"mod{i}" / "lang" / "zh_tw.json"
        ).exists()
    assert not (out / "lang_output" / "MyPack").exists()


@pytest.mark.parametrize("only_lang", [True, False])
def test_zip_merge_detects_prefix_once_and_strips(tmp_path, monkeypatch, only_lang):
    src = tmp_path / "src"
    _make_pack(src)
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src).as_posix())
    _forbid_per_task_scan(monkeypatch)
    out = tmp_path / "out"

    updates = list(merge_zhcn_to_zhtw_from_zip(str(zip_path), str(out), only_lang))

    assert not any(u.get("error") for u in updates)
    for i in range(3):
        assert (
            out / "lang_output" / "assets" / f"mod{i}" / "lang" / "zh_tw.json"
        ).exists()
    assert not (out / "lang_output" / "MyPack").exists()
