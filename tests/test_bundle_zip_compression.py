"""打包 ZIP 的壓縮等級（#165）：降到 6 不得改變解壓後的內容。"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from translation_tool.core import output_bundler
from translation_tool.core.output_bundler import bundle_outputs_generator


def _make_tree(root: Path) -> None:
    for i in range(6):
        lang = root / f"assets/mod{i}/lang/zh_tw.json"
        lang.parent.mkdir(parents=True, exist_ok=True)
        lang.write_text(
            json.dumps(
                {
                    f"mod{i}.k{k}": f"譯文 {i} {k} " + "字" * (k % 40)
                    for k in range(400)
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tex = root / f"assets/mod{i}/textures/t.png"
        tex.parent.mkdir(parents=True, exist_ok=True)
        tex.write_bytes(os.urandom(1500))
    (root / "pack.mcmeta").write_text('{"pack": {"pack_format": 15}}', encoding="utf-8")


def _bundle(src: Path, zip_path: Path) -> None:
    list(bundle_outputs_generator(str(src), str(zip_path), description="t"))


def _contents(zip_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.testzip() is None, "ZIP 必須完整可解壓"
        return {name: zf.read(name) for name in zf.namelist()}


def test_default_level_is_six():
    assert output_bundler.ZIP_COMPRESS_LEVEL == 6


def test_zip_is_created_with_the_configured_level_and_deflate(tmp_path, monkeypatch):
    src = tmp_path / "src"
    _make_tree(src)
    seen: list[dict] = []
    real = zipfile.ZipFile

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(output_bundler.zipfile, "ZipFile", spy)

    _bundle(src, tmp_path / "out.zip")

    assert seen and seen[0]["compresslevel"] == output_bundler.ZIP_COMPRESS_LEVEL
    assert seen[0]["compression"] == zipfile.ZIP_DEFLATED


def test_contents_and_entry_order_match_the_previous_level_9(tmp_path, monkeypatch):
    """降低壓縮等級只改壓縮位元組；項目名稱、順序與解壓後的內容必須與 level 9 逐位元組相同。"""
    src = tmp_path / "src"
    _make_tree(src)

    monkeypatch.setattr(output_bundler, "ZIP_COMPRESS_LEVEL", 9)
    _bundle(src, tmp_path / "level9.zip")
    monkeypatch.setattr(output_bundler, "ZIP_COMPRESS_LEVEL", 6)
    _bundle(src, tmp_path / "level6.zip")

    old, new = _contents(tmp_path / "level9.zip"), _contents(tmp_path / "level6.zip")
    assert list(new) == list(old)  # 項目與順序
    assert new == old  # 內容逐位元組相同
    assert "pack.mcmeta" in new


def test_every_entry_is_still_deflated(tmp_path):
    src = tmp_path / "src"
    _make_tree(src)
    _bundle(src, tmp_path / "out.zip")

    with zipfile.ZipFile(tmp_path / "out.zip") as zf:
        assert {i.compress_type for i in zf.infolist()} == {zipfile.ZIP_DEFLATED}


@pytest.mark.parametrize("level", [1, 6, 9])
def test_any_valid_level_produces_a_complete_archive(tmp_path, monkeypatch, level):
    src = tmp_path / "src"
    _make_tree(src)
    monkeypatch.setattr(output_bundler, "ZIP_COMPRESS_LEVEL", level)

    _bundle(src, tmp_path / f"l{level}.zip")

    names = set(_contents(tmp_path / f"l{level}.zip"))
    expected = {
        f"assets/mod{i}/{part}"
        for i in range(6)
        for part in ("lang/zh_tw.json", "textures/t.png")
    }
    assert expected <= names and "pack.mcmeta" in names
