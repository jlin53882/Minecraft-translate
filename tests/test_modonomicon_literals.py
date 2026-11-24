"""Modonomicon 書籍 JSON 內的字面英文：抽取規則、掃描與提取都要把它們補成「鍵 = 原文」。"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from translation_tool.core.jar_processor_extract import extract_from_jar_impl
from translation_tool.core.modonomicon_literals import (
    book_mod_id,
    collect_mod_literals,
    extract_literals,
    is_book_json,
    is_literal_english,
    main_lang_mod_id,
)
from translation_tool.translation_db.scanner import ScanOptions, scan_jar

MOD = "vitae"
EN_KEY = "item.vitae.orb"
BOOK = f"data/{MOD}/modonomicon/books/guide/entries/altar/rune.json"
RICH = "First line [#](0000ff)blue[#]\nsecond line"


def _book() -> dict:
    return {
        "name": "Slime of Vitae",
        "category": "vitae:altar",
        "icon": "vitae:orb",
        "pages": [
            {"type": "modonomicon:text", "title": "vitae.book.title", "text": RICH},
            {"type": "modonomicon:multiblock", "multiblock_name": "Blood Altar"},
            {"type": "modonomicon:text", "text": "100% pure"},
        ],
    }


def _jar(tmp_path: Path, book: dict | None = None) -> Path:
    jar = tmp_path / "vitae.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(
            f"assets/{MOD}/lang/en_us.json",
            json.dumps({EN_KEY: "Blood Orb", "vitae.book.title": "Guide"}),
        )
        zf.writestr(BOOK, json.dumps(book or _book()))
    return jar


def test_path_helpers() -> None:
    assert is_book_json(BOOK)
    assert not is_book_json(f"assets/{MOD}/lang/en_us.json")
    assert book_mod_id(BOOK) == MOD
    assert main_lang_mod_id(f"assets/{MOD}/lang/en_us.json") == MOD
    assert main_lang_mod_id(f"assets/{MOD}/lang/sub/en_us.json") is None


def test_literal_rules() -> None:
    keys = {"vitae.book.title"}
    assert is_literal_english("Slime of Vitae", keys)
    assert not is_literal_english("vitae.book.title", keys)  # 鍵引用
    assert not is_literal_english("vitae:orb", keys)  # 資源位置
    assert not is_literal_english("100% pure", keys)  # 含 %
    assert not is_literal_english("   ", keys)
    assert not is_literal_english("1234", keys)


def test_extract_only_text_fields_and_keeps_markup() -> None:
    found = extract_literals(_book(), {"vitae.book.title"})
    assert list(found) == ["Slime of Vitae", RICH, "Blood Altar"]
    assert all(k == v for k, v in found.items())
    assert "100% pure" not in found
    assert "vitae:altar" not in found  # category 不是文字欄位


def test_collect_skips_existing_keys_and_unreadable_books() -> None:
    names = [BOOK, f"data/{MOD}/modonomicon/books/guide/bad.json", "other/x.json"]

    def read(name: str):
        return _book() if name == BOOK else None

    got = collect_mod_literals(names, read, MOD, {"Blood Altar": "x"})
    assert "Blood Altar" not in got
    assert "Slime of Vitae" in got


def test_scan_adds_literals_as_lang_items(tmp_path: Path) -> None:
    result = scan_jar(_jar(tmp_path), ScanOptions(version="1.21.1"), dir_names=())
    keys = {i.key for i in result.items if i.kind == "lang"}
    assert {EN_KEY, "Slime of Vitae", RICH, "Blood Altar"} <= keys
    rich = next(i for i in result.items if i.key == RICH)
    assert rich.en_us == RICH  # 換行與標記逐位元保留


def test_scan_without_books_is_unchanged(tmp_path: Path) -> None:
    jar = tmp_path / "plain.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(f"assets/{MOD}/lang/en_us.json", json.dumps({EN_KEY: "Blood Orb"}))
    result = scan_jar(jar, ScanOptions(version="1.21.1"), dir_names=())
    assert {i.key for i in result.items} == {EN_KEY}


def test_extract_writes_literals_into_en_us(tmp_path: Path) -> None:
    jar = _jar(tmp_path)
    out = tmp_path / "out"
    regex = re.compile(r"^assets/[^/]+/lang/en_us\.json$")
    extract_from_jar_impl(str(jar), str(out), regex)
    written = json.loads(
        (out / "assets" / MOD / "lang" / "en_us.json").read_text(encoding="utf-8")
    )
    assert written[EN_KEY] == "Blood Orb"
    assert written["Slime of Vitae"] == "Slime of Vitae"
    assert written[RICH] == RICH


def test_extract_without_literals_keeps_original_bytes(tmp_path: Path) -> None:
    jar = tmp_path / "plain.jar"
    raw = json.dumps({EN_KEY: "Blood Orb"}, indent=4)
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(f"assets/{MOD}/lang/en_us.json", raw)
    out = tmp_path / "out"
    regex = re.compile(r"^assets/[^/]+/lang/en_us\.json$")
    extract_from_jar_impl(str(jar), str(out), regex)
    assert (out / "assets" / MOD / "lang" / "en_us.json").read_text(
        encoding="utf-8"
    ) == raw
