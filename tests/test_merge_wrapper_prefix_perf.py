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


def test_detect_content_wrapper_prefix_respects_caller_casing():
    """helper 保留呼叫端傳入的大小寫;呼叫端必須傳原始大小寫的檔名。

    小寫版的輸入只是用來把 contract 寫清楚 (helper 不會自行轉換大小寫),
    不代表它是正確的 production 輸入:input_path 保留原始大小寫,
    用小寫前綴 "mypack/" 去 startswith 會比對失敗、不會剝離。
    """
    names = [
        "MyPack/assets/foo/models/a.json",
        "MyPack/assets/foo/lang/zh_cn.json",
    ]
    assert detect_content_wrapper_prefix(names) == "MyPack/"

    lower_names = [n.lower() for n in names]
    assert detect_content_wrapper_prefix(lower_names) == "mypack/"


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
        (base / "models" / "m.json").write_text(
            json.dumps({"parent": "minecraft:block/cube_all"}), encoding="utf-8"
        )


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


# ── 混合大小寫包裝資料夾:內容檔輸出路徑 ─────────────────────────────────────
# 非本地化內容檔 (例如 models/m.json) 由 process_content_or_copy_file_impl
# 寫到 other_output_dir / _strip(input_path),所以正確位置是
# other_output/assets/mod0/models/m.json,而非 other_output/MyPack/...


def _assert_mixed_case_outputs(out: Path) -> None:
    for i in range(3):
        assert (
            out / "lang_output" / "assets" / f"mod{i}" / "lang" / "zh_tw.json"
        ).exists()
        assert (
            out / "other_output" / "assets" / f"mod{i}" / "models" / "m.json"
        ).exists()
        assert not (
            out / "other_output" / "MyPack" / "assets" / f"mod{i}" / "models" / "m.json"
        ).exists()
    assert not (out / "lang_output" / "MyPack").exists()
    assert not (out / "other_output" / "MyPack").exists()


def test_zip_mixed_case_wrapper_strips_content_output(tmp_path, monkeypatch):
    src = tmp_path / "src"
    _make_pack(src)
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src).as_posix())
    _forbid_per_task_scan(monkeypatch)
    out = tmp_path / "out"

    updates = list(
        merge_zhcn_to_zhtw_from_zip(str(zip_path), str(out), only_process_lang=False)
    )

    assert not any(u.get("error") for u in updates)
    _assert_mixed_case_outputs(out)


def test_folder_mixed_case_wrapper_strips_content_output(tmp_path, monkeypatch):
    src = tmp_path / "in"
    _make_pack(src)
    _forbid_per_task_scan(monkeypatch)
    out = tmp_path / "out"

    updates = list(
        merge_zhcn_to_zhtw_from_folder(str(src), str(out), only_process_lang=False)
    )

    assert not any(u.get("error") for u in updates)
    _assert_mixed_case_outputs(out)


def _run_content_copy(tmp_path: Path, **kwargs) -> Path:
    from translation_tool.core.lang_merge_content import _process_content_or_copy_file
    from translation_tool.core.lang_merge_io import FolderReader

    src = tmp_path / "in"
    _make_pack(src, mods=1)
    out = tmp_path / "out"
    result = _process_content_or_copy_file(
        FolderReader(str(src)),
        "MyPack/assets/mod0/models/m.json",
        [],
        str(out),
        other_output_dir=str(out / "other_output"),
        **kwargs,
    )
    assert result.get("success")
    return out / "other_output"


def test_content_copy_without_wrapper_prefix_falls_back_to_self_detect(tmp_path):
    """向後相容:未傳 wrapper_prefix (_UNSET) 時,worker 自行偵測並剝離。"""
    other = _run_content_copy(tmp_path)

    assert (other / "assets" / "mod0" / "models" / "m.json").exists()
    assert not (other / "MyPack").exists()


def test_content_copy_explicit_none_wrapper_prefix_means_no_strip(tmp_path):
    """wrapper_prefix=None 表示呼叫端已偵測且確認沒有包裝前綴,不應自行再偵測。"""
    other = _run_content_copy(tmp_path, wrapper_prefix=None)

    assert (other / "MyPack" / "assets" / "mod0" / "models" / "m.json").exists()
