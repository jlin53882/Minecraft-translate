"""Patchouli 語意分類、book context 與 template 解析的回歸測試。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from translation_tool.core.patchouli_semantics import (
    DISPLAY_NONTRANSLATABLE,
    LITERAL_TEXT,
    STRUCTURAL,
    TRANSLATION_REFERENCE,
    UNKNOWN,
    context_for_path,
    context_from_archive_documents,
    context_from_metadata,
    iter_patchouli_semantics,
    patchouli_translatable_items,
)
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.cleanup import _text_hash, entry_cleanup_category
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.scanner import ScanOptions, scan_jar


def _by_path(document: object, *, context=None):
    return {
        item.path: item
        for item in iter_patchouli_semantics(
            document,
            Path("assets/demo/patchouli_books/manual/en_us/entries/test.json"),
            context=context,
        )
    }


def test_known_page_separates_display_text_from_structure():
    document = {
        "type": "patchouli:text",
        "title": "Getting Started",
        "text": "Use the book to learn the basics.",
        "anchor": "getting_started",
        "recipe": "demo:recipe",
        "multiblock": {"pattern": [["A"]], "mapping": {"A": "minecraft:stone"}},
    }
    items = _by_path(document)

    assert items["title"].kind == LITERAL_TEXT
    assert items["text"].kind == LITERAL_TEXT
    assert items["anchor"].kind == STRUCTURAL
    assert items["recipe"].kind == STRUCTURAL
    assert items["multiblock.pattern[0][0]"].kind == STRUCTURAL
    assert items["multiblock.mapping.A"].kind == STRUCTURAL


def test_known_page_unknown_fields_stay_conservative_with_source_context():
    context = context_from_metadata(
        "entry.json", source_available=True, source_fingerprint="entry"
    )
    items = _by_path(
        {
            "type": "patchouli:text",
            "custom_metadata": "Example Internal Description",
        },
        context=context,
    )

    assert items["custom_metadata"].kind == UNKNOWN
    assert not items["custom_metadata"].ai_allowed
    assert not items["custom_metadata"].writer_allowed


def test_unknown_custom_page_does_not_inherit_global_display_fields():
    context = context_from_metadata(
        "entry.json",
        source_available=True,
        source_fingerprint="entry",
        resolution_error="template_missing",
    )
    items = _by_path(
        {"type": "demo:custom_page", "name": "Some Internal Name"},
        context=context,
    )

    assert items["name"].kind == UNKNOWN
    assert not items["name"].ai_allowed


def test_i18n_reference_is_not_sent_to_ai():
    context = context_from_metadata(
        "book.json", book_i18n=True, source_available=True, source_fingerprint="book"
    )
    document = {"name": "demo.book.title", "landing_text": "Welcome to the book"}

    items = _by_path(document, context=context)
    assert items["name"].kind == TRANSLATION_REFERENCE
    assert items["landing_text"].kind == LITERAL_TEXT
    output = patchouli_translatable_items(document, "book.json", context=context)
    assert len(output) == 1
    assert output[0]["path"] == "landing_text"
    assert output[0]["semantic_kind"] == LITERAL_TEXT
    assert output[0]["semantic_reason"] == "known_display_field"
    assert output[0]["semantic_evidence"] == [
        "field:landing_text",
        "page_type:book",
    ]
    assert output[0]["source_available"] is True
    assert output[0]["classifier_version"] == "patchouli-semantic-v1"
    assert output[0]["source_fingerprint"]
    assert output[0]["context_fingerprint"] == items["landing_text"].context_fingerprint


def test_i18n_reference_accepts_case_preserved_patchouli_keys():
    context = context_from_metadata(
        "book.json", book_i18n=True, source_available=True, source_fingerprint="book"
    )
    item = _by_path(
        {"name": "booklet.demo.indexEntry.getting_started"}, context=context
    )["name"]
    assert item.kind == TRANSLATION_REFERENCE
    assert item.reason == "book_i18n_translation_key"


def test_directory_context_links_data_book_to_assets_entry(tmp_path):
    book_root = tmp_path / "data" / "demo" / "patchouli_books" / "manual"
    entry_path = (
        tmp_path
        / "assets"
        / "demo"
        / "patchouli_books"
        / "manual"
        / "en_us"
        / "entries"
        / "start.json"
    )
    book_root.mkdir(parents=True)
    entry_path.parent.mkdir(parents=True)
    (book_root / "book.json").write_text(json.dumps({"i18n": True}), encoding="utf-8")
    entry_path.write_text(
        json.dumps(
            {
                "name": "demo.book.start",
                "pages": [{"type": "patchouli:text", "text": "Hello"}],
            }
        ),
        encoding="utf-8",
    )

    # The public path resolver must locate the sibling resource root too.
    resolved = context_for_path(entry_path)
    assert resolved.source_available is True
    assert resolved.book_i18n is True
    items = _by_path(json.loads(entry_path.read_text()), context=resolved)
    assert items["name"].kind == TRANSLATION_REFERENCE
    assert items["pages[0].text"].kind == LITERAL_TEXT


def test_archive_context_links_data_book_to_assets_entry(tmp_path):
    jar = tmp_path / "resource-pack.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr(
            "data/demo/patchouli_books/manual/book.json",
            json.dumps({"i18n": True}),
        )
        archive.writestr(
            "assets/demo/patchouli_books/manual/en_us/entries/start.json",
            json.dumps(
                {
                    "name": "demo.book.start",
                    "pages": [{"type": "patchouli:text", "text": "Hello"}],
                }
            ),
        )

    result = scan_jar(
        jar,
        ScanOptions(version="1.21.1", include_patchouli=True),
        dir_names=("patchouli_books", "book", "manual", "guidebook"),
    )

    assert result.error == ""
    rows = {item.key: item for item in result.items}
    text_key = next(key for key in rows if key.endswith("#pages[0].text"))
    assert not any(key.endswith("#name") for key in rows)
    assert rows[text_key].en_us == "Hello"


def test_template_variable_resolves_custom_page_display_field():
    context = context_from_archive_documents(
        "assets/demo/patchouli_books/manual/en_us/entries/test.json",
        book_json={"i18n": False},
        templates={
            "templates/custom_page.json": {
                "processor": "demo.Processor",
                "components": [
                    {"type": "patchouli:text", "text": "#body"},
                    {"type": "patchouli:item", "item": "#icon"},
                ],
            }
        },
    )
    document = {
        "type": "custom_page",
        "template": "custom_page",
        "body": "This is visible custom page prose.",
        "icon": "minecraft:stone",
        "processor": "demo.Processor",
    }

    items = _by_path(document, context=context)
    assert items["body"].kind == LITERAL_TEXT
    assert items["icon"].kind == STRUCTURAL
    assert items["processor"].kind == STRUCTURAL


def test_unresolved_custom_page_is_conservative():
    document = {
        "type": "demo:unknown_page",
        "body": "This field may be rendered by a custom processor.",
        "resource": "demo:thing",
    }
    items = _by_path(
        document,
        context=context_from_metadata(
            "entry.json", source_available=True, resolution_error="template_missing"
        ),
    )

    assert items["body"].kind == UNKNOWN
    assert items["resource"].kind == UNKNOWN
    assert not items["body"].ai_allowed
    assert not items["body"].cleanup_delete_allowed


def test_symbol_only_display_value_is_not_auto_translated_or_deleted():
    document = {"type": "patchouli:text", "text": "← →"}
    item = _by_path(document)["text"]
    assert item.kind == DISPLAY_NONTRANSLATABLE
    assert not item.ai_allowed
    assert not item.cleanup_delete_allowed


def test_cleanup_uses_semantic_result_and_keeps_unknown_values():
    document = {
        "type": "demo:unknown_page",
        "pattern": [["A"]],
        "custom": "A processor may interpret this value.",
    }
    items = _by_path(
        document,
        context=context_from_metadata(
            "entry.json", source_available=True, resolution_error="template_missing"
        ),
    )
    assert (
        entry_cleanup_category(
            "patchouli",
            "b/x.json#pattern[0][0]",
            "A",
            semantic=items["pattern[0][0]"],
        )
        == "not_text"
    )
    assert (
        entry_cleanup_category(
            "patchouli",
            "b/x.json#custom",
            document["custom"],
            semantic=items["custom"],
        )
        is None
    )


def test_legacy_cleanup_requires_high_confidence_key_evidence():
    assert (
        entry_cleanup_category(
            "patchouli",
            "b/x.json#pages[0].anchor",
            "getting_started",
            conservative=True,
        )
        is None
    )
    assert (
        entry_cleanup_category(
            "patchouli",
            "b/x.json#pages[0].multiblock.pattern[0]",
            "ABA",
            conservative=True,
        )
        == "multiblock"
    )
    assert (
        entry_cleanup_category(
            "patchouli",
            "b/x.json#pages[0].multiblock.mapping.A",
            "minecraft:stone",
            conservative=True,
        )
        == "not_text"
    )


def test_old_patchouli_report_cannot_delete_without_source_context(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(
        "1.21.1",
        [ScanItem("patchouli", "demo", "book#pages[0].anchor", "getting_started")],
    )
    entry_id, value = db._one("SELECT id, en_us FROM entry")
    report = tmp_path / "legacy.json"
    report.write_text(
        json.dumps(
            {
                "format": "mod-db-cleanup-v2",
                "mode": "rule",
                "version": "1.21.1",
                "db_id": db._ensure_db_id(),
                "items": [
                    {
                        "id": entry_id,
                        "delete": True,
                        "category": "placeholder",
                        "kind": "patchouli",
                        "mod": "demo",
                        "key": "book#pages[0].anchor",
                        "en_us_hash": _text_hash(value),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = db.preview_cleanup(report)
    assert plan.deletable == 0
    assert plan.skipped["目前規則判為正常內容"] == 1
