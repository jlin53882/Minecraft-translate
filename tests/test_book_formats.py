"""回歸測試：不同 Minecraft 書籍格式共用同一份安全格式契約。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from translation_tool.core.book_formats import (
    FORMAT_ETERNAL_STARLIGHT,
    FORMAT_MODONOMICON,
    FORMAT_NEUTRAL_PAGES,
    FORMAT_ORACLE_INDEX,
    FORMAT_STANDARD_PATCHOULI,
    resolve_book_format,
)
from translation_tool.core.translatable_extractor import extract_translatables
from translation_tool.translation_db.scanner import ScanOptions, scan_jar


def _neutral_document() -> dict:
    return {
        "title": "herbsandharvest.book.title",
        "pages": [
            {
                "type": "standard",
                "summary": "The Pantry Update adds useful food items.",
                "target_page": 2,
                "elements": [
                    {
                        "type": "image",
                        "image_path": "herbsandharvest:textures/gui/book.png",
                        "caption": "A basket full of fresh ingredients.",
                    },
                    {
                        "type": "text",
                        "literal_text": "Use the basket to store your harvest.",
                    },
                ],
            }
        ],
    }


def test_neutral_pages_profile_extracts_safe_fields_only():
    path = "assets/herbsandharvest/books/chapters/foods.json"
    resolved = resolve_book_format(path, _neutral_document(), dir_names=())
    assert resolved.name == FORMAT_NEUTRAL_PAGES

    items = extract_translatables(_neutral_document(), Path(path))
    assert {item["path"] for item in items} == {
        "pages[0].summary",
        "pages[0].elements[0].caption",
        "pages[0].elements[1].literal_text",
    }
    assert all("image_path" not in item["path"] for item in items)
    assert all("target_page" not in item["path"] for item in items)


def test_neutral_pages_identity_matches_existing_database_contract(tmp_path: Path):
    jar = tmp_path / "herbs.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr(
            "assets/herbsandharvest/books/chapters/foods.json",
            json.dumps(_neutral_document()),
        )

    result = scan_jar(
        jar,
        ScanOptions(version="1.21.1"),
        dir_names=(),
    )
    keys = {item.key for item in result.items}
    assert "books/chapters/foods.json#pages[0].summary" in keys
    assert "books/chapters/foods.json#pages[0].elements[1].literal_text" in keys
    assert "books/chapters/foods.json#pages[0].elements[0].image_path" not in keys


def test_known_custom_formats_are_not_generic_patchouli_or_json():
    assert resolve_book_format(
        "assets/eternal_starlight/eternal_starlight/books/main.json",
        {"pages": [{"type": "text", "summary": "Do not guess this schema."}]},
    ).name == FORMAT_ETERNAL_STARLIGHT
    assert resolve_book_format(
        "data/example/modonomicon/books/book.json",
        {"pages": [{"type": "text", "summary": "Do not guess this schema."}]},
    ).name == FORMAT_MODONOMICON
    assert resolve_book_format(
        "assets/example/oracle_index/books/index.mdx",
    ).name == FORMAT_ORACLE_INDEX


def test_standard_patchouli_and_known_non_book_paths_keep_their_boundaries():
    assert resolve_book_format(
        "assets/demo/patchouli_books/manual/en_us/entries/start.json"
    ).name == FORMAT_STANDARD_PATCHOULI
    assert resolve_book_format(
        "assets/demo/models/block/start.json",
        {"pages": [{"type": "text", "summary": "Not a book."}]},
    ).supported is False


def test_invalid_custom_book_is_skipped_and_reported(tmp_path: Path):
    jar = tmp_path / "invalid.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr(
            "assets/herbsandharvest/books/chapters/herbs.json",
            '{"pages": []}\n{"extra": "document"}',
        )
        archive.writestr(
            "assets/herbsandharvest/books/chapters/foods.json",
            json.dumps(_neutral_document()),
        )

    result = scan_jar(jar, ScanOptions(version="1.21.1"), dir_names=())
    assert result.error == ""
    assert any(path.endswith("herbs.json") for path in result.invalid_books)
    assert any(item.key.endswith("foods.json#pages[0].summary") for item in result.items)
