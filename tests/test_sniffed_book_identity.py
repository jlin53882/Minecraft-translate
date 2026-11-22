"""無語言資料夾的手冊 JSON：DB 掃描端與翻譯端必須算出同一個身分。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from translation_tool.core.lm_translator_scan import (
    extract_items_parallel,
    scan_translatable_files,
)
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
