from __future__ import annotations

import json

from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.pack_sources import scan_pack_sources
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator


def _write_fixture(root):
    (root / "kubejs" / "client_scripts" / "a").mkdir(parents=True)
    (root / "kubejs" / "client_scripts" / "b").mkdir(parents=True)
    (root / "kubejs" / "client_scripts" / "a" / "main.js").write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('item.demo.name', 'Demo Name');\n"
        "});\n"
        "ItemEvents.tooltip(event => {\n"
        "  event.add('demo:item', Text.of('Tooltip text'));\n"
        "  event.add('demo:key', Text.translate('demo.key'));\n"
        "});\n",
        encoding="utf-8",
    )
    (root / "kubejs" / "client_scripts" / "b" / "main.js").write_text(
        "ItemEvents.tooltip(event => {\n"
        "  event.add('demo:other', Text.literal('Other tooltip'));\n"
        "});\n",
        encoding="utf-8",
    )
    lang_dir = root / "kubejs" / "assets" / "demo" / "lang"
    lang_dir.mkdir(parents=True)
    (lang_dir / "en_us.json").write_text(
        json.dumps(
            {
                "item.demo.asset": "Asset name",
                "demo.key": "Referenced tooltip",
                "demo.quest.title": "Referenced title",
            }
        ),
        encoding="utf-8",
    )
    (lang_dir / "zh_tw.json").write_text(
        json.dumps({"item.demo.asset": "資產名稱"}, ensure_ascii=False),
        encoding="utf-8",
    )
    quests = root / "config" / "ftbquests" / "quests"
    (quests / "chapters" / "a").mkdir(parents=True)
    (quests / "chapters" / "b").mkdir(parents=True)
    quest = '{id:"same",title:"{demo.quest.title}",description:["First","{image:x}","Second"]}'
    (quests / "chapters" / "a" / "chapter.snbt").write_text(quest, encoding="utf-8")
    (quests / "chapters" / "b" / "chapter.snbt").write_text(quest, encoding="utf-8")
    lang = quests / "lang" / "en_us"
    lang.mkdir(parents=True)
    (lang / "chapter.snbt").write_text(
        '{demo.quest.lang:"A quest language title"}', encoding="utf-8"
    )


def test_pack_sources_keep_file_identity_and_skip_dynamic_text(tmp_path):
    _write_fixture(tmp_path)

    result = scan_pack_sources(tmp_path, "1.21.1")

    script_items = [item for item in result.items if item.kind == "kubejs_script"]
    assert {item.source_path for item in script_items} == {
        "client_scripts/a/main.js",
        "client_scripts/b/main.js",
    }
    assert not any("Text.translate" in item.en_us for item in script_items)
    assert not any(
        issue["reason"] == "kubejs_lang_reference" for issue in result.issues
    )
    assert any(
        item.key == "client_scripts/a/main.js|tooltip:demo:item:1"
        for item in script_items
    )
    assert any(item.key == "item.demo.asset" for item in result.items)

    quest_items = [item for item in result.items if item.kind == "ftb_quests"]
    assert len(quest_items) == 4
    assert {item.source_path for item in quest_items} == {
        "chapters/a/chapter.snbt",
        "chapters/b/chapter.snbt",
    }
    assert any("description[0]" in item.source_selector for item in quest_items)
    assert any("description[2]" in item.source_selector for item in quest_items)
    assert [reference.target_key for reference in result.references] == [
        "demo.key",
        "demo.quest.title",
        "demo.quest.title",
    ]
    assert len(result.references) == 3
    assert all(
        reference.target_mod_id.startswith("demo@") for reference in result.references
    )


def test_moddb_scan_ingests_pack_sources_and_provenance(tmp_path):
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        updates = list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=True),
                workers=1,
            )
        )
        report = updates[-1]["report"]
        assert report.pack_items_found > 0
        assert db._one("SELECT COUNT(*) FROM entry_origin")[0] > 0
        kinds = {row[0] for row in db._q("SELECT DISTINCT kind FROM entry", ())}
        assert {"lang", "kubejs_script", "ftb_quests"} <= kinds
        entry_id = db._one("SELECT id FROM entry WHERE kind='kubejs_script' LIMIT 1")[0]
        assert db.entry_origins(entry_id)[0].source_path.endswith(".js")
        refs = db.source_references("1.21.1")
        assert len(refs) == 3
        assert {ref.status for ref in refs} == {"resolved"}
    finally:
        db.close()


def test_moddb_can_disable_pack_sources_without_fake_entries(tmp_path):
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        updates = list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=False),
                workers=1,
            )
        )
        assert updates[-1]["report"].pack_items_found == 0
        assert db._one("SELECT COUNT(*) FROM entry")[0] == 0
    finally:
        db.close()


def test_entry_origins_keep_same_source_identity_across_pack_scopes(tmp_path):
    """不同模組包的同一 key／路徑不能因共用資料庫而互相覆蓋來源。"""
    db = TranslationDB(tmp_path / "translation.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    "kubejs_script",
                    "kubejs@pack-a",
                    "same.key",
                    "Same text",
                    source_path="client_scripts/main.js",
                    source_selector="tooltip[0]",
                    source_hash="hash-a",
                    source_format="kubejs-js",
                    pack_scope="pack-a",
                ),
                ScanItem(
                    "kubejs_script",
                    "kubejs@pack-a",
                    "same.key",
                    "Same text",
                    source_path="client_scripts/main.js",
                    source_selector="tooltip[0]",
                    source_hash="hash-b",
                    source_format="kubejs-js",
                    pack_scope="pack-b",
                ),
            ],
        )
        entry_id = db._one("SELECT id FROM entry WHERE key='same.key'")[0]
        origins = db.entry_origins(entry_id)
        assert {(origin.pack_scope, origin.source_hash) for origin in origins} == {
            ("pack-a", "hash-a"),
            ("pack-b", "hash-b"),
        }
    finally:
        db.close()
