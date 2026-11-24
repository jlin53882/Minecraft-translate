"""Markdown 流水線（資料夾）：Oracle Index 書的頁面與 _meta.json。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_md_pipeline_moddb import _fake_runner
from translation_tool.translation_db.md_pipeline import (
    MdPipelineOptions,
    run_md_pipeline,
    scan_md_sources,
)
from translation_tool.translation_db.repository import TranslationDB

BOOK = "assets/oracle_index/books/b"
PAGE = "---\ntitle: Overview\nid: x\n---\n\nThe machine stores energy for later use.\n"
META = {"arcane": "Arcane", "empty": "", "nested": {"name": "Start", "icon": None}}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def db(tmp_path):
    handle = TranslationDB(tmp_path / "t.db")
    yield handle
    handle.close()


def _source(root: Path) -> None:
    _write(root / BOOK / "content" / "x.mdx", PAGE)
    _write(root / BOOK / "content" / "_meta.json", json.dumps(META))
    _write(
        root / BOOK / "translated" / "zh_cn" / "content" / "_meta.json",
        json.dumps({"arcane": "奥术"}, ensure_ascii=False),
    )
    _write(root / BOOK / "translated" / "ja_jp" / "content" / "x.mdx", "日本語")
    _write(root / BOOK / "data" / "recipes.json", json.dumps({"a": "Not Text"}))
    _write(root / BOOK / "assets" / "note.mdx", "A page in the assets folder.\n")


def test_scan_pairs_pages_and_meta_with_their_translations(tmp_path):
    _source(tmp_path)

    result = scan_md_sources(tmp_path)

    assert {f.rel for f in result.files} == {
        f"{BOOK}/content/x.mdx",
        f"{BOOK}/content/_meta.json",
    }
    assert {f.mod_id for f in result.files} == {"oracle_index"}
    assert {f.out_rel for f in result.files} == {
        f"{BOOK}/translated/zh_tw/content/x.mdx",
        f"{BOOK}/translated/zh_tw/content/_meta.json",
    }
    meta = {i.en_us: i.zh_cn for i in result.items if "_meta.json" in i.key}
    assert meta == {"Arcane": "奥术", "Start": ""}  # 譯文缺的鍵為空
    keys = {i.key.split("#")[0] for i in result.items}
    assert keys == {"books/b/content/x.mdx", "books/b/content/_meta.json"}
    assert not [i for i in result.issues if i["reason"] == "misaligned"]
    assert "Overview" in {i.en_us for i in result.items}  # front matter 標題


def test_publish_writes_translated_pages_and_full_meta(tmp_path, db):
    src = tmp_path / "in"
    _source(src)
    out = tmp_path / "out"
    options = MdPipelineOptions(
        version="1.21.1",
        dry_run=False,
        publish=True,
        output_root=str(out),
        translate_missing=True,
    )

    report = run_md_pipeline(db, src, options, translation_runner=_fake_runner)

    assert report.output.files_written == 2
    page = (out / BOOK / "translated" / "zh_tw" / "content" / "x.mdx").read_text(
        encoding="utf-8"
    )
    assert "title: 譯:Overview" in page and "id: x" in page
    meta = json.loads(
        (out / BOOK / "translated" / "zh_tw" / "content" / "_meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert list(meta) == list(META)  # 全部的鍵與順序
    assert meta["empty"] == ""
    assert meta["arcane"] == "譯:Arcane"
    assert meta["nested"] == {"name": "譯:Start", "icon": None}
    assert not (out / BOOK / "translated" / "zh_tw" / "data").exists()
    assert (
        json.loads((src / BOOK / "content" / "_meta.json").read_text("utf-8")) == META
    )
