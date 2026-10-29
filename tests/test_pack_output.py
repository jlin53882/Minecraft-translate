"""ModDB 生效譯文輸出到 KubeJS／FTB overlay 的 round-trip 測試。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ftb_snbt_lib as snbt
import pytest

from tests.test_pack_sources import _write_fixture
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.pack_output import (
    PackOutputError,
    export_pack_overlay,
)
from translation_tool.translation_db.pack_sources import (
    pack_scope_for_root,
    scan_pack_sources,
)
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator


def test_effective_db_translations_write_to_format_specific_overlay(tmp_path: Path):
    _write_fixture(tmp_path)
    (tmp_path / "config/ftbquests/quests/lang/en_us/chapter.snbt").write_text(
        '{demo.quest.lang.title:"A quest language title"}', encoding="utf-8"
    )
    literal_quest = (
        tmp_path / "config" / "ftbquests" / "quests" / "chapters" / "a" / "literal.snbt"
    )
    literal_quest.write_text(
        '{id:"literal",title:"Literal quest title"}', encoding="utf-8"
    )
    db = TranslationDB(tmp_path / "translation.db")
    try:
        list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=True),
                workers=1,
            )
        )
        scope = scan_pack_sources(tmp_path, "1.21.1").pack_scope

        asset_id = db._one(
            "SELECT e.id FROM entry e JOIN entry_origin eo ON eo.entry_id=e.id "
            "WHERE e.key=? AND eo.pack_scope=? LIMIT 1",
            ("item.demo.asset", scope),
        )[0]
        script_id = db._one(
            "SELECT id FROM entry WHERE kind='kubejs_script' "
            "AND key LIKE '%tooltip:demo:item:1' LIMIT 1"
        )[0]
        quest_id = db._one(
            "SELECT id FROM entry WHERE kind='ftb_quests' "
            "AND key LIKE '%|root.title|id:literal' LIMIT 1"
        )[0]
        lang_id = db._one("SELECT id FROM entry WHERE kind='ftb_quest_lang' LIMIT 1")[0]
        db.save_manual(asset_id, "資產翻譯", propagate=False)
        db.save_manual(script_id, "工具提示翻譯", propagate=False)
        db.save_manual(quest_id, "任務標題翻譯", propagate=False)
        db.save_manual(lang_id, "任務語系翻譯", propagate=False)

        output = tmp_path.parent / f"{tmp_path.name}-overlay"
        report = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)
        assert report.ok
        assert report.entries_applied >= 4

        lang = json.loads(
            (output / "kubejs/assets/demo/lang/zh_tw.json").read_text(encoding="utf-8")
        )
        assert lang["item.demo.asset"] == "資產翻譯"

        script = (output / "kubejs/client_scripts/a/main.js").read_text(
            encoding="utf-8"
        )
        assert "工具提示翻譯" in script
        assert "Tooltip text" not in script

        quest = snbt.load(
            (output / "config/ftbquests/quests/chapters/a/literal.snbt").open(
                encoding="utf-8"
            )
        )
        assert str(quest["title"]) == "任務標題翻譯"
        assert not (output / "config/ftbquests/quests/chapters/b/chapter.snbt").exists()

        lang_path = output / "config/ftbquests/quests/lang/zh_tw/chapter.snbt"
        with lang_path.open(encoding="utf-8") as stream:
            lang_snbt = snbt.load(stream)
        assert str(lang_snbt["demo.quest.lang.title"]) == "任務語系翻譯"
    finally:
        db.close()


def test_export_rejects_a_changed_source_before_writing(tmp_path: Path):
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=True),
                workers=1,
            )
        )
        scope = scan_pack_sources(tmp_path, "1.21.1").pack_scope
        entry_id = db._one(
            "SELECT e.id FROM entry e JOIN entry_origin eo ON eo.entry_id=e.id "
            "WHERE e.kind='kubejs_script' "
            "AND eo.source_path='client_scripts/a/main.js' LIMIT 1"
        )[0]
        db.save_manual(entry_id, "變更後不可套用", propagate=False)
        source = tmp_path / "kubejs/client_scripts/a/main.js"
        source.write_text(
            source.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8"
        )

        output = tmp_path.parent / f"{tmp_path.name}-overlay"
        try:
            export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)
        except RuntimeError as exc:
            assert "stale" in str(exc)
        else:
            raise AssertionError("changed source must reject output")
        assert not output.exists()
    finally:
        db.close()


def test_export_rejects_conflicting_hashes_for_same_source(tmp_path: Path):
    source = tmp_path / "kubejs" / "client_scripts" / "main.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "ItemEvents.tooltip(event => { event.add('demo:item', Text.of('Text')); });\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    scope = pack_scope_for_root(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    "kubejs_script",
                    f"kubejs@{scope}",
                    "same.key",
                    "Text",
                    source_path="client_scripts/main.js",
                    source_selector="tooltip[0]",
                    source_hash=digest,
                    source_format="kubejs-js",
                    pack_scope=scope,
                ),
                ScanItem(
                    "kubejs_script",
                    f"kubejs@{scope}",
                    "same.key",
                    "Text",
                    source_path="client_scripts/main.js",
                    source_selector="tooltip[1]",
                    source_hash="stale-hash",
                    source_format="kubejs-js",
                    pack_scope=scope,
                ),
            ],
        )
        entry_id = db._one("SELECT id FROM entry WHERE key='same.key'")[0]
        db.save_manual(entry_id, "翻譯", propagate=False)
        with pytest.raises(PackOutputError, match="來源 hash 不一致"):
            export_pack_overlay(
                db,
                tmp_path,
                "1.21.1",
                tmp_path.parent / "conflict-overlay",
                pack_scope=scope,
            )
    finally:
        db.close()


def test_export_rejects_unmatched_kubejs_selector_without_publishing(tmp_path: Path):
    source = tmp_path / "kubejs" / "client_scripts" / "main.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "ItemEvents.tooltip(event => { event.add('demo:item', Text.of('Text')); });\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    scope = pack_scope_for_root(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    output = tmp_path.parent / "unmatched-overlay"
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    "kubejs_script",
                    f"kubejs@{scope}",
                    "not-in-source",
                    "Text",
                    source_path="client_scripts/main.js",
                    source_selector="missing",
                    source_hash=digest,
                    source_format="kubejs-js",
                    pack_scope=scope,
                )
            ],
        )
        entry_id = db._one("SELECT id FROM entry WHERE key='not-in-source'")[0]
        db.save_manual(entry_id, "不應靜默輸出", propagate=False)
        with pytest.raises(PackOutputError, match="KubeJS selector 未命中"):
            export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)
        assert not output.exists()
    finally:
        db.close()


def test_export_maps_single_file_ftb_locale_to_zh_tw(tmp_path: Path):
    lang = tmp_path / "config" / "ftbquests" / "quests" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.snbt").write_text('{quest.title:"English title"}', encoding="utf-8")
    (lang / "zh_cn.snbt").write_text('{quest.title:"簡體標題"}', encoding="utf-8")
    db = TranslationDB(tmp_path / "translation.db")
    try:
        list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=True),
                workers=1,
            )
        )
        scope = scan_pack_sources(tmp_path, "1.21.1").pack_scope
        entry_id = db._one(
            "SELECT e.id FROM entry e JOIN entry_origin eo ON eo.entry_id=e.id "
            "WHERE e.kind='ftb_quest_lang' AND eo.pack_scope=? LIMIT 1",
            (scope,),
        )[0]
        db.save_manual(entry_id, "繁體標題", propagate=False)
        output = tmp_path.parent / "single-file-overlay"
        report = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)
        assert report.ok
        assert report.entries_verified == 1
        translated = snbt.load(
            (output / "config/ftbquests/quests/lang/zh_tw.snbt").open(encoding="utf-8")
        )
        assert str(translated["quest.title"]) == "繁體標題"
    finally:
        db.close()


def test_export_preserves_existing_ftb_zh_tw_keys_not_managed_by_db(tmp_path: Path):
    quests = tmp_path / "config" / "ftbquests" / "quests" / "lang"
    quests.mkdir(parents=True)
    (quests / "en_us.snbt").write_text(
        '{quest.title:"English title"}', encoding="utf-8"
    )
    (quests / "zh_tw.snbt").write_text(
        '{quest.title:"舊繁中標題",unmanaged.key:"既有未管理內容"}',
        encoding="utf-8",
    )
    db = TranslationDB(tmp_path / "translation.db")
    try:
        list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=True),
                workers=1,
            )
        )
        scope = scan_pack_sources(tmp_path, "1.21.1").pack_scope
        entry_id = db._one(
            "SELECT e.id FROM entry e JOIN entry_origin eo ON eo.entry_id=e.id "
            "WHERE e.kind='ftb_quest_lang' AND e.key LIKE '%quest.title' "
            "AND eo.pack_scope=? LIMIT 1",
            (scope,),
        )[0]
        db.save_manual(entry_id, "新的繁中標題", propagate=False)

        output = tmp_path.parent / "ftb-preserve-overlay"
        report = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)
        assert report.ok
        translated = snbt.load(
            (output / "config/ftbquests/quests/lang/zh_tw.snbt").open(encoding="utf-8")
        )
        assert str(translated["quest.title"]) == "新的繁中標題"
        assert str(translated["unmanaged.key"]) == "既有未管理內容"
    finally:
        db.close()
