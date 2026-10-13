"""輸出 ZIP 路徑必須是檔案：資料夾、空白、空檔名設定都要被擋下或補成預設檔名。"""

from __future__ import annotations

import os
import zipfile

import pytest

from translation_tool.core import output_bundler
from translation_tool.core.output_bundler import (
    DEFAULT_OUTPUT_ZIP_NAME,
    bundle_outputs_generator,
    normalize_output_zip_name,
)


@pytest.fixture
def src(tmp_path):
    root = tmp_path / "src"
    (root / "lang").mkdir(parents=True)
    (root / "lang" / "zh_tw.json").write_text('{"k": "v"}', encoding="utf-8")
    return root


def _errors(updates):
    return [u for u in updates if u.get("error")]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_OUTPUT_ZIP_NAME),
        ("", DEFAULT_OUTPUT_ZIP_NAME),
        ("   ", DEFAULT_OUTPUT_ZIP_NAME),
        (123, DEFAULT_OUTPUT_ZIP_NAME),
        ("a/b.zip", DEFAULT_OUTPUT_ZIP_NAME),
        ("a\\b.zip", DEFAULT_OUTPUT_ZIP_NAME),
        ("pack.zip", "pack.zip"),
        ("Pack.ZIP", "Pack.ZIP"),
        (" pack ", "pack.zip"),
    ],
)
def test_normalize_output_zip_name(value, expected):
    assert normalize_output_zip_name(value) == expected


def test_directory_path_is_rejected_without_touching_anything(src, tmp_path):
    target = tmp_path / "existing_dir"
    target.mkdir()
    updates = list(bundle_outputs_generator(str(src), str(target)))
    assert len(_errors(updates)) == 1
    assert "資料夾" in updates[-1]["log"] and "檔名" in updates[-1]["log"]
    assert target.is_dir() and not list(target.iterdir())
    assert not os.path.exists(str(target) + ".tmp")


@pytest.mark.parametrize("suffix", ["/", "\\"])
def test_trailing_separator_path_is_rejected(src, tmp_path, suffix):
    updates = list(bundle_outputs_generator(str(src), str(tmp_path / "out") + suffix))
    assert len(_errors(updates)) == 1 and "資料夾" in updates[-1]["log"]


def test_blank_output_path_is_rejected(src):
    updates = list(bundle_outputs_generator(str(src), "  "))
    assert len(_errors(updates)) == 1 and "不可空白" in updates[-1]["log"]


def test_normal_file_path_still_builds(src, tmp_path):
    out = tmp_path / "out.zip"
    updates = list(bundle_outputs_generator(str(src), str(out)))
    assert not _errors(updates)
    with zipfile.ZipFile(out) as zf:
        assert "pack.mcmeta" in zf.namelist()


def test_final_replace_retries_transient_permission_error(src, tmp_path, monkeypatch):
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(source, target):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "存取被拒。")
        return real_replace(source, target)

    monkeypatch.setattr(output_bundler.os, "replace", flaky)
    monkeypatch.setattr(
        "translation_tool.utils.file_replace.time.sleep", lambda s: None
    )
    out = tmp_path / "out.zip"
    updates = list(bundle_outputs_generator(str(src), str(out)))
    assert not _errors(updates) and out.is_file()
    assert calls["n"] >= 3
