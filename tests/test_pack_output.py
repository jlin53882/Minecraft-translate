"""ModDB 生效譯文輸出到 KubeJS／FTB overlay 的 round-trip 測試。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ftb_snbt_lib as snbt
import pytest

from tests.test_pack_sources import _write_fixture
from translation_tool.translation_db.models import (
    EffectivePackTranslation,
    EntryOrigin,
    ScanItem,
)
from translation_tool.translation_db.pack_output import (
    PackOutputError,
    PackOutputReport,
    _check_rescan_identity,
    _PlannedTranslation,
    _validate_stage_files,
    export_pack_overlay,
)
from translation_tool.translation_db.pack_sources import (
    PackScanResult,
    pack_scope_for_root,
    scan_pack_sources,
)
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator
from translation_tool.translation_db.schema import KIND_KUBEJS_LANG


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
        assert report.entries_reparsed > 0
        assert report.entries_rescan_verified >= report.entries_applied

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

        first_hashes = {
            path.relative_to(output).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in output.rglob("*")
            if path.is_file()
        }
        second = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)
        assert second.ok
        second_hashes = {
            path.relative_to(output).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in output.rglob("*")
            if path.is_file()
        }
        assert second_hashes == first_hashes
    finally:
        db.close()


def test_kubejs_lang_script_round_trip_maps_en_us_to_emitted_zh_tw_selector(
    tmp_path: Path,
):
    """追加的 zh_tw ClientEvents.lang 必須能被同一 scanner 找回。"""
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo.key', 'Original');\n"
        "});\n",
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
            "WHERE e.kind=? AND e.key=? AND eo.pack_scope=? LIMIT 1",
            (KIND_KUBEJS_LANG, "demo.key", scope),
        )[0]
        db.save_manual(entry_id, "翻譯後", propagate=False)

        report = export_pack_overlay(
            db,
            tmp_path,
            "1.21.1",
            tmp_path.parent / "kubejs-lang-overlay",
            pack_scope=scope,
        )

        assert report.ok
        emitted = (
            tmp_path.parent / "kubejs-lang-overlay" / "kubejs/client_scripts/lang.js"
        ).read_text(encoding="utf-8")
        assert "ClientEvents.lang('zh_tw'" in emitted
        assert "翻譯後" in emitted
        assert report.entries_rescan_verified == report.entries_applied
    finally:
        db.close()


def test_kubejs_lang_script_zh_cn_fallback_emits_zh_tw_selector(tmp_path: Path):
    """只有 zh_cn 的 JS 來源仍可翻譯，輸出時改寫成 zh_tw selector。"""
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('zh_cn', event => {\n"
        "  event.add('demo.key', 'Original');\n"
        "});\n",
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
            "WHERE e.kind=? AND e.key=? AND eo.pack_scope=? LIMIT 1",
            (KIND_KUBEJS_LANG, "demo.key", scope),
        )[0]
        db.save_manual(entry_id, "翻譯後", propagate=False)

        output = tmp_path.parent / "kubejs-zh-cn-overlay"
        report = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)

        assert report.ok
        emitted = (output / "kubejs/client_scripts/lang.js").read_text(encoding="utf-8")
        assert "ClientEvents.lang('zh_tw'" in emitted
        assert "翻譯後" in emitted
        assert report.entries_rescan_verified == report.entries_applied
    finally:
        db.close()


@pytest.mark.parametrize("source_locale", ["en_us", "zh_cn"])
def test_kubejs_lang_script_updates_existing_zh_tw_without_duplicate(
    tmp_path: Path, source_locale: str
):
    """既有 zh_tw key 應更新，不可追加第二個同 key overlay。"""
    script = tmp_path / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        f"ClientEvents.lang('{source_locale}', event => {{\n"
        "  event.add('demo', 'item.demo.machine', 'Original');\n"
        "});\n"
        "ClientEvents.lang('zh_tw', event => {\n"
        "  event.add('demo', 'item.demo.machine', '舊翻譯');\n"
        "});\n",
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
            "WHERE e.kind=? AND e.key=? AND eo.pack_scope=? "
            "AND eo.selector LIKE ? LIMIT 1",
            (
                KIND_KUBEJS_LANG,
                "item.demo.machine",
                scope,
                f"%ClientEvents.lang[{source_locale}]%",
            ),
        )[0]
        db.save_manual(entry_id, "新翻譯", propagate=False)

        output = tmp_path.parent / f"kubejs-existing-zh-tw-{source_locale}"
        report = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)

        assert report.ok
        emitted = (output / "kubejs/client_scripts/lang.js").read_text(encoding="utf-8")
        assert emitted.count("item.demo.machine") == 2
        assert emitted.count("ClientEvents.lang('zh_tw'") == 1
        assert "舊翻譯" not in emitted
        assert "新翻譯" in emitted
        assert report.entries_rescan_verified == report.entries_applied
    finally:
        db.close()


def test_rescan_identity_uses_one_index_and_reports_duplicate_conflicts():
    """大批量重掃描不可退化成每筆計畫線性掃描。"""
    scope = "pack-scope"
    plans: list[_PlannedTranslation] = []
    scanned: list[ScanItem] = []
    for index in range(2_000):
        source_path = f"client_scripts/{index}.js"
        selector = f"ItemEvents.tooltip.event.add[demo:{index}|1]"
        origin = EntryOrigin(scope, "kubejs_script", source_path, selector)
        item = EffectivePackTranslation(
            index,
            "kubejs_script",
            f"kubejs@{scope}",
            f"key-{index}",
            "Original",
            "翻譯",
            origin,
        )
        plans.append(_PlannedTranslation(item, f"kubejs-script/{source_path}", "翻譯"))
        scanned.append(
            ScanItem(
                "kubejs_script",
                f"kubejs@{scope}",
                f"key-{index}",
                "Original",
                zh_tw="翻譯",
                source_path=source_path,
                source_selector=selector,
            )
        )

    report = PackOutputReport(scope, "output", True)
    _check_rescan_identity(
        plans, PackScanResult(pack_scope=scope, items=scanned), report
    )
    assert report.entries_rescan_verified == len(plans)
    assert report.unapplied == []

    duplicate = scanned[0]
    conflicting = ScanItem(
        duplicate.kind,
        duplicate.mod_id,
        duplicate.key,
        duplicate.en_us,
        zh_tw="另一個翻譯",
        source_path=duplicate.source_path,
        source_selector=duplicate.source_selector,
    )
    duplicate_report = PackOutputReport(scope, "output", True)
    _check_rescan_identity(
        plans[:1],
        PackScanResult(pack_scope=scope, items=[duplicate, conflicting]),
        duplicate_report,
    )
    assert duplicate_report.conflicts


def test_kubejs_normalized_rescan_collision_is_not_hidden_by_expected_value():
    """normalized occurrence 的不同值不能被 plan value 反向篩掉。"""
    scope = "pack-scope"
    source_path = "client_scripts/lang.js"
    selector = "ClientEvents.lang[en_us][demo].event.add[1]|item.same"
    origin = EntryOrigin(
        scope,
        KIND_KUBEJS_LANG,
        source_path,
        selector,
        source_hash="source-hash",
        format_profile="kubejs-js",
    )
    plan = _PlannedTranslation(
        EffectivePackTranslation(
            1,
            KIND_KUBEJS_LANG,
            f"demo@{scope}",
            "item.same",
            "Original",
            "翻譯",
            origin,
        ),
        f"kubejs-script/{source_path}",
        "翻譯",
    )
    scanned = [
        ScanItem(
            KIND_KUBEJS_LANG,
            f"demo@{scope}",
            "item.same",
            "",
            zh_tw="翻譯",
            source_path=source_path,
            source_selector=("ClientEvents.lang[zh_tw][demo].event.add[3]|item.same"),
        ),
        ScanItem(
            KIND_KUBEJS_LANG,
            f"demo@{scope}",
            "item.same",
            "",
            zh_tw="另一個翻譯",
            source_path=source_path,
            source_selector=("ClientEvents.lang[zh_tw][demo].event.add[4]|item.same"),
        ),
    ]
    report = PackOutputReport(scope, "output", True)

    _check_rescan_identity(
        [plan], PackScanResult(pack_scope=scope, items=scanned), report
    )

    assert report.conflicts
    assert report.entries_rescan_verified == 0


def test_stage_validation_uses_javascript_parser(tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "broken.js").write_text("function (", encoding="utf-8")
    report = PackOutputReport("scope", str(tmp_path / "output"), True)

    _validate_stage_files(stage, report)

    assert any("JS 語法驗證失敗" in message for message in report.unapplied)


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


def test_export_rejects_existing_unmanaged_output_directory(tmp_path: Path):
    """非本工具管理的非空輸出不可被整體替換或刪除。"""
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
        output = tmp_path.parent / "unmanaged-overlay"
        output.mkdir()
        sentinel = output / "important.txt"
        sentinel.write_text("must survive", encoding="utf-8")

        with pytest.raises(PackOutputError, match="未受管內容"):
            export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)

        assert sentinel.read_text(encoding="utf-8") == "must survive"
    finally:
        db.close()


def test_export_preserves_existing_kubejs_lang_keys_not_managed_by_db(
    tmp_path: Path,
):
    """KubeJS lang overlay 只覆蓋本次計畫鍵，保留既有未管理翻譯。"""
    _write_fixture(tmp_path)
    lang_path = tmp_path / "kubejs/assets/demo/lang/zh_tw.json"
    lang_path.write_text(
        json.dumps(
            {
                "item.demo.asset": "舊資產翻譯",
                "item.demo.unmanaged": "保留的既有翻譯",
            },
            ensure_ascii=False,
        ),
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
            "WHERE e.key=? AND eo.pack_scope=? LIMIT 1",
            ("item.demo.asset", scope),
        )[0]
        db.save_manual(entry_id, "新的資產翻譯", propagate=False)

        output = tmp_path.parent / "kubejs-preserve-overlay"
        report = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)
        assert report.ok
        result = json.loads(
            (output / "kubejs/assets/demo/lang/zh_tw.json").read_text(encoding="utf-8")
        )
        assert result == {
            "item.demo.asset": "新的資產翻譯",
            "item.demo.unmanaged": "保留的既有翻譯",
        }
    finally:
        db.close()


def test_export_round_trip_scans_and_writes_direct_string_tooltip(
    tmp_path: Path,
):
    """直接字串 Tooltip 必須完整通過 scan、DB 與 overlay writer。"""
    _write_fixture(tmp_path)
    direct = tmp_path / "kubejs/client_scripts/c/direct.js"
    direct.parent.mkdir(parents=True)
    direct.write_text(
        "ItemEvents.tooltip(event => { "
        "event.add('demo:direct', 'Direct tooltip'); "
        "});\n",
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
            "WHERE e.kind='kubejs_script' AND e.key=? AND eo.pack_scope=? LIMIT 1",
            ("client_scripts/c/direct.js|tooltip:demo:direct:1", scope),
        )[0]
        db.save_manual(entry_id, "直接字串工具提示", propagate=False)

        output = tmp_path.parent / "direct-tooltip-overlay"
        report = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)
        assert report.ok
        result = (output / "kubejs/client_scripts/c/direct.js").read_text(
            encoding="utf-8"
        )
        assert "直接字串工具提示" in result
        assert "Direct tooltip" not in result
    finally:
        db.close()


@pytest.mark.parametrize("target_kind", ["same", "ancestor", "inside"])
def test_export_rejects_output_boundary_that_can_touch_source(
    tmp_path: Path, target_kind: str
):
    pack_root = tmp_path / "pack"
    _write_fixture(pack_root)
    sentinel = tmp_path / "keep.txt"
    sentinel.write_bytes(b"do not delete")
    source = pack_root / "kubejs" / "client_scripts" / "a" / "main.js"
    source_bytes = source.read_bytes()
    db = TranslationDB(pack_root / "translation.db")
    try:
        if target_kind == "same":
            output = pack_root
        elif target_kind == "ancestor":
            output = tmp_path
        else:
            output = pack_root / "overlay"

        with pytest.raises(PackOutputError, match="輸出目錄不可"):
            export_pack_overlay(
                db,
                pack_root,
                "1.21.1",
                output,
                pack_scope=pack_scope_for_root(pack_root),
            )

        assert source.read_bytes() == source_bytes
        assert sentinel.read_bytes() == b"do not delete"
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


def test_export_maps_non_default_ftb_locale_directory_to_zh_tw(tmp_path: Path):
    lang = tmp_path / "config" / "ftbquests" / "quests" / "lang" / "pt_br"
    lang.mkdir(parents=True)
    (lang / "chapter.snbt").write_text(
        '{quest.title:"Título em português"}', encoding="utf-8"
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
            "WHERE e.kind='ftb_quest_lang' AND eo.source_locale='pt_br' "
            "AND eo.pack_scope=? LIMIT 1",
            (scope,),
        )[0]
        db.save_manual(entry_id, "繁體標題", propagate=False)
        output = tmp_path.parent / "ftb-pt-br-overlay"
        report = export_pack_overlay(db, tmp_path, "1.21.1", output, pack_scope=scope)

        assert report.ok
        translated = snbt.load(
            (output / "config/ftbquests/quests/lang/zh_tw/chapter.snbt").open(
                encoding="utf-8"
            )
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


# --- 舊版 FTB 語系 selector（``lang:<key>``）與新版（``lang:<locale>:<key>``）---------


def _scan_ftb_pack(db, root: Path) -> str:
    list(
        scan_folder_generator(
            db, root, ScanOptions("1.21.1", include_pack_content=True), workers=1
        )
    )
    return scan_pack_sources(root, "1.21.1").pack_scope


def _make_ftb_pack(tmp_path: Path) -> Path:
    lang = tmp_path / "config" / "ftbquests" / "quests" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.snbt").write_text('{quest.title:"English title"}', encoding="utf-8")
    return tmp_path


def _downgrade_ftb_origins_to_legacy(db) -> int:
    """模擬升級前的資料庫：FTB 語系來源只有 ``lang:<key>``，沒有 locale。"""
    with db._tx() as conn:
        rows = conn.execute(
            "SELECT id, selector FROM entry_origin WHERE source_kind='ftb_quest_lang'"
        ).fetchall()
        for row_id, selector in rows:
            legacy = "lang:" + selector.split(":", 2)[2]
            conn.execute(
                "UPDATE entry_origin SET selector=? WHERE id=?", (legacy, row_id)
            )
    return len(rows)


def _ftb_selectors(db) -> list[str]:
    return sorted(
        row[0]
        for row in db._q(
            "SELECT selector FROM entry_origin WHERE source_kind='ftb_quest_lang'"
        )
    )


def test_legacy_ftb_selector_without_rescan_still_passes_rescan_identity(
    tmp_path: Path,
):
    """升級前的資料庫（只有 ``lang:<key>``）不重新掃描、直接輸出，重掃描驗證要能對應。"""
    root = _make_ftb_pack(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        scope = _scan_ftb_pack(db, root)
        entry_id = db._one(
            "SELECT e.id FROM entry e WHERE e.kind='ftb_quest_lang' LIMIT 1"
        )[0]
        db.save_manual(entry_id, "繁體標題", propagate=False)
        assert _downgrade_ftb_origins_to_legacy(db) == 1
        assert _ftb_selectors(db) == ["lang:quest.title"]

        output = tmp_path.parent / "legacy-no-rescan"
        report = export_pack_overlay(db, root, "1.21.1", output, pack_scope=scope)

        assert report.ok, (report.unapplied, report.conflicts)
        assert report.entries_rescan_verified == 1
        translated = snbt.load(
            (output / "config/ftbquests/quests/lang/zh_tw.snbt").open(encoding="utf-8")
        )
        assert str(translated["quest.title"]) == "繁體標題"
    finally:
        db.close()


def test_upgrade_rescan_export_round_trip_retires_the_legacy_selector(tmp_path: Path):
    """舊資料庫 → 升級 → 重新掃描 → 用既有譯文輸出 → 重掃描驗證成功，且舊 selector 已淘汰。"""
    root = _make_ftb_pack(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        scope = _scan_ftb_pack(db, root)
        entry_id = db._one(
            "SELECT e.id FROM entry e WHERE e.kind='ftb_quest_lang' LIMIT 1"
        )[0]
        db.save_manual(entry_id, "繁體標題", propagate=False)
        _downgrade_ftb_origins_to_legacy(db)

        _scan_ftb_pack(db, root)  # 升級後重新掃描

        assert _ftb_selectors(db) == ["lang:en_us:quest.title"]
        output = tmp_path.parent / "legacy-rescanned"
        report = export_pack_overlay(db, root, "1.21.1", output, pack_scope=scope)

        assert report.ok, (report.unapplied, report.conflicts)
        assert report.entries_rescan_verified == 1
        translated = snbt.load(
            (output / "config/ftbquests/quests/lang/zh_tw.snbt").open(encoding="utf-8")
        )
        assert str(translated["quest.title"]) == "繁體標題"
    finally:
        db.close()


def test_legacy_ftb_selector_without_path_evidence_is_still_reported(tmp_path: Path):
    """路徑沒有 locale 證據時不猜測對應：維持「重掃描未找到」，不放行。"""
    from translation_tool.translation_db.pack_output import _find_rescanned_items

    origin = EntryOrigin(
        pack_scope="p",
        source_kind="ftb_quest_lang",
        source_path="config/ftbquests/quests/lang/notalocale/chapter.snbt",
        selector="lang:quest.title",
        source_hash="h",
        format_profile="ftb-snbt-lang",
    )

    class Plan:
        class item:
            pass

    Plan.item.origin = origin
    assert _find_rescanned_items(Plan, {}) == []
