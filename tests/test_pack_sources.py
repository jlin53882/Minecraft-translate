from __future__ import annotations

import json

import pytest

from translation_tool.plugins.kubejs.kubejs_tooltip_inject import inject
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.pack_sources import scan_pack_sources
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator
from translation_tool.translation_db.schema import SRC_JAR_CN


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
        assert {
            "kubejs_lang",
            "kubejs_script",
            "ftb_quests",
        } <= kinds
        entry_id = db._one("SELECT id FROM entry WHERE kind='kubejs_script' LIMIT 1")[0]
        assert db.entry_origins(entry_id)[0].source_path.endswith(".js")
        refs = db.source_references("1.21.1")
        assert len(refs) == 3
        assert {ref.status for ref in refs} == {"resolved"}
    finally:
        db.close()


def test_moddb_pack_scan_emits_source_phases_and_ingest_summary(tmp_path):
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
        logs = [update["log"] for update in updates if update.get("log")]
        report = updates[-1]["report"]

        assert any("📦 模組包來源：開始分析" in log for log in logs)
        assert any("📖 KubeJS：掃描" in log for log in logs)
        assert any("📖 FTB Quests：掃描" in log for log in logs)
        assert any("📦 模組包來源分析完成" in log for log in logs)
        assert any("📥 模組包來源匯入資料庫" in log for log in logs)
        assert report.pack_source_breakdown["kubejs"]["files_seen"] > 0
        assert report.pack_source_breakdown["ftb_quests"]["files_seen"] > 0
    finally:
        db.close()


def test_moddb_pack_scan_logs_empty_sources_instead_of_only_no_jar(tmp_path):
    (tmp_path / "kubejs").mkdir()
    (tmp_path / "config" / "ftbquests" / "quests").mkdir(parents=True)

    updates = list(
        scan_folder_generator(
            None,
            tmp_path,
            ScanOptions("1.21.1", include_pack_content=True, dry_run=True),
            workers=1,
        )
    )
    logs = [update["log"] for update in updates if update.get("log")]

    assert any("📦 模組包來源：開始分析" in log for log in logs)
    assert any("📖 KubeJS：掃描 0 個檔案" in log for log in logs)
    assert any("📖 FTB Quests：掃描 0 個檔案" in log for log in logs)
    assert any("📥 模組包來源預覽匯入統計（未寫入）" in log for log in logs)
    assert any("找不到任何 jar" in log for log in logs)


def test_moddb_pack_scan_logs_parse_failure_source_and_reason(tmp_path):
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text('{"broken":', encoding="utf-8")

    updates = list(
        scan_folder_generator(
            None,
            tmp_path,
            ScanOptions("1.21.1", include_pack_content=True, dry_run=True),
            workers=1,
        )
    )
    logs = [update["log"] for update in updates if update.get("log")]
    report = updates[-1]["report"]

    assert any("json_parse_failed" in log and "en_us.json" in log for log in logs)
    assert report.as_dict()["issue_counts"] == {"parse_failed": 1}


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


def test_kubejs_lang_supports_locales_and_three_argument_form(tmp_path):
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo', 'item.demo.machine', 'Machine');\n"
        "});\n"
        "ClientEvents.lang('zh_tw', event => {\n"
        "  event.add('demo', 'item.demo.machine', '機器');\n"
        "});\n",
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.key == "item.demo.machine"]

    assert {(item.en_us, item.zh_tw) for item in items} == {
        ("Machine", ""),
        ("", "機器"),
    }
    assert {item.kind for item in items} == {"kubejs_lang"}
    assert any(
        "ClientEvents.lang[zh_tw][demo]" in item.source_selector for item in items
    )
    assert {item.mod_id for item in items} == {"demo@" + result.pack_scope}
    assert {item.source_locale for item in items} == {"en_us"}

    db = TranslationDB(tmp_path / "kubejs-locale.db")
    try:
        db.ingest("1.21.1", result.items)
        entry_id = db._one(
            "SELECT id FROM entry WHERE kind='kubejs_lang' AND mod_id=? AND key=?",
            ("demo@" + result.pack_scope, "item.demo.machine"),
        )[0]
        assert db.source_locales_for_entries([entry_id]) == {entry_id: "en_us"}
    finally:
        db.close()

    translated = tmp_path / "translated" / "client_scripts" / "lang.json"
    translated.parent.mkdir(parents=True)
    translated.write_text(
        json.dumps(
            {
                "client_scripts/lang.js|ClientEvents.lang[en_us][demo].event.add[1]|item.demo.machine": "機器翻譯"
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    summary = inject(
        str(tmp_path / "kubejs"), str(tmp_path / "translated"), str(output)
    )
    assert summary["missing_translation_keys"] == []
    assert summary["applied_translation_keys"] == [
        "client_scripts/lang.js|ClientEvents.lang[en_us][demo].event.add[1]|item.demo.machine"
    ]
    output_text = (output / "client_scripts/lang.js").read_text(encoding="utf-8")
    assert "'demo', 'item.demo.machine', 'Machine'" in output_text
    assert "event.add('demo', 'item.demo.machine', \"機器翻譯\");" in output_text


def test_kubejs_script_locale_fallback_uses_most_common_locale(tmp_path):
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('zh_tw', event => {\n"
        "  event.add('demo', 'item.demo.machine', '機器');\n"
        "  event.add('demo', 'item.demo.machine', '機器二');\n"
        "});\n"
        "ClientEvents.lang('zh_cn', event => {\n"
        "  event.add('demo', 'item.demo.machine', '机器');\n"
        "});\n",
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.key == "item.demo.machine"]

    assert len(items) == 3
    assert {item.source_locale for item in items} == {"zh_tw"}
    assert {(item.en_us, item.zh_tw, item.zh_cn) for item in items} == {
        ("機器", "", ""),
        ("機器二", "", ""),
        ("", "", "机器"),
    }

    db = TranslationDB(tmp_path / "kubejs-fallback.db")
    try:
        db.ingest("1.21.1", result.items)
        row = db._one(
            "SELECT en_us FROM entry WHERE kind='kubejs_lang' AND mod_id=? AND key=?",
            ("demo@" + result.pack_scope, "item.demo.machine"),
        )
        assert row is not None and row[0] in {"機器", "機器二"}
    finally:
        db.close()


def test_kubejs_lang_skips_blank_values_but_keeps_occurrence_alignment(tmp_path):
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo', 'item.demo.empty', '');\n"
        "  event.add('demo', 'item.demo.machine', 'Machine');\n"
        "});\n",
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")

    items = [item for item in result.items if item.kind == "kubejs_lang"]
    assert [item.key for item in items] == ["item.demo.machine"]
    assert "event.add[2]|item.demo.machine" in items[0].source_selector
    assert len(result.stale_origins) == 1
    assert result.stale_origins[0].selector.endswith("event.add[1]|item.demo.empty")


def test_moddb_rescan_removes_legacy_blank_kubejs_origin(tmp_path):
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo', 'item.demo.machine', '');\n"
        "});\n"
        "ClientEvents.lang('zh_cn', event => {\n"
        "  event.add('demo', 'item.demo.machine', '机器');\n"
        "});\n",
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    db = TranslationDB(tmp_path / "translation.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    "kubejs_lang",
                    "demo@" + result.pack_scope,
                    "item.demo.machine",
                    "",
                    source_path="client_scripts/lang.js",
                    source_selector=(
                        "ClientEvents.lang[en_us][demo].event.add[1]|item.demo.machine"
                    ),
                    source_hash="legacy",
                    source_format="kubejs-js",
                    pack_scope=result.pack_scope,
                    source_locale="en_us",
                )
            ],
        )

        list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=True),
                workers=1,
            )
        )

        row = db._one(
            "SELECT id, en_us FROM entry WHERE kind='kubejs_lang' "
            "AND mod_id=? AND key=?",
            ("demo@" + result.pack_scope, "item.demo.machine"),
        )
        assert row is not None and row[1] == "机器"
        assert db._one(
            "SELECT COUNT(*) FROM entry_origin WHERE pack_scope=? "
            "AND source_kind='kubejs_lang' AND source_path=? "
            "AND selector LIKE '%[en_us][demo].event.add[1]|%'",
            (result.pack_scope, "client_scripts/lang.js"),
        ) == (0,)
        assert db.source_locales_for_entries([row[0]]) == {row[0]: "zh_cn"}
    finally:
        db.close()


def test_moddb_rescan_scopes_legacy_origin_cleanup_to_version(tmp_path):
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo', 'item.demo.machine', '');\n"
        "});\n"
        "ClientEvents.lang('zh_cn', event => {\n"
        "  event.add('demo', 'item.demo.machine', '机器');\n"
        "});\n",
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    db = TranslationDB(tmp_path / "translation.db")
    try:
        legacy_item = ScanItem(
            "kubejs_lang",
            "demo@" + result.pack_scope,
            "item.demo.machine",
            "",
            source_path="client_scripts/lang.js",
            source_selector=(
                "ClientEvents.lang[en_us][demo].event.add[1]|item.demo.machine"
            ),
            source_hash="legacy",
            source_format="kubejs-js",
            pack_scope=result.pack_scope,
            source_locale="en_us",
        )
        db.ingest("1.20.1", [legacy_item])
        db.ingest("1.21.1", [legacy_item])

        list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=True),
                workers=1,
            )
        )

        rows = db._q(
            "SELECT e.mc_version, COUNT(eo.id) "
            "FROM entry e LEFT JOIN entry_origin eo ON eo.entry_id=e.id "
            "AND eo.pack_scope=? AND eo.source_kind='kubejs_lang' "
            "AND eo.source_path=? AND eo.selector LIKE '%[en_us][demo].event.add[1]|%' "
            "WHERE e.kind='kubejs_lang' AND e.mod_id=? AND e.key=? "
            "GROUP BY e.mc_version ORDER BY e.mc_version",
            (
                result.pack_scope,
                "client_scripts/lang.js",
                "demo@" + result.pack_scope,
                "item.demo.machine",
            ),
        )
        assert rows == [("1.20.1", 1), ("1.21.1", 0)]
    finally:
        db.close()


@pytest.mark.parametrize("locale", ["zh_cn", "zh_tw"])
def test_kubejs_script_locale_only_fallback_is_a_translation_candidate(
    tmp_path, locale
):
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        f"ClientEvents.lang('{locale}', event => {{\n"
        "  event.add('demo', 'item.demo.machine', '机器');\n"
        "});\n",
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    item = next(item for item in result.items if item.key == "item.demo.machine")
    assert item.source_locale == locale
    assert (item.en_us, item.zh_cn, item.zh_tw) == ("机器", "", "")

    db_path = tmp_path / f"kubejs-{locale}-only.db"
    db = TranslationDB(db_path)
    try:
        db.ingest("1.21.1", result.items)
        row = db._one(
            "SELECT en_us FROM entry WHERE kind='kubejs_lang' AND mod_id=? AND key=?",
            ("demo@" + result.pack_scope, "item.demo.machine"),
        )
        assert row is not None and row[0] == "机器"
        db.close()
        db = TranslationDB(db_path)
        persisted = db._one(
            "SELECT e.en_us, eo.source_locale FROM entry e "
            "JOIN entry_origin eo ON eo.entry_id=e.id "
            "WHERE e.kind='kubejs_lang' AND e.mod_id=? AND e.key=?",
            ("demo@" + result.pack_scope, "item.demo.machine"),
        )
        assert persisted == ("机器", locale)
    finally:
        db.close()


def test_kubejs_lang_dynamic_value_keeps_scan_and_writer_occurrence_aligned(
    tmp_path,
):
    """動態值不可翻譯時，後續靜態值仍須沿用 scanner 的 occurrence。"""
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('item.demo.dynamic', computeTitle());\n"
        "  event.add('item.demo.machine', 'Advanced Machine');\n"
        "});\n",
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    item = next(item for item in result.items if item.key == "item.demo.machine")
    assert item.source_selector.endswith(
        "ClientEvents.lang[en_us][minecraft].event.add[2]|item.demo.machine"
    )
    translation_key = f"{item.source_path}|{item.source_selector}"

    translated = tmp_path / "translated" / "client_scripts" / "lang.json"
    translated.parent.mkdir(parents=True)
    translated.write_text(
        json.dumps({translation_key: "進階機器"}, ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    summary = inject(
        str(tmp_path / "kubejs"), str(tmp_path / "translated"), str(output)
    )

    output_text = (output / "client_scripts" / "lang.js").read_text(encoding="utf-8")
    assert summary["missing_translation_keys"] == []
    assert summary["applied_translation_keys"] == [translation_key]
    assert "computeTitle()" in output_text
    assert 'event.add("item.demo.machine", "進階機器");' in output_text


def test_kubejs_lang_writer_keeps_other_api_calls_untouched(tmp_path):
    source = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "// event.add('demo_a', 'item.same', 'comment');\n"
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo_a', 'item.same', 'A');\n"
        "  event.add('demo_b', 'item.same', 'B');\n"
        "});\n"
        "SomeRegistry.add('demo_a', 'item.same', 'Registry text');\n",
        encoding="utf-8",
    )
    translated = tmp_path / "translated" / "client_scripts" / "lang.json"
    translated.parent.mkdir(parents=True)
    translated.write_text(
        json.dumps(
            {
                "client_scripts/lang.js|ClientEvents.lang[en_us][demo_a].event.add[1]|item.same": "甲",
                "client_scripts/lang.js|ClientEvents.lang[en_us][demo_b].event.add[2]|item.same": "乙",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = tmp_path / "output"
    summary = inject(
        str(tmp_path / "kubejs"), str(tmp_path / "translated"), str(output)
    )
    output_text = (output / "client_scripts/lang.js").read_text(encoding="utf-8")

    assert summary["missing_translation_keys"] == []
    assert "Registry text" in output_text
    assert "comment" in output_text
    assert 'event.add("demo_a", "item.same", "甲");' in output_text
    assert 'event.add("demo_b", "item.same", "乙");' in output_text


def test_kubejs_lang_writer_does_not_fallback_for_unsupported_locale(tmp_path):
    source = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "ClientEvents.lang('de_de', event => {\n"
        "  event.add('demo', 'item.same', 'German');\n"
        "});\n",
        encoding="utf-8",
    )
    translated = tmp_path / "translated" / "client_scripts" / "lang.json"
    translated.parent.mkdir(parents=True)
    translated.write_text(
        json.dumps(
            {"client_scripts/lang.js|lang:item.same": "不應套用"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = tmp_path / "output"
    summary = inject(
        str(tmp_path / "kubejs"), str(tmp_path / "translated"), str(output)
    )
    output_text = (output / "client_scripts/lang.js").read_text(encoding="utf-8")

    assert summary["applied_translation_keys"] == []
    assert summary["missing_translation_keys"] == [
        "client_scripts/lang.js|lang:item.same"
    ]
    assert "German" in output_text
    assert "不應套用" not in output_text


def test_kubejs_lang_writer_rejects_ambiguous_existing_zh_tw_key(tmp_path):
    """多個既有繁中目標不可猜測套用，避免產生不一致 overlay。"""
    source = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo', 'item.same', 'Original');\n"
        "});\n"
        "ClientEvents.lang('zh_tw', event => {\n"
        "  event.add('demo', 'item.same', '舊一');\n"
        "  event.add('demo', 'item.same', '舊二');\n"
        "});\n",
        encoding="utf-8",
    )
    translated = tmp_path / "translated" / "client_scripts" / "lang.json"
    translated.parent.mkdir(parents=True)
    translated.write_text(
        json.dumps(
            {
                "client_scripts/lang.js|ClientEvents.lang[en_us][demo].event.add[1]|item.same": "新翻譯"
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="zh_tw key 不唯一"):
        inject(
            str(tmp_path / "kubejs"),
            str(tmp_path / "translated"),
            str(tmp_path / "output"),
        )


def test_kubejs_lang_writer_rejects_ambiguous_zh_tw_fallback_key(tmp_path):
    """zh_tw fallback 的重複 key 也不可只更新其中一筆。"""
    source = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "ClientEvents.lang('zh_tw', event => {\n"
        "  event.add('demo', 'item.same', '舊一');\n"
        "  event.add('demo', 'item.same', '舊二');\n"
        "});\n",
        encoding="utf-8",
    )
    translated = tmp_path / "translated" / "client_scripts" / "lang.json"
    translated.parent.mkdir(parents=True)
    translated.write_text(
        json.dumps(
            {
                "client_scripts/lang.js|ClientEvents.lang[zh_tw][demo].event.add[1]|item.same": "新翻譯"
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="zh_tw key 不唯一"):
        inject(
            str(tmp_path / "kubejs"),
            str(tmp_path / "translated"),
            str(tmp_path / "output"),
        )


def test_kubejs_lang_writer_rejects_dynamic_zh_tw_duplicate_key(tmp_path):
    """動態 zh_tw value 也要納入同 key 衝突保護，不能只更新靜態目標。"""
    source = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "ClientEvents.lang('zh_tw', event => {\n"
        "  const value = getTranslatedValue();\n"
        "  event.add('demo', 'item.same', value);\n"
        "  event.add('demo', 'item.same', '舊翻譯');\n"
        "});\n",
        encoding="utf-8",
    )
    translated = tmp_path / "translated" / "client_scripts" / "lang.json"
    translated.parent.mkdir(parents=True)
    translated.write_text(
        json.dumps(
            {
                "client_scripts/lang.js|ClientEvents.lang[zh_tw][demo].event.add[2]|item.same": "新翻譯"
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="zh_tw key 不唯一"):
        inject(
            str(tmp_path / "kubejs"),
            str(tmp_path / "translated"),
            str(tmp_path / "output"),
        )


def test_kubejs_lang_writer_rejects_dynamic_zh_tw_target_from_english_source(tmp_path):
    """英文來源不可覆寫唯一但動態的既有繁中目標。"""
    source = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo', 'item.same', 'Original');\n"
        "});\n"
        "ClientEvents.lang('zh_tw', event => {\n"
        "  event.add('demo', 'item.same', getTranslatedValue());\n"
        "});\n",
        encoding="utf-8",
    )
    translated = tmp_path / "translated" / "client_scripts" / "lang.json"
    translated.parent.mkdir(parents=True)
    translated.write_text(
        json.dumps(
            {
                "client_scripts/lang.js|ClientEvents.lang[en_us][demo].event.add[1]|item.same": "新翻譯"
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="不是可安全回寫的靜態字串"):
        inject(
            str(tmp_path / "kubejs"),
            str(tmp_path / "translated"),
            str(tmp_path / "output"),
        )


def test_ftb_single_file_locale_identity_is_shared(tmp_path):
    lang = tmp_path / "config" / "ftbquests" / "quests" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.snbt").write_text('{quest.title:"English title"}', encoding="utf-8")
    (lang / "zh_cn.snbt").write_text('{quest.title:"簡體標題"}', encoding="utf-8")

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.kind == "ftb_quest_lang"]
    assert len(items) == 1
    assert items[0].key.startswith("__root__.snbt|lang:quest.title")
    assert items[0].source_path == "lang/en_us.snbt"


def test_ftb_non_default_locale_is_source_and_keeps_locale_identity(tmp_path):
    lang = tmp_path / "config" / "ftbquests" / "quests" / "lang" / "pt_br"
    lang.mkdir(parents=True)
    (lang / "chapter.snbt").write_text(
        '{quest.title:"Título em português"}', encoding="utf-8"
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.kind == "ftb_quest_lang"]

    assert len(items) == 1
    assert result.locale_fallbacks == {"ftbquests": "pt_br"}
    assert items[0].source_locale == "pt_br"
    assert items[0].source_selector == "lang:pt_br:quest.title"
    assert items[0].source_path == "lang/pt_br/chapter.snbt"


def test_ftb_fallback_source_keeps_companion_zh_tw_translation(tmp_path):
    """非英文 fallback 仍是原文；同時存在的 zh_tw 只作為既有譯文。"""
    lang = tmp_path / "config" / "ftbquests" / "quests" / "lang"
    (lang / "pt_br").mkdir(parents=True)
    (lang / "zh_tw").mkdir(parents=True)
    (lang / "pt_br" / "chapter.snbt").write_text(
        '{quest.title:"Título em português"}', encoding="utf-8"
    )
    (lang / "zh_tw" / "chapter.snbt").write_text(
        '{quest.title:"繁體標題"}', encoding="utf-8"
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.kind == "ftb_quest_lang"]

    assert len(items) == 1
    assert items[0].source_locale == "pt_br"
    assert items[0].en_us == "Título em português"
    assert items[0].zh_tw == "繁體標題"


def test_ftb_empty_en_us_uses_non_empty_fallback_source(tmp_path):
    """空的 en_us 不可遮蔽可用的 pt_br 原文。"""
    lang = tmp_path / "config" / "ftbquests" / "quests" / "lang"
    (lang / "pt_br").mkdir(parents=True)
    (lang / "en_us").mkdir(parents=True)
    (lang / "pt_br" / "chapter.snbt").write_text(
        '{quest.title:"Título em português"}', encoding="utf-8"
    )
    (lang / "en_us" / "chapter.snbt").write_text('{quest.title:""}', encoding="utf-8")

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.kind == "ftb_quest_lang"]

    assert len(items) == 1
    assert result.locale_fallbacks == {"ftbquests": "pt_br"}
    assert items[0].source_locale == "pt_br"
    assert items[0].en_us == "Título em português"
    assert items[0].source_path == "lang/pt_br/chapter.snbt"


def test_ftb_invalid_locale_path_is_reported_and_not_used_as_source(tmp_path):
    lang = tmp_path / "config" / "ftbquests" / "quests" / "lang"
    lang.mkdir(parents=True)
    (lang / "README.snbt").write_text(
        '{quest.title:"Not a locale file"}', encoding="utf-8"
    )

    result = scan_pack_sources(tmp_path, "1.21.1")

    assert not [item for item in result.items if item.kind == "ftb_quest_lang"]
    assert any(issue["reason"] == "ftb_lang_locale_invalid" for issue in result.issues)


def test_ftb_component_json_is_not_sent_as_plain_text(tmp_path):
    quests = tmp_path / "config" / "ftbquests" / "quests"
    quests.mkdir(parents=True)
    (quests / "chapter.snbt").write_text(
        '{id:"component",title:"{\\"text\\":\\"Warning\\",\\"color\\":\\"red\\"}"}',
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    assert not result.items
    assert any(
        issue["reason"] == "ftb_component_json_unsupported" for issue in result.issues
    )


def test_kubejs_lang_namespace_is_part_of_identity(tmp_path):
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo_a', 'item.same', 'A');\n"
        "  event.add('demo_b', 'item.same', 'B');\n"
        "});\n",
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.key == "item.same"]

    assert {(item.mod_id, item.en_us) for item in items} == {
        (f"demo_a@{result.pack_scope}", "A"),
        (f"demo_b@{result.pack_scope}", "B"),
    }
    assert len({item.source_selector for item in items}) == 2


def test_kubejs_lang_uses_most_populated_locale_only_without_en_us(tmp_path):
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "pt_br.json").write_text(
        json.dumps({"a": "Portuguese A", "b": "Portuguese B"}),
        encoding="utf-8",
    )
    (lang / "ru_ru.json").write_text(
        json.dumps({"a": "Russian A"}),
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.source_format == "lang-json"]

    assert result.locale_fallbacks == {"demo": "pt_br"}
    assert {(item.key, item.en_us) for item in items} == {
        ("a", "Portuguese A"),
        ("b", "Portuguese B"),
    }


def test_kubejs_lang_prefers_pt_br_on_a_fallback_tie(tmp_path):
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "pt_br.json").write_text(json.dumps({"a": "Portuguese"}), encoding="utf-8")
    (lang / "ru_ru.json").write_text(json.dumps({"a": "Russian"}), encoding="utf-8")

    result = scan_pack_sources(tmp_path, "1.21.1")

    assert result.locale_fallbacks == {"demo": "pt_br"}
    assert [
        item.en_us for item in result.items if item.source_format == "lang-json"
    ] == ["Portuguese"]


def test_kubejs_lang_keeps_en_us_as_source_when_other_locale_is_larger(tmp_path):
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({"a": "English"}), encoding="utf-8")
    (lang / "pt_br.json").write_text(
        json.dumps({"a": "Portuguese", "b": "Extra"}),
        encoding="utf-8",
    )

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.source_format == "lang-json"]

    assert result.locale_fallbacks == {}
    assert {(item.key, item.en_us) for item in items} == {("a", "English")}


def test_kubejs_lang_uses_fallback_when_en_us_is_empty(tmp_path):
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({}), encoding="utf-8")
    (lang / "pt_br.json").write_text(json.dumps({"a": "Portuguese"}), encoding="utf-8")

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.source_format == "lang-json"]

    assert result.locale_fallbacks == {"demo": "pt_br"}
    assert {(item.key, item.en_us) for item in items} == {("a", "Portuguese")}


def test_kubejs_lang_uses_chinese_fallback_as_the_source_snapshot(tmp_path):
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "zh_cn.json").write_text(
        json.dumps({"a": "簡體來源", "b": "另一個來源"}), encoding="utf-8"
    )
    (lang / "ru_ru.json").write_text(json.dumps({"a": "Russian"}), encoding="utf-8")

    result = scan_pack_sources(tmp_path, "1.21.1")
    items = [item for item in result.items if item.source_format == "lang-json"]

    assert result.locale_fallbacks == {"demo": "zh_cn"}
    # 來源快照仍是原文；簡中來源同時是轉繁的候選（zh_cn 供匯入時 OpenCC 轉繁）。
    assert {(item.key, item.en_us, item.zh_cn) for item in items} == {
        ("a", "簡體來源", "簡體來源"),
        ("b", "另一個來源", "另一個來源"),
    }
    assert all(item.source is None for item in items)


def test_moddb_ingest_converts_zh_cn_only_pack_lang_to_zh_tw(tmp_path):
    """只附 zh_cn.json 的 KubeJS 語系：原文保留簡中，zh_tw 自動簡轉繁（簡中轉繁來源）。"""
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "zh_cn.json").write_text(
        json.dumps({"subtitles.hurt": "幽灵受伤"}), encoding="utf-8"
    )

    db = TranslationDB(tmp_path / "translation.db")
    try:
        list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.20.1", include_pack_content=True),
                workers=1,
            )
        )
        row = db._one(
            "SELECT e.en_us, ef.zh_tw, ef.source FROM entry e "
            "JOIN effective ef ON ef.entry_id = e.id "
            "WHERE e.kind='kubejs_lang' AND e.key='subtitles.hurt'"
        )
        assert row[0] == "幽灵受伤"
        assert row[1] == "幽靈受傷"
        assert row[2] == SRC_JAR_CN
    finally:
        db.close()


def test_untranslated_english_value_in_a_chinese_source_is_not_a_translation(tmp_path):
    """zh_cn 檔裡沒翻的英文值不是譯文：不補 zh_cn，條目仍是未翻譯。"""
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "zh_cn.json").write_text(
        json.dumps({"a": "Untranslated English", "b": "幽灵受伤"}), encoding="utf-8"
    )

    result = scan_pack_sources(tmp_path, "1.20.1")
    items = {i.key: i for i in result.items if i.source_format == "lang-json"}

    assert items["a"].zh_cn == "" and items["a"].zh_tw == ""
    assert items["b"].zh_cn == "幽灵受伤"


def test_moddb_ingest_keeps_chinese_fallback_as_original_text(tmp_path):
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "zh_tw.json").write_text(
        json.dumps({"item.demo": "繁中來源"}), encoding="utf-8"
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
        row = db._one(
            "SELECT en_us FROM entry WHERE kind='kubejs_lang' AND key='item.demo'"
        )
        assert row == ("繁中來源",)
    finally:
        db.close()
