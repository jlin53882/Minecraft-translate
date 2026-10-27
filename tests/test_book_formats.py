"""回歸測試：不同 Minecraft 書籍格式共用同一份安全格式契約。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from translation_tool.core import lang_merge_content, lang_merger
from translation_tool.core.book_formats import (
    FORMAT_ETERNAL_STARLIGHT,
    FORMAT_MODONOMICON,
    FORMAT_NEUTRAL_PAGES,
    FORMAT_ORACLE_INDEX,
    FORMAT_STANDARD_PATCHOULI,
    resolve_book_format,
)
from translation_tool.core.guide_translation import find_guide_files
from translation_tool.core.neutral_book_json import load_neutral_book_json
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


def test_neutral_book_recovery_normalizes_only_confirmed_trailing_tail():
    path = "assets/herbsandharvest/books/chapters/grapes.json"
    malformed = json.dumps(_neutral_document(), ensure_ascii=False) + "\n]}\n"

    loaded = load_neutral_book_json(malformed, path)

    assert loaded.repaired
    assert loaded.repaired_tail == "]}"
    assert loaded.data == _neutral_document()


def test_neutral_book_recovery_rejects_unrelated_trailing_json():
    path = "assets/herbsandharvest/books/chapters/herbs.json"
    malformed = json.dumps(_neutral_document()) + "\n{}"

    with pytest.raises(json.JSONDecodeError):
        load_neutral_book_json(malformed, path)


def test_neutral_book_recovery_allows_whitespace_between_repair_tokens():
    path = "assets/herbsandharvest/books/chapters/herbs.json"
    malformed = json.dumps(_neutral_document()) + "\n]\r\n}\n"

    loaded = load_neutral_book_json(malformed, path)

    assert loaded.repaired
    assert loaded.repaired_tail == "]\r\n}"
    assert loaded.data == _neutral_document()


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
    assert (
        resolve_book_format(
            "assets/eternal_starlight/eternal_starlight/books/main.json",
            {"pages": [{"type": "text", "summary": "Do not guess this schema."}]},
        ).name
        == FORMAT_ETERNAL_STARLIGHT
    )
    assert (
        resolve_book_format(
            "data/example/modonomicon/books/book.json",
            {"pages": [{"type": "text", "summary": "Do not guess this schema."}]},
        ).name
        == FORMAT_MODONOMICON
    )
    assert (
        resolve_book_format(
            "assets/example/oracle_index/books/index.mdx",
        ).name
        == FORMAT_ORACLE_INDEX
    )
    assert (
        resolve_book_format(
            "assets/oracle_index/books/index.mdx",
        ).name
        == FORMAT_ORACLE_INDEX
    )


def test_unsupported_custom_docs_are_not_detected_as_guides(tmp_path: Path):
    oracle = tmp_path / "assets" / "oracle_index" / "books" / "docs" / "index.mdx"
    oracle.parent.mkdir(parents=True)
    oracle.write_text(
        "# Oracle docs\n\nThis is a long documentation paragraph that must not become a game guide.\n",
        encoding="utf-8",
    )
    ordinary = tmp_path / "assets" / "things" / "lavender" / "entries" / "almanac.md"
    ordinary.parent.mkdir(parents=True)
    ordinary.write_text(
        "# Almanac\n\nThis is a long in-game guide paragraph that should remain discoverable.\n",
        encoding="utf-8",
    )

    found = {
        path.relative_to(tmp_path).as_posix() for path in find_guide_files(tmp_path)
    }
    assert "assets/oracle_index/books/docs/index.mdx" not in found
    assert "assets/things/lavender/entries/almanac.md" in found


def test_unsupported_mdx_is_ignored_by_archive_sniffer(tmp_path: Path):
    jar = tmp_path / "oracle.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr(
            "assets/oracle_index/books/docs/index.mdx",
            "# Oracle docs\n\nThis is a long documentation paragraph that must not become a game guide.\n",
        )

    result = scan_jar(jar, ScanOptions(version="1.21.1"), dir_names=())
    assert result.error == ""
    assert not any(item.kind == "guide" for item in result.items)


def test_invalid_non_book_json_is_not_reported_as_invalid_book(tmp_path: Path):
    jar = tmp_path / "broken-display.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr(
            "assets/tacz/custom/tacz_default_gun/assets/tacz/display/guns/ai_awp_display.json",
            '{"broken": true\n',
        )

    result = scan_jar(jar, ScanOptions(version="1.20.1"), dir_names=())
    assert result.error == ""
    assert result.invalid_books == []
    assert result.items == []


def test_standard_patchouli_and_known_non_book_paths_keep_their_boundaries():
    assert (
        resolve_book_format(
            "assets/demo/patchouli_books/manual/en_us/entries/start.json"
        ).name
        == FORMAT_STANDARD_PATCHOULI
    )
    assert (
        resolve_book_format(
            "assets/demo/models/block/start.json",
            {"pages": [{"type": "text", "summary": "Not a book."}]},
        ).supported
        is False
    )


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
    assert any(
        item.key.endswith("foods.json#pages[0].summary") for item in result.items
    )


def test_recoverable_neutral_book_is_detected_by_archive_sniffer(tmp_path: Path):
    jar = tmp_path / "recoverable.jar"
    malformed = json.dumps(_neutral_document(), ensure_ascii=False) + "\n]}"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("assets/herbsandharvest/books/chapters/grapes.json", malformed)

    result = scan_jar(jar, ScanOptions(version="1.21.1"), dir_names=())

    assert result.error == ""
    assert result.invalid_books == []
    assert any(
        item.key.endswith("grapes.json#pages[0].summary") for item in result.items
    )


def _merge_config() -> dict:
    return {
        "replace_rules_path": "replace_rules.json",
        "translator": {"parallel_execution_workers": 1},
        "lang_merger": {
            "pending_folder_name": "待翻譯",
            "pending_organized_folder_name": "待翻譯整理",
            "filtered_pending_min_count": 1,
            "quarantine_folder_name": "skipped_json",
        },
        "lm_translator": {"patchouli": {"dir_names": ["patchouli_books"]}},
    }


def _mark_text(value, _rules):
    if isinstance(value, str):
        return f"TW:{value}"
    if isinstance(value, list):
        return [_mark_text(item, _rules) for item in value]
    if isinstance(value, dict):
        return {key: _mark_text(item, _rules) for key, item in value.items()}
    return value


def test_neutral_pages_merge_routes_pending_and_preserves_structure(
    tmp_path: Path, monkeypatch
):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    source = (
        input_dir / "assets" / "herbsandharvest" / "books" / "chapters" / "foods.json"
    )
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(_neutral_document()), encoding="utf-8")

    monkeypatch.setattr(lang_merger, "load_config", _merge_config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])
    monkeypatch.setattr(lang_merge_content, "load_config", _merge_config)
    monkeypatch.setattr(lang_merge_content, "recursive_translate_dict", _mark_text)

    updates = list(
        lang_merger.merge_zhcn_to_zhtw_from_folder(
            str(input_dir), str(output_dir), only_process_lang=False
        )
    )
    assert all(not update.get("error", False) for update in updates)

    target = (
        output_dir
        / "patchouli_output"
        / "待翻譯"
        / "assets"
        / "herbsandharvest"
        / "books"
        / "chapters"
        / "foods.json"
    )
    assert target.exists()
    merged = json.loads(target.read_text(encoding="utf-8"))
    page = merged["pages"][0]
    assert page["summary"].startswith("TW:")
    assert page["elements"][0]["caption"].startswith("TW:")
    assert page["elements"][1]["literal_text"].startswith("TW:")
    assert page["elements"][0]["image_path"] == "herbsandharvest:textures/gui/book.png"
    assert page["target_page"] == 2
    assert not (
        output_dir / "other_output" / "assets" / "herbsandharvest" / "books"
    ).exists()


def test_invalid_neutral_pages_merge_is_quarantined(tmp_path: Path, monkeypatch):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    source = (
        input_dir / "assets" / "herbsandharvest" / "books" / "chapters" / "herbs.json"
    )
    source.parent.mkdir(parents=True)
    source.write_text('{"pages": []}\n{"extra": "document"}', encoding="utf-8")

    monkeypatch.setattr(lang_merger, "load_config", _merge_config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])
    monkeypatch.setattr(lang_merge_content, "load_config", _merge_config)

    updates = list(
        lang_merger.merge_zhcn_to_zhtw_from_folder(
            str(input_dir), str(output_dir), only_process_lang=False
        )
    )
    assert all(not update.get("error", False) for update in updates)
    assert not (output_dir / "patchouli_output" / "待翻譯").exists() or not list(
        (output_dir / "patchouli_output" / "待翻譯").rglob("herbs.json")
    )
    assert list((output_dir / "errordata_output").rglob("herbs.json"))
