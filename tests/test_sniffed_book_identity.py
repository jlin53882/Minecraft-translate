"""無語言資料夾的手冊 JSON：DB 掃描端與翻譯端必須算出同一個身分。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from translation_tool.core.lm_translator_scan import (
    extract_items_parallel,
    scan_translatable_files,
)
from translation_tool.translation_db import TranslationDB, WriteBackBuffer
from translation_tool.translation_db.identity import classify_sniffed_book_file
from translation_tool.translation_db.resolver import item_identity
from translation_tool.translation_db.scanner import ScanOptions, scan_jar
from translation_tool.translation_db.schema import KIND_PATCHOULI

_BOOK = json.dumps(
    {
        "pages": [
            {"type": "standard", "summary": "This custom book explains the harvest."}
        ]
    }
)
_MEMBER = "assets/demo/books/chapters/foods.json"


def _lm_items(root: Path) -> list[dict]:
    _content, _lang, files = scan_translatable_files(root)
    batches = list(
        extract_items_parallel(files=files, export_lang=False, work_thread=1)
    )
    return batches[-1][1]


def test_scan_and_translate_share_identity_for_book_without_lang_dir(tmp_path: Path):
    jar = tmp_path / "demo.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(_MEMBER, _BOOK)
    scanned = {
        (i.kind, i.mod_id, i.key, i.en_us)
        for i in scan_jar(jar, ScanOptions(version="1.21.1")).items
    }
    assert scanned, "掃描端應匯入這份手冊"

    root = tmp_path / "extracted"
    target = root / _MEMBER
    target.parent.mkdir(parents=True)
    target.write_text(_BOOK, encoding="utf-8")
    items = [i for i in _lm_items(root) if i.get("cache_type") == "patchouli"]
    assert items
    assert {item_identity(i, root) for i in items} == scanned


def test_extracted_prefix_is_not_part_of_identity(tmp_path: Path):
    """內嵌 jar 提取時多一層 ``<名稱>_extracted/``，身分仍要相同。"""
    direct = classify_sniffed_book_file(tmp_path / _MEMBER, tmp_path)
    nested = classify_sniffed_book_file(
        tmp_path / "inner_extracted" / _MEMBER, tmp_path
    )
    assert direct == nested
    assert direct is not None
    assert direct.kind == KIND_PATCHOULI
    assert (
        direct.item_key("pages[0].summary")
        == "books/chapters/foods.json#pages[0].summary"
    )


def test_other_language_or_non_json_is_not_identified(tmp_path: Path):
    assert (
        classify_sniffed_book_file(
            tmp_path / "assets/demo/books/ja_jp/a.json", tmp_path
        )
        is None
    )
    assert (
        classify_sniffed_book_file(tmp_path / "assets/demo/books/a/b.md", tmp_path)
        is None
    )


_STD = json.dumps(
    {
        "name": "Book Name Here",
        "landing_text": "A long landing text for the book.",
        "pages": [{"type": "patchouli:text", "text": "Welcome to the demo manual."}],
    }
)


def _item(path: Path) -> dict:
    return {
        "file": str(path),
        "path": "name",
        "source_text": "Hello World",
        "cache_type": "patchouli",
    }


def test_scan_excluded_paths_are_not_identified(tmp_path: Path):
    """掃描端不收的路徑（book.json、規格資料夾、lang）翻譯端也不算身分。"""
    for rel in (
        "assets/m/patchouli_books/b/book.json",
        "assets/m/books/book.json",
        "assets/m/textures/book/x.json",
        "assets/m/models/foo/x.json",
    ):
        assert classify_sniffed_book_file(tmp_path / rel, tmp_path) is None, rel
        assert item_identity(_item(tmp_path / rel), tmp_path) is None, rel


def test_extracted_data_book_resolves_like_scan(tmp_path: Path):
    """內嵌 jar 的 ``_extracted/data/.../patchouli_books/.../en_us/...`` 也要有身分。"""
    path = tmp_path / "x_extracted/data/m/patchouli_books/b/en_us/entries/a.json"
    assert item_identity(_item(path), tmp_path) == (
        KIND_PATCHOULI,
        "m",
        "patchouli_books/b/entries/a.json#name",
        "Hello World",
    )


def test_writeback_matches_scan_and_skips_book_json(tmp_path: Path):
    """jar 內的 book.json 掃描不收；翻譯端寫回也不能多出 book.json 的條目。"""
    members = {
        "assets/m/patchouli_books/b/book.json": _STD,
        "assets/m/patchouli_books/b/en_us/entries/a.json": _STD,
    }
    jar = tmp_path / "m.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    scanned = {
        (i.kind, i.mod_id, i.key)
        for i in scan_jar(jar, ScanOptions(version="1.21.1")).items
    }
    assert scanned

    root = tmp_path / "out"
    for name, text in members.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    db = TranslationDB(tmp_path / "t.db")
    try:
        buffer = WriteBackBuffer(db, "1.21.1", root)
        for item in _lm_items(root):
            buffer.add(item, "譯文")
        buffer.flush()
        written = {tuple(r[:3]) for r in db._q("SELECT kind, mod_id, key FROM entry")}
    finally:
        db.close()
    assert written == scanned
    assert not any("book.json" in key for _k, _m, key in written)


def test_lang_file_is_not_a_sniffed_book(tmp_path: Path):
    """語言檔走 lang 身分，不會被當成內容辨識的手冊。"""
    path = tmp_path / "assets/m/lang/en_us.json"
    assert classify_sniffed_book_file(path, tmp_path) is None
    assert item_identity(_item(path), tmp_path)[0] == "lang"
