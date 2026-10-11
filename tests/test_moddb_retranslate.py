"""Explicit repair of existing AI translations equal to their source."""

from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from app.services_impl import (
    moddb_repair_review_store,
    moddb_retranslate_service,
    moddb_service,
    moddb_translate_service,
)
from app.services_impl.moddb_translate_service import TranslateOptions
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    OperationDescriptor,
    OperationRegistry,
    ShutdownPolicy,
)
from app.tasks.task_session import TaskSession
from app.views.moddb import retranslation_controller, translate_panel
from tests.conftest import mock_page
from translation_tool.translation_db import (
    DbSettings,
    ScanItem,
    TranslationDB,
    WriteBackItem,
)
from translation_tool.translation_db.schema import (
    KIND_LANG,
    KIND_PATCHOULI,
    SRC_AI,
    SRC_AI_REPAIR,
    SRC_CUSTOM,
    SRC_JAR_TW,
    SRC_MANUAL,
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "retranslate.db"
    monkeypatch.setattr(
        moddb_service,
        "load_db_settings",
        lambda: DbSettings(path=str(path), version="1.21.1"),
    )
    return path


def _ingest(
    db, version, mod_id, key, source, translation, *, en="Minecraft", kind=KIND_LANG
):
    db.ingest(
        version,
        [
            ScanItem(
                kind,
                mod_id,
                key,
                en,
                translation,
                source=source,
            )
        ],
    )


def _ai_entry(
    db,
    *,
    version="1.21.1",
    mod_id="foo",
    key="item.name",
    en="Minecraft",
    kind=KIND_LANG,
):
    _ingest(db, version, mod_id, key, SRC_AI, en, en=en, kind=kind)
    return next(
        row
        for row in db.list_entries(version)[0]
        if row.mod_id == mod_id and row.key == key
    )


def _replace_identity(entry):
    return {
        "expected_version": entry.mc_version,
        "expected_kind": entry.kind,
        "expected_mod_id": entry.mod_id,
        "expected_key": entry.key,
        "expected_en_us": entry.en_us,
    }


def _translation_revision(db, entry_id, source):
    row = db._one(
        "SELECT revision FROM translation WHERE entry_id=? AND source=?",
        (entry_id, source),
    )
    return row[0] if row else None


def _options(**overrides):
    values = {
        "version": "1.21.1",
        "mod_ids": (),
        "limit": 2000,
        "write_cache": True,
    }
    values.update(overrides)
    return TranslateOptions(**values)


def _set_repair_batch_size(monkeypatch, size):
    batch_size = lambda _cache_type, _sizes: size
    monkeypatch.setattr(
        moddb_retranslate_service, "_get_default_batch_size", batch_size
    )
    monkeypatch.setattr(moddb_translate_service, "_get_default_batch_size", batch_size)


def _drain_page_tasks(page):
    page._run_all_tasks()


def _contains_control_text(control, expected):
    if getattr(control, "value", None) == expected:
        return True
    if getattr(control, "text", None) == expected:
        return True
    content = getattr(control, "content", None)
    if content is not None and _contains_control_text(content, expected):
        return True
    return any(
        _contains_control_text(child, expected)
        for child in (getattr(control, "controls", None) or [])
    )


def _find_control_by_text(control, expected):
    content = getattr(control, "content", None)
    if getattr(control, "text", None) == expected or content == expected:
        return control
    found = (
        _find_control_by_text(content, expected)
        if content is not None and not isinstance(content, str)
        else None
    )
    if found is not None:
        return found
    for child in getattr(control, "controls", None) or []:
        found = _find_control_by_text(child, expected)
        if found is not None:
            return found
    return None


def _select_repair_mode(panel, mode="same_source_ai"):
    panel._repair_mode = mode
    panel.repair_mode_group.value = mode
    panel.repair_explainer.value = panel._repair_explanation()


def test_repair_mode_selector_explains_scope_and_updates_copy():
    panel = translate_panel.TranslatePanel(mock_page(), lambda: None)

    assert panel.log_view.expand is False
    assert panel.log_view.height == 0
    assert panel.log_view.visible is False
    assert panel.repair_samples.height == 0
    assert panel.repair_samples.visible is False

    radios = panel.repair_mode_group.content.controls
    assert [radio.value for radio in radios] == [
        "quality_mismatch",
        "same_source_ai",
    ]
    assert "所有來源" in panel.repair_explainer.value
    assert "匯入 ZIP" in panel.repair_explainer.value
    assert "不會改寫原始 ZIP" in panel.repair_explainer.value

    panel.repair_mode_group.value = "same_source_ai"
    panel._on_repair_mode_changed(SimpleNamespace(control=panel.repair_mode_group))

    assert panel._repair_mode == "same_source_ai"
    assert "目前生效來源為 AI 機翻" in panel.repair_explainer.value


def _inline_operation_launcher(calls=None):
    def launch(page, target, **kwargs):
        if calls is not None:
            calls.append((target, kwargs))
        if kwargs["owner"] == "moddb-retranslate-preview":
            target()
        return True

    return launch


def test_preview_uses_effective_ai_source_scope_and_limit(db_path):
    db = TranslationDB(db_path)
    _ai_entry(db, mod_id="foo", key="same")
    _ai_entry(db, mod_id="bar", key="same")
    _ingest(db, "1.21.1", "foo", "different", SRC_AI, "石頭", en="Stone")
    _ai_entry(db, mod_id="foo", key="empty", en="")
    manual = _ai_entry(db, mod_id="foo", key="manual")
    db.save_manual(manual.id, "Minecraft", propagate=False)
    _ingest(db, "1.21.1", "foo", "jar", SRC_JAR_TW, "Minecraft")
    _ingest(db, "1.21.1", "foo", "custom", SRC_CUSTOM, "Minecraft")
    _ai_entry(db, version="1.20.1", mod_id="foo", key="old-version")

    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(mod_ids=("foo",), limit=1)
    )

    assert preview.total_candidates == 1
    assert preview.selected_count == 1
    assert preview.entries[0].key == "same"
    assert preview.source == SRC_AI
    assert db.count_same_as_source_ai("1.21.1") == 2
    db.close()


def test_repair_scan_follows_the_effective_source_including_ai_repair(db_path):
    db = TranslationDB(db_path)
    shadowed = _ai_entry(db, key="shadowed", en="Use %s")
    db.save_manual(shadowed.id, "使用", propagate=False)
    _ingest(db, "1.21.1", "foo", "shadowed", SRC_JAR_TW, "使用", en="Use %s")
    repaired = _ai_entry(db, key="repaired", en="Use %s")
    db._conn.execute(
        "UPDATE translation SET zh_tw='使用' WHERE entry_id=? AND source=?",
        (repaired.id, SRC_AI),
    )
    db._conn.commit()
    _ingest(db, "1.21.1", "foo", "repaired", SRC_AI_REPAIR, "使用", en="Use %s")

    scanned = {
        (entry.key, entry.source_id)
        for entry, _nl, _mixed in db.iter_repairable_translation_entries("1.21.1")
    }
    everything = {
        (entry.key, entry.source_id)
        for entry, _nl, _mixed in db.iter_repairable_translation_entries(
            "1.21.1", only_effective=False
        )
    }
    effective = {
        row[0]: row[1]
        for row in db._conn.execute(
            "SELECT e.key, f.source FROM effective f JOIN entry e ON e.id=f.entry_id"
        )
    }
    db.close()

    assert scanned == {("shadowed", SRC_MANUAL), ("repaired", effective["repaired"])}
    assert (("shadowed", SRC_JAR_TW)) in everything and len(everything) > len(scanned)


@pytest.fixture
def all_source_rows(monkeypatch):
    """Scan every source row (not only the effective one) to exercise fan-out."""
    original = TranslationDB.iter_repairable_translation_entries

    def scan_all(self, *args, **kwargs):
        return original(self, *args, **{**kwargs, "only_effective": False})

    monkeypatch.setattr(TranslationDB, "iter_repairable_translation_entries", scan_all)


def test_special_character_repair_only_touches_the_effective_source_and_can_be_undone(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s")
    db.save_manual(entry.id, "使用", propagate=False)
    _ingest(db, "1.21.1", "foo", "item.name", SRC_JAR_TW, "使用", en="Use %s")

    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    # 人工（生效）以外的來源被蓋住、使用者看不到，不列入修復
    assert preview.mode == "quality_mismatch"
    assert preview.total_candidates == 1
    assert preview.selected_count == 1
    assert preview.sources == (SRC_MANUAL,)
    assert {row.source_id for row in preview.entries} == {SRC_MANUAL}
    db.close()

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: (
            [{**item, "text": "使用 %s"} for item in batch],
            "AUTO",
        ),
    )
    snap = _run(
        db_path,
        preview.entries,
        options=_options(write_cache=False),
        mode="quality_mismatch",
    )

    check = TranslationDB(db_path)
    rows = {row.source: row for row in check.entry_detail(entry.id).translations}
    assert rows[SRC_MANUAL].zh_tw == "使用 %s"
    assert rows[SRC_JAR_TW].zh_tw == "使用"
    assert rows[SRC_AI].zh_tw == "Use %s"
    history = check.entry_detail(entry.id).history
    source_repairs = [row for row in history if row.action == "quality_repair"]
    assert len(source_repairs) == 1 and source_repairs[0].source_id == SRC_MANUAL
    assert snap["summary"]["operation"] == "repair_special_character_mismatch"
    assert snap["summary"]["updated"] == 1
    assert snap["summary"]["remaining"] == 0
    assert check.revert(source_repairs[0].id) == 1
    restored = {row.source: row for row in check.entry_detail(entry.id).translations}
    assert restored[SRC_MANUAL].zh_tw == "使用"
    assert SRC_AI_REPAIR not in restored
    assert repair_cache == []
    check.close()


def test_quality_repair_includes_input_newline_mismatch_and_allows_output_difference(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    newline_mismatch = _ai_entry(db, key="a.newline_mismatch", en="Use %s\nnext")
    eligible = _ai_entry(db, key="b.eligible", en="Use %s\nnext")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (newline_mismatch.id, SRC_AI),
    )
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use\nnext' WHERE entry_id=? AND source=?",
        (eligible.id, SRC_AI),
    )
    db._conn.commit()

    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(limit=2), mode="quality_mismatch"
    )
    assert preview.total_candidates == 2
    assert preview.input_newline_mismatch_candidates == 1
    assert preview.input_newline_mismatch_with_other_hard_issues == 1
    assert [row.key for row in preview.entries] == [
        "a.newline_mismatch",
        "b.eligible",
    ]
    db.close()

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: (
            [{**item, "text": "Use %s repaired"} for item in batch],
            "AUTO",
        ),
    )
    result = _run(
        db_path,
        preview.entries,
        options=_options(write_cache=False),
        mode="quality_mismatch",
    )
    check = TranslationDB(db_path)
    stored = {
        row[0]: row[1]
        for row in check._q(
            "SELECT e.key,t.zh_tw FROM entry e JOIN translation t "
            "ON t.entry_id=e.id WHERE t.source=?",
            (SRC_AI,),
        )
    }
    assert stored["a.newline_mismatch"] == "Use %s repaired"
    assert stored["b.eligible"] == "Use %s repaired"
    # The general quality view still warns about this output's real newline delta.
    eligible_detail = check.entry_detail(eligible.id)
    assert eligible_detail.entry.quality_state == "mismatch"
    assert result["summary"]["updated"] == 2
    # 只有實際換行差異的結果不再佔用 AI 修復名額，因此完成後不列為待 AI 修復。
    assert result["summary"]["remaining"] == 0
    assert check.entry_detail(eligible.id).history[0].action == "quality_repair"
    check.close()


def test_quality_repair_skips_newline_only_input_mismatch(db_path, monkeypatch):
    db = TranslationDB(db_path)
    newline_only = _ai_entry(db, key="a.newline_only", en="Line one\nLine two")
    hard_issue = _ai_entry(db, key="b.hard_issue", en="Use %s")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Line one' WHERE entry_id=? AND source=?",
        (newline_only.id, SRC_AI),
    )
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (hard_issue.id, SRC_AI),
    )
    db._conn.commit()

    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()

    assert preview.total_candidates == 1
    assert preview.selected_count == 1
    assert preview.entries[0].key == "b.hard_issue"
    assert preview.skipped_newline_only_candidates == 1

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: (
            [{**item, "text": "Use %s repaired"} for item in batch],
            "AUTO",
        ),
    )
    session = TaskSession()
    moddb_retranslate_service.run_moddb_retranslate_service(
        _options(write_cache=False),
        session,
        preview.entries,
        mode="quality_mismatch",
        review_preview=preview,
    )
    summary = session.snapshot()["summary"]
    assert summary["candidates"] == 1
    assert summary["skipped_newline_only_candidates"] == 1
    assert summary["ai_submitted_items"] == 1


@pytest.mark.usefixtures("all_source_rows")
def test_quality_repair_representative_limit_keeps_all_mapped_sources(db_path):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s", key="mapped")
    db.save_manual(entry.id, "Use", propagate=False)
    _ingest(db, "1.21.1", "foo", "mapped", SRC_JAR_TW, "Use", en="Use %s")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(limit=1), mode="quality_mismatch"
    )
    assert preview.ai_representatives == 1
    assert preview.selected_count == 3
    assert {row.source_id for row in preview.entries} == {
        SRC_AI,
        SRC_MANUAL,
        SRC_JAR_TW,
    }
    db.close()


def test_manual_review_confirmation_skips_format_check_but_keeps_cas_history_and_undo(
    db_path,
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, key="review", en="Use %s")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    item = {
        "entry_id": entry.id,
        "version": entry.mc_version,
        "mod_id": entry.mod_id,
        "kind": entry.kind,
        "source_id": SRC_AI,
        "key": entry.key,
        "en_us": entry.en_us,
        "old_translation": "Use",
        "expected_revision": _translation_revision(db, entry.id, SRC_AI),
        "database_identity": str(db_path.resolve()),
        "database_file_id": db_path.stat().st_ino,
        "database_priority": db.priority,
    }
    db.close()

    assert (
        moddb_retranslate_service.apply_repair_review_item(db_path, item, "Use")
        == "stale"
    )
    assert (
        moddb_retranslate_service.apply_repair_review_item(
            db_path, item, "人工已確認，即使不同於來源佔位符"
        )
        == "applied"
    )
    check = TranslationDB(db_path)
    detail = check.entry_detail(entry.id)
    history = next(
        row for row in detail.history if row.action == "quality_repair_review"
    )
    assert history.actor == "AI 機翻修復・人工確認"
    from translation_tool.translation_db.schema import SRC_AI_REPAIR

    assert history.source_id == SRC_AI
    repaired = next(row for row in detail.translations if row.source == SRC_AI)
    assert repaired.zh_tw == "人工已確認，即使不同於來源佔位符"
    assert check.get_entry(entry.id).source == SRC_AI
    assert check.revert(history.id) == 1
    assert (
        next(
            row
            for row in check.entry_detail(entry.id).translations
            if row.source == SRC_AI
        ).zh_tw
        == "Use"
    )
    assert all(
        row.source != SRC_AI_REPAIR for row in check.entry_detail(entry.id).translations
    )
    check.close()


def test_manual_review_applies_one_confirmed_draft_to_matching_keys(
    db_path,
):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "shared.key", SRC_AI, "舊 AI", en="Use %s")
    _ingest(
        db,
        "1.21.1",
        "foo",
        "shared.key",
        SRC_MANUAL,
        "保留舊譯文",
        en="Use %s",
    )
    entry = next(
        row
        for row in db.list_entries("1.21.1")[0]
        if row.mod_id == "foo" and row.key == "shared.key"
    )
    review_path = moddb_repair_review_store.create_run(db_path, "run-shared-key")

    def review_item(source_id):
        old_translation = {
            SRC_AI: "舊 AI",
            SRC_MANUAL: "保留舊譯文",
        }[source_id]
        return {
            "entry_id": entry.id,
            "version": entry.mc_version,
            "mod_id": entry.mod_id,
            "kind": entry.kind,
            "source_id": source_id,
            "key": entry.key,
            "en_us": entry.en_us,
            "old_translation": old_translation,
            "expected_revision": _translation_revision(db, entry.id, source_id),
            "database_identity": str(db_path.resolve()),
            "database_file_id": db_path.stat().st_ino,
            "database_priority": db.priority,
        }

    first_index = moddb_repair_review_store.append_item(
        review_path, "run-shared-key", review_item(SRC_AI)
    )
    moddb_repair_review_store.append_item(
        review_path, "run-shared-key", review_item(SRC_MANUAL)
    )
    unrelated = review_item(SRC_AI)
    unrelated["mod_id"] = "other-mod"
    moddb_repair_review_store.append_item(review_path, "run-shared-key", unrelated)
    first = moddb_repair_review_store.load_item(
        review_path, "run-shared-key", first_index
    )
    db.close()

    assert first is not None
    assert (
        moddb_retranslate_service.apply_and_record_repair_review(
            db_path,
            review_path,
            "run-shared-key",
            first,
            "人工統一翻譯 %s",
        )
        == "applied"
    )
    assert moddb_repair_review_store.status_counts(review_path, "run-shared-key") == {
        "applied": 2,
        "pending": 1,
    }

    check = TranslationDB(db_path)
    translations = {
        row.source: row.zh_tw for row in check.entry_detail(entry.id).translations
    }
    assert translations[SRC_AI] == "人工統一翻譯 %s"
    assert translations[SRC_MANUAL] == "人工統一翻譯 %s"
    check.close()


def test_rejected_repair_result_is_written_to_durable_review_store(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, key="rejected", en="Use %s")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: ([{**batch[0], "text": "Use"}], "AUTO"),
    )
    summary = _run(
        db_path,
        preview.entries,
        options=_options(write_cache=False),
        mode="quality_mismatch",
    )["summary"]

    assert summary["flagged"] == summary["reviewable_results"] == 1
    assert summary["flagged_entries"] == []
    store_path = summary["review_store_path"]
    item = moddb_repair_review_store.load_item(store_path, summary["review_run_id"], 1)
    assert item["entry_id"] == entry.id
    assert item["expected_revision"] is not None
    assert item["database_file_id"] == db_path.stat().st_ino


def test_quality_repair_demotes_reviewed_manual_and_revert_restores_it(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s")
    db.save_manual(entry.id, "使用", actor="editor", propagate=False)
    db.review_manual(entry.id, expected_zh_tw="使用", actor="reviewer", propagate=False)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    assert preview.sources == (SRC_MANUAL,)
    db.close()

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: ([{**batch[0], "text": "使用 %s"}], "AUTO"),
    )
    result = _run(
        db_path,
        preview.entries,
        options=_options(write_cache=False),
        mode="quality_mismatch",
    )

    check = TranslationDB(db_path)
    detail = check.entry_detail(entry.id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    repaired_history = next(
        row for row in detail.history if row.action == "quality_repair"
    )
    assert result["summary"]["updated"] == 1
    # Automated repair updates the original row and demotes its reviewed status.
    assert (manual.zh_tw, manual.checker, manual.review_status) == (
        "使用 %s",
        "",
        "unreviewed",
    )
    assert detail.entry.source == SRC_MANUAL
    assert detail.entry.zh_tw == "使用 %s"
    assert SRC_AI_REPAIR not in {row.source for row in detail.translations}

    assert check.revert(repaired_history.id) == 1
    restored = check.entry_detail(entry.id)
    manual = next(row for row in restored.translations if row.source == SRC_MANUAL)
    assert (manual.zh_tw, manual.checker, manual.review_status) == (
        "使用",
        "reviewer",
        "reviewed",
    )
    assert restored.entry.source == SRC_MANUAL
    assert repair_cache == []
    check.close()


def _seed_fragmented_candidates(db):
    scans = []
    for mod_id, patch_count in (("mod_a", 2), ("mod_b", 3), ("mod_c", 1)):
        scans.extend(
            ScanItem(
                KIND_LANG,
                mod_id,
                f"lang.{index:02d}",
                "Minecraft",
                "Minecraft",
                source=SRC_AI,
            )
            for index in range(20)
        )
        scans.extend(
            ScanItem(
                KIND_PATCHOULI,
                mod_id,
                f"patchouli.{index:02d}",
                "Minecraft",
                "Minecraft",
                source=SRC_AI,
            )
            for index in range(patch_count)
        )
    db.ingest("1.21.1", scans)


def test_retranslation_items_stable_group_by_runtime_cache_type():
    items = [
        {"cache_type": "lang", "_kind": "lang", "id": "lang-a"},
        {"cache_type": "patchouli", "_kind": "book", "id": "book"},
        {"cache_type": "lang", "_kind": "future-kind", "id": "lang-b"},
    ]

    grouped = moddb_retranslate_service._group_retranslation_items_by_cache_type(items)

    assert [item["id"] for item in grouped] == ["lang-a", "lang-b", "book"]
    assert grouped[-1]["_kind"] == "book"
    assert grouped[-1]["cache_type"] == "patchouli"


def test_repair_batches_group_profiles_and_preview_reports_breakdown(
    db_path, monkeypatch, repair_cache
):
    _set_repair_batch_size(monkeypatch, 100)
    db = TranslationDB(db_path)
    _seed_fragmented_candidates(db)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()

    assert preview.total_candidates == 66
    assert preview.selected_count == 66
    assert preview.source == SRC_AI
    assert dict(preview.profile_counts) == {"lang": 60, "patchouli": 6}
    assert preview.estimated_batches == 2
    assert preview.entry_cache_types.count("lang") == 60
    assert preview.entry_cache_types.count("patchouli") == 6

    calls = []

    def fake_translate(batch, total):
        types = {item["cache_type"] for item in batch}
        calls.append((len(batch), types))
        return [{**item, "text": "我的世界"} for item in batch], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", fake_translate
    )
    snap = _run(db_path, preview.entries, options=_options(write_cache=False))

    assert calls == [(60, {"lang"}), (6, {"patchouli"})]
    assert snap["summary"]["batches"] == 2
    assert snap["summary"]["updated"] == 66
    logs = "\n".join(entry.text for entry in snap["logs"])
    assert "找到 66 筆待處理來源項目" in logs
    assert "來源：AI 機翻" in logs
    assert "Lang 60 筆" in logs
    assert "Patchouli 6 筆" in logs
    assert "本次略過既有翻譯快取，重新呼叫 AI" in logs
    assert repair_cache == []


def test_retranslation_token_budget_still_caps_grouped_profile(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "mod_a",
                f"lang.{index:02d}",
                "Minecraft",
                "Minecraft",
                source=SRC_AI,
            )
            for index in range(60)
        ],
    )
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()
    _set_repair_batch_size(monkeypatch, 100)
    monkeypatch.setattr(
        moddb_retranslate_service,
        "load_config",
        lambda: {"lm_translator": {"token_budget_enabled": True}},
    )
    budget_calls = []

    def token_budget(items, profile, preferred, lm_cfg):
        budget_calls.append((len(items), profile, preferred, lm_cfg))
        return min(len(items), 25)

    monkeypatch.setattr(moddb_retranslate_service, "select_batch_size", token_budget)
    batch_sizes = []

    def fake_translate(batch, total):
        batch_sizes.append(len(batch))
        return [{**item, "text": "我的世界"} for item in batch], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", fake_translate
    )

    snap = _run(db_path, preview.entries, options=_options(write_cache=False))

    assert batch_sizes == [25, 25, 10]
    assert [call[:3] for call in budget_calls[:3]] == [
        (60, "lang", 100),
        (35, "lang", 100),
        (10, "lang", 100),
    ]
    assert len(budget_calls) > 3  # planning samples reuse the runtime selector
    assert all(
        call[:3] in {(60, "lang", 100), (35, "lang", 100), (10, "lang", 100)}
        for call in budget_calls
    )
    assert all(call[3]["token_budget_enabled"] for call in budget_calls)
    assert snap["summary"]["batches"] == 3


def test_replace_ai_translation_is_compare_and_set_and_does_not_propagate(db_path):
    db = TranslationDB(db_path)
    target = _ai_entry(db)
    other = _ai_entry(db, version="1.20.1")

    identity = _replace_identity(target)
    unchanged = db.replace_ai_translation(
        target.id, "Minecraft", "Minecraft", **identity
    )
    assert unchanged.status == "unchanged"
    assert db.entry_detail(target.id).history == []

    result = db.replace_ai_translation(target.id, "Minecraft", "我的世界", **identity)
    assert result.status == "updated"
    assert db.get_entry(target.id).zh_tw == "我的世界"
    from translation_tool.translation_db.schema import SRC_AI_REPAIR

    assert db.get_entry(target.id).source == SRC_AI_REPAIR
    assert any(
        row.source == SRC_AI and row.zh_tw == "Minecraft"
        for row in db.entry_detail(target.id).translations
    )
    assert db.get_entry(other.id).zh_tw == "Minecraft"
    assert db.entry_detail(target.id).history[0].action == "ai_retranslate"
    assert (
        db.replace_ai_translation(target.id, "Minecraft", "過期結果", **identity).status
        == "skipped_changed"
    )

    db.write_back(
        "1.21.1",
        [
            # The ordinary INSERT OR IGNORE path must not overwrite the repaired row.
            WriteBackItem(KIND_LANG, "foo", "item.name", "Minecraft", "錯誤覆寫")
        ],
    )
    assert db.get_entry(target.id).zh_tw == "我的世界"
    db.close()


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("expected_version", "1.20.1"),
        ("expected_kind", KIND_PATCHOULI),
        ("expected_mod_id", "different-mod"),
        ("expected_key", "different.key"),
        ("expected_en_us", "different source"),
    ],
)
def test_replace_ai_translation_requires_exact_candidate_identity(
    db_path, field, wrong_value
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db)
    identity = _replace_identity(entry)
    identity[field] = wrong_value

    result = db.replace_ai_translation(entry.id, "Minecraft", "我的世界", **identity)

    assert result.status == "skipped_changed"
    assert db.get_entry(entry.id).zh_tw == "Minecraft"
    db.close()


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("expected_version", "1.20.1"),
        ("expected_kind", KIND_PATCHOULI),
        ("expected_mod_id", "different-mod"),
        ("expected_key", "different.key"),
        ("expected_en_us", "different source"),
    ],
)
def test_quality_repair_cas_requires_exact_entry_identity(db_path, field, wrong_value):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "item.quality.cas", SRC_AI, "Use", en="Use %s")
    entry = next(
        row for row in db.list_entries("1.21.1")[0] if row.key == "item.quality.cas"
    )
    identity = _replace_identity(entry)
    identity[field] = wrong_value

    result = db.replace_translation_quality_mismatch(
        entry.id,
        SRC_AI,
        "Use",
        "Use %s",
        **identity,
        expected_revision=_translation_revision(db, entry.id, SRC_AI),
    )

    assert result.status == "skipped_changed"
    assert (
        next(
            row
            for row in db.entry_detail(entry.id).translations
            if row.source == SRC_AI
        ).zh_tw
        == "Use"
    )
    db.close()


def test_automated_quality_repair_still_requires_valid_output(db_path):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "item.quality.output", SRC_AI, "Use", en="Use %s")
    entry = next(
        row for row in db.list_entries("1.21.1")[0] if row.key == "item.quality.output"
    )

    result = db.replace_translation_quality_mismatch(
        entry.id,
        SRC_AI,
        "Use",
        "人工已確認但未保留佔位符",
        **_replace_identity(entry),
        expected_revision=_translation_revision(db, entry.id, SRC_AI),
    )

    current = next(
        row for row in db.entry_detail(entry.id).translations if row.source == SRC_AI
    )
    assert result.status == "skipped_changed"
    assert current.zh_tw == "Use"
    db.close()


def test_automated_quality_repair_updates_and_tags_the_original_source(db_path):
    from translation_tool.translation_db.schema import SRC_AI_REPAIR

    db = TranslationDB(db_path)
    entry = _ai_entry(db, key="quality.tagged", en="Use %s")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()

    result = db.replace_translation_quality_mismatch(
        entry.id,
        SRC_AI,
        "Use",
        "使用 %s",
        **_replace_identity(entry),
        expected_revision=_translation_revision(db, entry.id, SRC_AI),
    )

    detail = db.entry_detail(entry.id)
    translations = {row.source: row for row in detail.translations}
    assert result.status == "updated"
    assert translations[SRC_AI].zh_tw == "使用 %s"
    assert SRC_AI_REPAIR not in translations
    history = next(row for row in detail.history if row.action == "quality_repair")
    assert history.source_id == SRC_AI
    assert db.get_entry(entry.id).source == SRC_AI
    assert SRC_AI in db.effective_source_codes("1.21.1")
    # The original source row was updated in place, so it is no longer a candidate.
    assert db.mismatched_translation_entries("1.21.1")[0] == 0
    db.close()


def test_legacy_ai_repair_source_does_not_hide_other_mismatched_sources(db_path):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, key="quality.legacy-repair", en="Use %s")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.execute(
        "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?,?,?)",
        (entry.id, SRC_AI_REPAIR, "使用 %s"),
    )
    db._conn.commit()

    count, candidates = db.mismatched_translation_entries("1.21.1")

    assert count == 1
    assert [(candidate.entry_id, candidate.source_id) for candidate in candidates] == [
        (entry.id, SRC_AI)
    ]
    db.close()


def test_quality_repair_cas_rejects_a_newer_source_revision(db_path):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "item.quality.revision", SRC_AI, "Use", en="Use %s")
    entry = next(
        row
        for row in db.list_entries("1.21.1")[0]
        if row.key == "item.quality.revision"
    )
    expected_revision = _translation_revision(db, entry.id, SRC_AI)
    db._conn.execute(
        "UPDATE translation SET checker='external-writer' "
        "WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()

    result = db.replace_translation_quality_mismatch(
        entry.id,
        SRC_AI,
        "Use",
        "Use %s",
        **_replace_identity(entry),
        expected_revision=expected_revision,
    )

    current = next(
        row for row in db.entry_detail(entry.id).translations if row.source == SRC_AI
    )
    assert result.status == "skipped_changed"
    assert current.zh_tw == "Use" and current.checker == "external-writer"
    db.close()


def test_quality_repair_cas_does_not_follow_translation_to_another_source(db_path):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "item.quality.source", SRC_AI, "Use", en="Use %s")
    entry = next(
        row for row in db.list_entries("1.21.1")[0] if row.key == "item.quality.source"
    )
    expected_revision = _translation_revision(db, entry.id, SRC_AI)
    db._conn.execute(
        "UPDATE translation SET source=? WHERE entry_id=? AND source=?",
        (SRC_JAR_TW, entry.id, SRC_AI),
    )
    db._conn.commit()

    result = db.replace_translation_quality_mismatch(
        entry.id,
        SRC_AI,
        "Use",
        "Use %s",
        **_replace_identity(entry),
        expected_revision=expected_revision,
    )

    current = next(
        row
        for row in db.entry_detail(entry.id).translations
        if row.source == SRC_JAR_TW
    )
    assert result.status == "skipped_changed"
    assert current.zh_tw == "Use"
    db.close()


def test_service_cas_rejects_same_id_from_a_different_candidate(
    db_path, tmp_path, monkeypatch, repair_cache
):
    preview_db = TranslationDB(tmp_path / "preview.db")
    preview_entry = _ai_entry(preview_db, mod_id="preview-mod")
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        preview_db, _options()
    )
    preview_db.close()

    current_db = TranslationDB(db_path)
    current_entry = _ai_entry(current_db, mod_id="current-mod")
    assert preview_entry.id == current_entry.id
    current_db.close()

    monkeypatch.setattr(
        moddb_retranslate_service,
        "open_database",
        lambda **_kwargs: TranslationDB(db_path),
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: ([{**batch[0], "text": "我的世界"}], "AUTO"),
    )

    snap = _run(db_path, preview.entries)

    assert snap["summary"]["status"] == "DONE"
    assert snap["summary"]["updated"] == 0
    assert snap["summary"]["skipped_changed"] == 1
    check = TranslationDB(db_path)
    assert check.get_entry(current_entry.id).zh_tw == "Minecraft"
    check.close()


@pytest.fixture
def repair_cache(monkeypatch):
    events = []
    monkeypatch.setattr(
        moddb_retranslate_service,
        "initialize_translation_cache",
        lambda: events.append(("initialize",)),
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "add_to_cache_with_receipt",
        lambda *args, **kwargs: (
            events.append(("add", args, kwargs))
            or SimpleNamespace(accepted=True, changed=True, generation=1)
        ),
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "save_translation_cache_keys",
        lambda cache_type, keys, *, expected_versions: (
            events.append(("save", cache_type, set(keys), dict(expected_versions)))
            or SimpleNamespace(
                saving_enabled=True,
                saved_keys=tuple(keys),
                superseded_keys=(),
            )
        ),
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "load_config",
        lambda: {"lm_translator": {"token_budget_enabled": False}},
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "_get_default_batch_size",
        lambda _cache_type, _sizes: 20,
    )
    monkeypatch.setattr(
        moddb_translate_service,
        "_get_default_batch_size",
        lambda _cache_type, _sizes: 20,
    )
    return events


def _run(db_path, entries, *, options=None, mode="same_source_ai"):
    session = TaskSession()
    moddb_retranslate_service.run_moddb_retranslate_service(
        options or _options(), session, entries, mode=mode
    )
    return session.snapshot()


def test_service_calls_ai_directly_then_updates_db_before_cache(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()
    calls = []

    def fake_translate(batch, total):
        calls.append((batch, total, list(repair_cache)))
        assert not repair_cache
        return [{**batch[0], "text": "我的世界"}], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", fake_translate
    )
    original_add = moddb_retranslate_service.add_to_cache_with_receipt

    def check_db_then_add(*args, **kwargs):
        check = TranslationDB(db_path)
        assert check.get_entry(entry.id).zh_tw == "我的世界"
        check.close()
        return original_add(*args, **kwargs)

    monkeypatch.setattr(
        moddb_retranslate_service, "add_to_cache_with_receipt", check_db_then_add
    )
    snap = _run(db_path, preview.entries)

    summary = snap["summary"]
    assert len(calls) == 1
    assert summary["updated"] == 1
    assert summary["failed"] == 0
    assert [event[0] for event in repair_cache] == ["initialize", "add", "save"]
    assert snap["status"] == "DONE"


def test_service_keeps_old_translation_on_token_issue_and_api_failure(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s")
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, total: ([{**batch[0], "text": "使用它"}], "AUTO"),
    )
    flagged = _run(db_path, preview.entries)
    check = TranslationDB(db_path)
    assert check.get_entry(entry.id).zh_tw == "Use %s"
    check.close()
    assert flagged["summary"]["flagged"] == 1
    assert repair_cache == []

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda _batch, _total: ([], "FAILED"),
    )
    failed = _run(db_path, preview.entries)
    assert failed["summary"]["status"] == "FAILED"
    assert failed["summary"]["failed"] == 1
    check = TranslationDB(db_path)
    assert check.get_entry(entry.id).zh_tw == "Use %s"
    check.close()

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, total: ([{**batch[0], "text": ""}], "AUTO"),
    )
    empty = _run(db_path, preview.entries)
    assert empty["summary"]["failed"] == 1
    check = TranslationDB(db_path)
    assert check.get_entry(entry.id).zh_tw == "Use %s"
    check.close()
    assert repair_cache == []


def test_invalid_result_stops_before_next_batch(db_path, monkeypatch, repair_cache):
    db = TranslationDB(db_path)
    for key in ("a", "b", "c"):
        _ai_entry(db, key=key)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(write_cache=False)
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "_get_default_batch_size",
        lambda _cache_type, _sizes: 2,
    )
    calls = []

    def fake_translate(batch, total):
        calls.append([item["_entry_id"] for item in batch])
        valid = {**batch[0], "text": "我的世界"}
        invalid = {**batch[1], "_entry_id": -1, "text": "錯誤結果"}
        return [valid, invalid], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", fake_translate
    )

    snap = _run(db_path, preview.entries, options=_options(write_cache=False))

    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert snap["summary"]["status"] == "FAILED"
    assert snap["summary"]["failed"] == 1
    assert snap["summary"]["not_submitted_candidates"] == 1
    assert snap["summary"]["unprocessed_candidates"] == 1
    check = TranslationDB(db_path)
    assert check.get_entry(calls[0][0]).zh_tw == "我的世界"
    assert check.get_entry(calls[0][1]).zh_tw == "Minecraft"
    third_id = next(
        row.entry_id for row in preview.entries if row.entry_id not in calls[0]
    )
    assert check.get_entry(third_id).zh_tw == "Minecraft"
    check.close()
    assert repair_cache == []


def test_excess_results_stop_before_next_batch(db_path, monkeypatch, repair_cache):
    db = TranslationDB(db_path)
    _ai_entry(db, key="a")
    _ai_entry(db, key="b")
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(write_cache=False)
    )
    db.close()
    _set_repair_batch_size(monkeypatch, 1)
    calls = []

    def fake_translate(batch, total):
        calls.append([item["_entry_id"] for item in batch])
        valid = {**batch[0], "text": "我的世界"}
        extra = {**batch[0], "text": "多餘結果"}
        return [valid, extra], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", fake_translate
    )

    snap = _run(db_path, preview.entries, options=_options(write_cache=False))

    assert len(calls) == 1
    assert snap["summary"]["status"] == "FAILED"
    assert snap["summary"]["failed"] == 0
    assert snap["summary"]["not_submitted_candidates"] == 1
    assert snap["summary"]["unprocessed_candidates"] == 1
    check = TranslationDB(db_path)
    assert check.get_entry(calls[0][0]).zh_tw == "我的世界"
    pending_id = next(
        row.entry_id for row in preview.entries if row.entry_id not in calls[0]
    )
    assert check.get_entry(pending_id).zh_tw == "Minecraft"
    check.close()
    assert repair_cache == []


def test_unexpected_batch_exception_keeps_candidate_counters_distinct(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entries = [_ai_entry(db, key=f"exception-{index}") for index in range(3)]
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(write_cache=False)
    )
    db.close()
    _set_repair_batch_size(monkeypatch, 2)
    failing_id = entries[1].id
    original_replace = TranslationDB.replace_ai_translation

    def fail_during_second_result(self, entry_id, *args, **kwargs):
        if entry_id == failing_id:
            raise RuntimeError("simulated CAS failure after an earlier failed row")
        return original_replace(self, entry_id, *args, **kwargs)

    monkeypatch.setattr(
        TranslationDB, "replace_ai_translation", fail_during_second_result
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: (
            [
                {**batch[0], "_entry_id": -1, "text": "invalid"},
                {**batch[1], "text": "有效譯文"},
            ],
            "AUTO",
        ),
    )

    snap = _run(db_path, preview.entries, options=_options(write_cache=False))

    summary = snap["summary"]
    assert summary["status"] == "FAILED"
    assert summary["attempted_candidates"] == 2
    assert summary["processed_candidates"] == 1
    assert summary["failed"] == 1
    assert summary["not_submitted_candidates"] == 1
    assert summary["unprocessed_candidates"] == 2
    assert any("已送出 2，已完成 1，其中失敗 1" in row.text for row in snap["logs"])
    assert repair_cache == []


def test_identical_result_is_unchanged_and_does_not_touch_cache(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, total: ([{**batch[0], "text": "Minecraft"}], "AUTO"),
    )

    snap = _run(db_path, preview.entries)

    assert snap["summary"]["unchanged"] == 1
    assert snap["summary"]["updated"] == 0
    assert repair_cache == []
    check = TranslationDB(db_path)
    assert check.entry_detail(entry.id).history == []
    check.close()


def test_cache_write_failure_is_reported_without_rolling_back_database(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, total: ([{**batch[0], "text": "我的世界"}], "AUTO"),
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "add_to_cache_with_receipt",
        lambda *a, **k: SimpleNamespace(accepted=False, changed=False),
    )

    snap = _run(db_path, preview.entries)

    assert snap["summary"]["updated"] == 1
    assert snap["summary"]["cache_failed"] == 1
    assert snap["summary"]["cache_add_failed"] == 1
    assert snap["summary"]["cache_keys_changed"] == 0
    assert snap["summary"]["cache_keys_saved"] == 0
    check = TranslationDB(db_path)
    assert check.get_entry(entry.id).zh_tw == "我的世界"
    check.close()


@pytest.mark.parametrize("failure_stage", ["initialize", "add", "save"])
def test_cache_exceptions_do_not_stop_later_repair_batches(
    db_path, monkeypatch, repair_cache, failure_stage
):
    db = TranslationDB(db_path)
    lang_entry = _ai_entry(db, key="item.lang")
    patch_entry = _ai_entry(db, key="item.patchouli", kind=KIND_PATCHOULI)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()

    calls = {"initialize": 0, "add": 0, "save": 0}
    batches = []

    def fail_once(stage):
        calls[stage] += 1
        if stage == failure_stage and calls[stage] == 1:
            raise RuntimeError(f"simulated {stage} failure")

    def fake_translate(batch, _total):
        batches.append({item["cache_type"] for item in batch})
        return [{**item, "text": "我的世界"} for item in batch], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", fake_translate
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "initialize_translation_cache",
        lambda: fail_once("initialize"),
    )

    def add_to_cache_with_receipt(*_args, **_kwargs):
        fail_once("add")
        return SimpleNamespace(accepted=True, changed=True, generation=1)

    def save_translation_cache_keys(_cache_type, keys, *, expected_versions):
        fail_once("save")
        assert set(expected_versions) == set(keys)
        return SimpleNamespace(
            saving_enabled=True, saved_keys=tuple(keys), superseded_keys=()
        )

    monkeypatch.setattr(
        moddb_retranslate_service,
        "add_to_cache_with_receipt",
        add_to_cache_with_receipt,
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "save_translation_cache_keys",
        save_translation_cache_keys,
    )

    snap = _run(db_path, preview.entries)

    assert batches == [{"lang"}, {"patchouli"}]
    assert snap["summary"]["status"] == "DONE"
    assert snap["status"] == "DONE"
    assert snap["summary"]["updated"] == 2
    assert snap["summary"]["cache_failed"] == 1
    assert snap["summary"]["failed"] == 0
    assert snap["summary"]["cache_save_failed"] == 0
    assert snap["summary"]["cache_keys_saved"] == (2 if failure_stage == "save" else 1)
    check = TranslationDB(db_path)
    assert check.get_entry(lang_entry.id).zh_tw == "我的世界"
    assert check.get_entry(patch_entry.id).zh_tw == "我的世界"
    check.close()


def test_service_skips_when_effective_source_changes_after_preview(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()

    def change_source_then_translate(batch, total):
        changed = TranslationDB(db_path)
        changed.save_manual(entry.id, "人工譯文", propagate=False)
        changed.close()
        return [{**batch[0], "text": "我的世界"}], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", change_source_then_translate
    )
    snap = _run(db_path, preview.entries)

    check = TranslationDB(db_path)
    assert check.get_entry(entry.id).zh_tw == "人工譯文"
    assert check.get_entry(entry.id).source == SRC_MANUAL
    check.close()
    assert snap["summary"]["skipped_changed"] == 1
    assert repair_cache == []


def test_service_cancellation_preserves_old_translation(
    db_path, monkeypatch, repair_cache
):
    from translation_tool.utils.cancellation import TaskCancelled

    db = TranslationDB(db_path)
    entry = _ai_entry(db)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda _batch, _total: (_ for _ in ()).throw(TaskCancelled()),
    )

    snap = _run(db_path, preview.entries)

    check = TranslationDB(db_path)
    assert check.get_entry(entry.id).zh_tw == "Minecraft"
    check.close()
    assert snap["summary"]["status"] == "CANCELLED"
    assert repair_cache == []


@pytest.mark.usefixtures("all_source_rows")
def test_quality_repair_deduplicates_equivalent_rows_and_keeps_source_cas(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s", key="a/very/long/path/item.name")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    db.save_manual(entry.id, "Use", propagate=False)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()

    assert preview.selected_count == 2
    assert preview.ai_representatives == 1
    assert preview.dedup_reused_candidates == 1
    calls = []

    def fake_translate(batch, _total):
        calls.append(batch)
        return [{**batch[0], "text": "Use %s：正確"}], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", fake_translate
    )
    snap = _run(db_path, preview.entries, mode="quality_mismatch")

    assert [len(batch) for batch in calls] == [1]
    summary = snap["summary"]
    assert summary["candidates"] == 2
    assert summary["ai_representatives"] == 1
    assert summary["ai_submitted_items"] == 1
    assert summary["ai_validated_items"] == 1
    assert summary["dedup_reused_candidates"] == 1
    assert summary["processed_candidates"] == 2
    assert summary["updated"] == 2
    assert summary["cache_keys_changed"] == 1
    assert summary["cache_keys_saved"] == 1

    check = TranslationDB(db_path)
    rows = {row.source: row for row in check.entry_detail(entry.id).translations}
    assert rows[SRC_AI].zh_tw == "Use %s：正確"
    assert rows[SRC_MANUAL].zh_tw == "Use %s：正確"
    history_sources = {
        row.source_id
        for row in check.entry_detail(entry.id).history
        if row.action == "quality_repair"
    }
    assert history_sources == {SRC_AI, SRC_MANUAL}
    check.close()

    logs = "\n".join(row.text for row in snap["logs"])
    assert f"[MC 1.21.1][mod=foo][kind=lang][source={SRC_AI}:AI 機翻]" in logs
    assert f"[source={SRC_MANUAL}:人工-未審核]" in logs
    assert "[key=a/very/long/path/item.name]" in logs


@pytest.mark.usefixtures("all_source_rows")
def test_invalid_representative_never_fans_out_to_source_rows(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s", key="same.entry")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    db.save_manual(entry.id, "Use", propagate=False)
    history_before = db.entry_detail(entry.id).history
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: (
            [{**batch[0], "_entry_id": -1, "text": "Use %s：正確"}],
            "AUTO",
        ),
    )
    snap = _run(
        db_path,
        preview.entries,
        options=_options(write_cache=False),
        mode="quality_mismatch",
    )

    check = TranslationDB(db_path)
    rows = {row.source: row for row in check.entry_detail(entry.id).translations}
    assert rows[SRC_AI].zh_tw == "Use"
    assert rows[SRC_MANUAL].zh_tw == "Use"
    assert check.entry_detail(entry.id).history == history_before
    check.close()
    assert snap["summary"]["ai_representatives"] == 1
    assert snap["summary"]["ai_submitted_items"] == 1
    assert snap["summary"]["ai_validated_items"] == 0
    assert snap["summary"]["dedup_mapped_candidates"] == 1
    assert snap["summary"]["dedup_reused_candidates"] == 0
    assert snap["summary"]["failed"] == 2
    assert snap["summary"]["updated"] == 0


@pytest.mark.usefixtures("all_source_rows")
def test_candidate_log_uses_database_local_source_catalog(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    db._conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('custom_sources', ?)",
        (json.dumps({"譯文團隊": 100}),),
    )
    db._conn.commit()
    db.close()

    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s", key="custom/source/path")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    _ingest(db, "1.21.1", "foo", entry.key, 100, "Use", en="Use %s")
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: ([{**batch[0], "text": "Use %s：正確"}], "AUTO"),
    )

    snap = _run(
        db_path,
        preview.entries,
        options=_options(write_cache=False),
        mode="quality_mismatch",
    )

    logs = "\n".join(row.text for row in snap["logs"])
    assert "[source=100:譯文團隊]" in logs
    assert "[key=custom/source/path]" in logs
    assert snap["summary"]["updated"] == 2


@pytest.mark.usefixtures("all_source_rows")
def test_fanout_stale_second_source_keeps_first_cas_and_checker(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s", key="same.entry")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    db.save_manual(entry.id, "Use", propagate=False)
    before = {row.source: row for row in db.entry_detail(entry.id).translations}[
        SRC_MANUAL
    ]
    before_revision = db._conn.execute(
        "SELECT revision FROM translation WHERE entry_id=? AND source=?",
        (entry.id, SRC_MANUAL),
    ).fetchone()[0]
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()

    def stale_manual_then_translate(batch, _total):
        external = TranslationDB(db_path)
        external._conn.execute(
            "UPDATE translation SET checker='external-writer', revision=revision+1 "
            "WHERE entry_id=? AND source=?",
            (entry.id, SRC_MANUAL),
        )
        external._conn.commit()
        external.close()
        return [{**batch[0], "text": "Use %s：正確"}], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", stale_manual_then_translate
    )
    snap = _run(
        db_path,
        preview.entries,
        options=_options(write_cache=False),
        mode="quality_mismatch",
    )

    check = TranslationDB(db_path)
    detail = check.entry_detail(entry.id)
    rows = {row.source: row for row in detail.translations}
    assert rows[SRC_AI].zh_tw == "Use %s：正確"
    assert SRC_AI_REPAIR not in rows
    assert rows[SRC_MANUAL].zh_tw == before.zh_tw
    assert rows[SRC_MANUAL].checker == "external-writer"
    assert rows[SRC_MANUAL].review_status == before.review_status
    current_revision = check._conn.execute(
        "SELECT revision FROM translation WHERE entry_id=? AND source=?",
        (entry.id, SRC_MANUAL),
    ).fetchone()[0]
    assert current_revision == before_revision + 1
    assert {
        row.source_id for row in detail.history if row.action == "quality_repair"
    } == {SRC_AI}
    check.close()
    assert snap["summary"]["updated"] == 1
    assert snap["summary"]["skipped_changed"] == 1
    assert snap["summary"]["dedup_reused_candidates"] == 1


@pytest.mark.usefixtures("all_source_rows")
def test_cancel_between_fanout_targets_preserves_committed_row_and_review_state(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s", key="same.entry")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    db.save_manual(entry.id, "Use", propagate=False)
    before_manual = {row.source: row for row in db.entry_detail(entry.id).translations}[
        SRC_MANUAL
    ]
    before_revision = db._conn.execute(
        "SELECT revision FROM translation WHERE entry_id=? AND source=?",
        (entry.id, SRC_MANUAL),
    ).fetchone()[0]
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()

    session = TaskSession()
    original_replace = TranslationDB.replace_translation_quality_mismatch

    def cancel_after_first_commit(self, *args, **kwargs):
        result = original_replace(self, *args, **kwargs)
        if result.status == "updated":
            session.request_cancel()
        return result

    monkeypatch.setattr(
        TranslationDB, "replace_translation_quality_mismatch", cancel_after_first_commit
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: ([{**batch[0], "text": "Use %s：正確"}], "AUTO"),
    )
    moddb_retranslate_service.run_moddb_retranslate_service(
        _options(write_cache=False),
        session,
        preview.entries,
        mode="quality_mismatch",
    )
    snap = session.snapshot()

    check = TranslationDB(db_path)
    detail = check.entry_detail(entry.id)
    rows = {row.source: row for row in detail.translations}
    assert rows[SRC_AI].zh_tw == "Use %s：正確"
    assert SRC_AI_REPAIR not in rows
    assert (
        rows[SRC_MANUAL].zh_tw,
        rows[SRC_MANUAL].checker,
        rows[SRC_MANUAL].review_status,
    ) == (
        before_manual.zh_tw,
        before_manual.checker,
        before_manual.review_status,
    )
    current_revision = check._conn.execute(
        "SELECT revision FROM translation WHERE entry_id=? AND source=?",
        (entry.id, SRC_MANUAL),
    ).fetchone()[0]
    assert current_revision == before_revision
    assert [
        row.source_id for row in detail.history if row.action == "quality_repair"
    ] == [SRC_AI]
    check.close()
    assert snap["summary"]["status"] == "CANCELLED"
    assert snap["summary"]["updated"] == 1
    assert snap["summary"]["processed_candidates"] == 1
    assert snap["summary"]["unprocessed_candidates"] == 1
    assert snap["summary"]["failed"] == 0


@pytest.mark.usefixtures("all_source_rows")
def test_cancel_after_result_before_fanout_leaves_all_source_rows_unchanged(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, en="Use %s", key="same.entry")
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    db.save_manual(entry.id, "Use", propagate=False)
    history_before = db.entry_detail(entry.id).history
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()

    session = TaskSession()

    def cancel_when_result_returns(batch, _total):
        session.request_cancel()
        return [{**batch[0], "text": "Use %s：正確"}], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        cancel_when_result_returns,
    )
    moddb_retranslate_service.run_moddb_retranslate_service(
        _options(write_cache=False),
        session,
        preview.entries,
        mode="quality_mismatch",
    )
    snap = session.snapshot()

    check = TranslationDB(db_path)
    detail = check.entry_detail(entry.id)
    assert {row.source: row.zh_tw for row in detail.translations} == {
        SRC_AI: "Use",
        SRC_MANUAL: "Use",
    }
    assert detail.history == history_before
    check.close()
    assert snap["summary"]["status"] == "CANCELLED"
    assert snap["summary"]["ai_submitted_items"] == 1
    assert snap["summary"]["ai_validated_items"] == 0
    assert snap["summary"]["processed_candidates"] == 0
    assert snap["summary"]["unprocessed_candidates"] == 2


@pytest.mark.usefixtures("all_source_rows")
def test_quality_repair_dedup_is_limited_to_same_entry_and_profile(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    first = _ai_entry(db, en="Use %s", key="same.source.first")
    second = _ai_entry(db, en="Use %s", key="same.source.second")
    for entry in (first, second):
        db._conn.execute(
            "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
            (entry.id, SRC_AI),
        )
        db._conn.commit()
        db.save_manual(entry.id, "Use", propagate=False)
    patch = _ai_entry(db, en="Use %s", key="same.source.patch", kind=KIND_PATCHOULI)
    db._conn.execute(
        "UPDATE translation SET zh_tw='Use' WHERE entry_id=? AND source=?",
        (patch.id, SRC_AI),
    )
    db._conn.commit()
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    db.close()

    assert preview.selected_count == 5
    assert preview.ai_representatives == 3
    assert preview.dedup_reused_candidates == 2
    no_reuse_representatives, _fanout = (
        moddb_retranslate_service._deduplicate_retranslation_items(
            moddb_retranslate_service._build_retranslation_items(preview.entries),
            enabled=False,
        )
    )
    assert len(no_reuse_representatives) == 5
    calls = []

    def fake_translate(batch, _total):
        calls.append((batch[0]["cache_type"], [item["path"] for item in batch]))
        return [{**item, "text": "Use %s：正確"} for item in batch], "AUTO"

    monkeypatch.setattr(
        moddb_retranslate_service, "translate_batch_smart", fake_translate
    )
    _run(
        db_path,
        preview.entries,
        options=_options(write_cache=False),
        mode="quality_mismatch",
    )

    assert calls == [
        ("lang", [first.key, second.key]),
        ("patchouli", [patch.key]),
    ]


def test_repair_progress_does_not_apply_patchouli_sample_to_lang(monkeypatch):
    monkeypatch.setattr(
        moddb_retranslate_service,
        "_get_default_batch_size",
        lambda _cache_type, _sizes: 300,
    )
    items = [
        *({"cache_type": "patchouli"} for _ in range(79)),
        *({"cache_type": "lang"} for _ in range(221)),
    ]
    fanout = {id(item): (item,) for item in items}
    tracker = moddb_retranslate_service._RepairRunProgress(
        items,
        fanout,
        total_candidates=300,
        lm_cfg={},
        started_mono=0.0,
        started_wall=100.0,
    )

    tracker.update("patchouli", candidate_count=79, representative_count=79, elapsed=20)
    live = tracker.live(processed=79, now_mono=20.0, now_wall=120.0)

    assert live["batch_done"] == 1
    assert live["batch_est"] == 2
    assert live["processed"] == 79
    assert live["total"] == 300
    assert live["eta_sec"] is None
    assert "Lang 尚無耗時樣本" in live["eta_note"]

    tracker.update("lang", candidate_count=100, representative_count=100, elapsed=10)
    sampled = tracker.live(processed=179, now_mono=30.0, now_wall=130.0)
    assert sampled["eta_sec"] == pytest.approx(12.1)
    assert sampled["eta_note"] == ""
    assert sampled["profile_progress"]["lang"]["planned_remaining_batches"] == 1
    assert sampled["profile_progress"]["lang"]["ai_submitted_items"] == 100

    tracker.update("lang", candidate_count=121, representative_count=121, elapsed=10)
    done = tracker.live(processed=300, now_mono=40.0, now_wall=140.0)
    assert done["batch_done"] == 3
    assert done["batch_est"] == 3
    assert done["processed"] == done["total"] == 300
    assert done["eta_sec"] == 0


def test_batch_planner_handles_100k_candidates_with_bounded_windows(monkeypatch):
    preferred = 64
    monkeypatch.setattr(
        moddb_retranslate_service,
        "_get_default_batch_size",
        lambda _cache_type, _sizes: preferred,
    )
    windows = []
    monkeypatch.setattr(
        moddb_retranslate_service,
        "select_batch_size",
        lambda items, _profile, cap, _cfg: (
            windows.append(len(items)) or min(len(items), cap)
        ),
    )
    items = [{"cache_type": "lang"} for _ in range(100_000)]

    planned = moddb_retranslate_service._count_planned_batches(
        items, {"token_budget_enabled": False}
    )

    assert planned == (len(items) + preferred - 1) // preferred
    assert len(windows) == planned
    assert max(windows) == preferred


def test_batch_planner_checks_deadline_while_scanning_profile(monkeypatch):
    times = iter((0.0, 0.0, 61.0))
    monkeypatch.setattr(
        moddb_retranslate_service, "monotonic", lambda: next(times, 61.0)
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "_get_default_batch_size",
        lambda _cache_type, _sizes: 32,
    )
    items = [{"cache_type": "lang"} for _ in range(1000)]

    with pytest.raises(TimeoutError, match="時間預算"):
        moddb_retranslate_service._count_planned_batches(
            items, {"token_budget_enabled": False}, deadline=60.0
        )


def test_repair_progress_replans_only_when_profile_budget_changes(monkeypatch):
    selected_size = 64
    signature = 0
    monkeypatch.setattr(
        moddb_retranslate_service,
        "_get_default_batch_size",
        lambda _cache_type, _sizes: 64,
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "_batch_plan_signature",
        lambda _cache_type, _cfg: (64, signature),
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "select_batch_size",
        lambda items, _profile, _cap, _cfg: min(len(items), selected_size),
    )
    items = [{"cache_type": "lang"} for _ in range(130)]
    fanout = {id(item): (item,) for item in items}
    tracker = moddb_retranslate_service._RepairRunProgress(
        items, fanout, total_candidates=130, lm_cfg={}
    )

    assert tracker.live()["batch_est"] == 3
    tracker.update("lang", candidate_count=64, representative_count=64, elapsed=1)
    signature = 1
    selected_size = 32

    live = tracker.live(processed=64)

    assert live["profile_progress"]["lang"]["planned_remaining_batches"] == 3
    assert live["batch_est"] == 4


def test_batch_planner_checks_cancellation_while_scanning_profile(monkeypatch):
    from translation_tool.utils.cancellation import TaskCancelled

    monkeypatch.setattr(
        moddb_retranslate_service,
        "_get_default_batch_size",
        lambda _cache_type, _sizes: 32,
    )
    checks = 0

    def cancel_during_planning():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise TaskCancelled()

    items = [{"cache_type": "lang"} for _ in range(1000)]

    with pytest.raises(TaskCancelled):
        moddb_retranslate_service._count_planned_batches(
            items,
            {"token_budget_enabled": False},
            check=cancel_during_planning,
        )

    assert checks == 3


def test_repair_progress_empty_run_has_no_batches_or_eta(monkeypatch):
    tracker = moddb_retranslate_service._RepairRunProgress(
        [], {}, total_candidates=0, lm_cfg={}
    )

    live = tracker.live(processed=0)

    assert live["batch_done"] == live["batch_est"] == 0
    assert live["processed"] == live["total"] == 0
    assert live["eta_sec"] == 0


def test_candidate_detail_log_cap_reports_omitted_categories(
    db_path, monkeypatch, repair_cache
):
    db = TranslationDB(db_path)
    for index in range(25):
        _ai_entry(db, key=f"long.path.entry-{index:02d}")
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: (
            [{**item, "text": "我的世界"} for item in batch],
            "AUTO",
        ),
    )

    snap = _run(db_path, preview.entries, options=_options(write_cache=False))

    candidate_lines = [row.text for row in snap["logs"] if "[MC 1.21.1]" in row.text]
    summary_lines = [row.text for row in snap["logs"] if "已省略" in row.text]
    assert len(candidate_lines) == 20
    assert snap["summary"]["log_details_omitted"] == 5
    assert summary_lines == ["ℹ️ 已省略 5 筆候選明細（updated 5 筆）"]


def test_preview_confirmation_cancel_and_scope_invalidation(db_path, monkeypatch):
    db = TranslationDB(db_path)
    _ai_entry(db)
    page = mock_page()
    panel = translate_panel.TranslatePanel(page, lambda: db)
    _select_repair_mode(panel)
    panel.version_dd.value = "1.21.1"
    panel.limit_field.value = "1"
    launches = []
    monkeypatch.setattr(
        translate_panel,
        "launch_page_operation",
        _inline_operation_launcher(launches),
    )
    monkeypatch.setattr(
        retranslation_controller,
        "launch_page_operation",
        _inline_operation_launcher(launches),
    )

    snacks = []
    monkeypatch.setattr(
        translate_panel,
        "show_snack",
        lambda _page, message, _tone: snacks.append(message),
    )
    monkeypatch.setattr(
        retranslation_controller,
        "show_snack",
        lambda _page, message, _tone: snacks.append(message),
    )
    instructions = panel.repair_explainer.value
    assert "目前生效來源為 AI 機翻" in instructions
    assert "只更新 AI 來源" in instructions
    assert panel.repair_start_btn.disabled is False
    panel.confirm_retranslation()
    assert "請先按「預覽候選譯文」" in snacks[-1]
    assert page.overlay == []

    panel.preview_retranslation()
    _drain_page_tasks(page)

    assert panel._repair_preview.selected_count == 1
    assert panel.repair_start_btn.disabled is False
    panel.confirm_retranslation()
    page.overlay[-1].actions[0].on_click(None)
    assert panel._running is False

    panel.confirm_retranslation()
    assert "確認重新翻譯舊 AI 譯文" in str(page.overlay[-1].title.value)
    preview = panel._repair_preview
    service_calls = []
    monkeypatch.setattr(
        translate_panel,
        "run_moddb_retranslate_service",
        lambda options, session, entries, **kwargs: service_calls.append(
            (options, session, entries, kwargs)
        ),
    )
    panel._poller.start = lambda *_args: None
    page.overlay[-1].actions[1].on_click(None)
    assert launches[-1][1]["name"] == "Mod 資料庫舊 AI 重翻"
    assert launches[-1][1]["owner"] == "moddb-retranslate"
    assert launches[-1][1]["task_session"] is panel.session
    assert panel.session.operation_registry is None
    launches[-1][0]()
    assert service_calls[0][0].version == "1.21.1"
    assert service_calls[0][0].limit == 1
    assert service_calls[0][2] is preview.entries
    assert service_calls[0][3]["mode"] == "same_source_ai"

    panel._running = False
    panel.mod_dd.value = "foo"
    panel._on_scope_changed()
    assert panel._repair_preview is None
    assert panel.repair_start_btn.disabled is False
    panel.confirm_retranslation()
    assert "請先按「預覽候選譯文」" in snacks[-1]
    panel.preview_retranslation()
    _drain_page_tasks(page)
    assert panel._repair_preview is not None
    panel._get_db = lambda: None
    panel.preview_retranslation()
    assert panel._repair_preview is None
    assert panel.repair_start_btn.disabled is False
    panel.confirm_retranslation()
    assert "請先按「預覽候選譯文」" in snacks[-1]
    db.close()


@pytest.mark.parametrize("identity_change", ["path", "priority"])
def test_database_change_invalidates_open_repair_confirmation(
    tmp_path, monkeypatch, identity_change
):
    preview_db = TranslationDB(tmp_path / "preview.db")
    preview_entry = _ai_entry(preview_db, mod_id="preview-mod")
    current_db = TranslationDB(tmp_path / "current.db")
    current_entry = _ai_entry(current_db, mod_id="current-mod")
    assert preview_entry.id == current_entry.id

    active_db = [preview_db]
    page = mock_page()
    panel = translate_panel.TranslatePanel(page, lambda: active_db[0])
    _select_repair_mode(panel)
    panel.version_dd.value = "1.21.1"
    launches = []
    monkeypatch.setattr(
        retranslation_controller,
        "launch_page_operation",
        _inline_operation_launcher(launches),
    )
    panel.preview_retranslation()
    _drain_page_tasks(page)
    panel.confirm_retranslation()
    stale_dialog = page.overlay[-1]

    snacks = []
    monkeypatch.setattr(
        translate_panel,
        "show_snack",
        lambda _page, message, _tone: snacks.append(message),
    )
    if identity_change == "path":
        active_db[0] = current_db
    else:
        preview_db.set_priority(tuple(reversed(preview_db.priority)))
    panel.refresh_scope()

    assert panel._repair_preview is None
    stale_dialog.actions[1].on_click(None)
    assert len(launches) == 1
    assert "預覽已失效" in snacks[-1]
    target_db = active_db[0]
    target_entry = current_entry if target_db is current_db else preview_entry
    assert target_db.get_entry(target_entry.id).zh_tw == "Minecraft"
    preview_db.close()
    current_db.close()


def test_retranslation_preview_shows_ai_source_profiles_and_samples(
    db_path, monkeypatch
):
    _set_repair_batch_size(monkeypatch, 1)
    db = TranslationDB(db_path)
    _ai_entry(db, key="lang")
    _ai_entry(db, key="patchouli", kind=KIND_PATCHOULI)
    manual = _ai_entry(db, key="manual")
    db.save_manual(manual.id, "Minecraft", propagate=False)
    _ingest(db, "1.21.1", "foo", "jar", SRC_JAR_TW, "Minecraft")
    _ingest(db, "1.21.1", "foo", "custom", SRC_CUSTOM, "Minecraft")

    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    _select_repair_mode(panel)
    panel.version_dd.value = "1.21.1"
    page = panel._page
    monkeypatch.setattr(
        retranslation_controller,
        "launch_page_operation",
        _inline_operation_launcher(),
    )
    assert panel.repair_samples.visible is False
    panel.preview_retranslation()
    _drain_page_tasks(page)

    preview = panel._repair_preview
    assert preview is not None
    assert preview.source == SRC_AI
    assert preview.total_candidates == 2
    assert preview.selected_count == 2
    assert dict(preview.profile_counts) == {"lang": 1, "patchouli": 1}
    assert preview.estimated_batches == 2
    text = panel.repair_preview_text.value
    assert "來源：AI 機翻" in text
    assert "人工、模組自帶及其他來源不會被重新翻譯" in text
    assert "Lang：1 筆" in text
    assert "Patchouli：1 筆" in text
    assert "唯一 AI 修復項目：2 個" in text
    assert "預計共用結果給 0 個來源項目" in text
    assert "規劃批次：2（不等於實際 HTTP 請求次數）" in text
    assert len(panel.repair_samples.controls) == 2
    assert panel.repair_samples.visible is True
    first_card = panel.repair_samples.controls[0]
    # 詳細內容（格式差異檢視）收合時不建立，展開才建，翻頁才不會卡
    assert not _contains_control_text(first_card, "格式差異檢視")
    _find_control_by_text(first_card, "查看詳細資料").on_click()
    assert _contains_control_text(first_card, "格式差異檢視")
    assert "顯示 2 / 2 個翻譯 Key" in panel.repair_preview_count.value
    panel.repair_preview_search.value = "patchouli"
    retranslation_controller.refresh_preview_cards(panel)
    assert len(panel.repair_samples.controls) == 1
    assert "顯示 1 / 2 個翻譯 Key" in panel.repair_preview_count.value
    db.close()


@pytest.mark.usefixtures("all_source_rows")
def test_retranslation_preview_groups_same_key_sources_and_filters(db_path):
    db = TranslationDB(db_path)
    entry = _ai_entry(db, key="shared", en="Use %s")
    db.save_manual(entry.id, "Use", propagate=False)
    _ingest(db, "1.21.1", "foo", "shared", SRC_JAR_TW, "Use", en="Use %s")
    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    _select_repair_mode(panel, "quality_mismatch")
    panel.version_dd.value = "1.21.1"
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )

    retranslation_controller._display_preview_result(
        panel,
        _options(),
        panel._database_identity(panel._get_db()),
        preview,
    )

    assert preview.ai_representatives == 1
    assert preview.selected_count == 2
    assert len(panel.repair_samples.controls) == 1
    assert "顯示 1 / 1 個翻譯 Key" in panel.repair_preview_count.value
    panel.repair_preview_search.value = "not-found"
    retranslation_controller.refresh_preview_cards(panel)
    assert panel.repair_samples.controls == []
    assert panel.repair_samples.visible is False
    assert "沒有符合目前篩選條件" in panel.repair_preview_count.value
    db.close()


def test_preview_uses_isolated_readonly_db_during_ui_queries_and_discards_stale_scope(
    db_path, monkeypatch
):
    db = TranslationDB(db_path)
    _ai_entry(db)
    page = mock_page()
    registry = OperationRegistry()
    page.operation_registry = registry
    panel = translate_panel.TranslatePanel(page, lambda: db)
    _select_repair_mode(panel)
    panel.version_dd.value = "1.21.1"

    entered = threading.Event()
    release = threading.Event()
    ui_query_done = threading.Event()
    ui_query_result = {}
    original_preview = moddb_retranslate_service.preview_same_source_ai_retranslation

    def blocking_preview(active_db, options, **kwargs):
        assert active_db is not db
        assert active_db.path == db.path
        assert active_db.priority == db.priority
        assert active_db.readonly is True
        with active_db._lock:
            entered.set()
            assert release.wait(3)
            return original_preview(active_db, options, **kwargs)

    monkeypatch.setattr(
        moddb_retranslate_service,
        "preview_same_source_ai_retranslation",
        blocking_preview,
    )

    panel.preview_retranslation()
    assert entered.wait(2)
    assert panel._repair_preview is None
    assert panel._repair_preview_running is True
    assert "正在背景查詢" in panel.repair_preview_text.value
    handle = registry.active()[0]
    assert handle.descriptor.owner == "moddb-retranslate-preview"
    assert handle.descriptor.cancellation == CancellationPolicy.NON_CANCELLABLE
    assert handle.descriptor.commit == CommitPolicy.EPHEMERAL
    assert handle.descriptor.durability == DurabilityPolicy.RECOMPUTABLE
    assert handle.descriptor.shutdown == ShutdownPolicy.DRAIN_ONLY

    ui_mod_ids = panel.mod_ids()

    def run_ui_database_queries():
        try:
            ui_query_result["versions"] = db.versions()
            ui_query_result["untranslated"] = db.count_untranslated(
                "1.21.1", ui_mod_ids
            )
        except Exception as exc:  # noqa: BLE001 - surface probe failure to assertion
            ui_query_result["error"] = exc
        finally:
            ui_query_done.set()

    ui_query = threading.Thread(target=run_ui_database_queries, daemon=True)
    ui_query.start()
    try:
        assert ui_query_done.wait(1), (
            "the UI-owned DB connection must remain usable while preview holds its lock"
        )
        ui_query.join(1)
        assert "error" not in ui_query_result
        assert "1.21.1" in ui_query_result["versions"]
        assert isinstance(ui_query_result["untranslated"], int)

        panel.mod_dd.value = "changed-scope"
        panel._on_scope_changed()
        registry.begin_shutdown()
        assert handle.cancel_requested is False
        assert registry.active_count() == 1
    finally:
        release.set()
        ui_query.join(1)

    assert registry.wait_for_idle(timeout=2)
    _drain_page_tasks(page)
    assert panel._repair_preview is None
    assert panel._repair_preview_running is False
    assert "舊預覽結果已丟棄" in panel.repair_preview_text.value
    db.close()


def test_readonly_retranslation_preview_has_sql_execution_deadline(
    db_path, monkeypatch
):
    db = TranslationDB(db_path)
    _ai_entry(db)
    priority = db.priority
    db.close()

    ticks = iter((0.0, 0.0, 100.0, 100.0))
    monkeypatch.setattr(
        moddb_retranslate_service, "monotonic", lambda: next(ticks, 100.0)
    )
    monkeypatch.setattr(moddb_retranslate_service, "PREVIEW_SQL_TIMEOUT_SEC", 60.0)
    monkeypatch.setattr(moddb_retranslate_service, "PREVIEW_SQL_PROGRESS_OPCODES", 1)

    with pytest.raises(TimeoutError, match="時間預算") as caught:
        moddb_retranslate_service.preview_same_source_ai_retranslation_from_path(
            db_path, priority, _options()
        )

    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
    assert "interrupt" in str(caught.value.__cause__).casefold()


def test_retranslation_preview_python_transforms_check_deadline(monkeypatch):
    from types import SimpleNamespace

    ticks = iter((0.0, 0.0, 61.0))
    monkeypatch.setattr(
        moddb_retranslate_service, "monotonic", lambda: next(ticks, 61.0)
    )
    entries = [
        SimpleNamespace(
            entry_id=index,
            kind=KIND_LANG,
            mod_id="mod",
            key=f"key.{index}",
            en_us="same",
            current_ai_translation="same",
            mc_version="1.21.1",
        )
        for index in (1, 2)
    ]

    with pytest.raises(TimeoutError, match="時間預算"):
        moddb_retranslate_service._build_retranslation_items(entries, deadline=60.0)


def test_retranslation_worker_is_page_owned_cancellable_and_drained(
    db_path, monkeypatch
):
    db = TranslationDB(db_path)
    _ai_entry(db)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    page_a = mock_page()
    registry_a = OperationRegistry()
    page_a.operation_registry = registry_a
    page_b = mock_page()
    registry_b = OperationRegistry()
    page_b.operation_registry = registry_b
    panel = translate_panel.TranslatePanel(page_a, lambda: db)
    panel.version_dd.value = "1.21.1"
    panel._repair_preview = preview
    panel._repair_preview_db_identity = panel._database_identity(db)
    panel._poller.start = lambda *_args: None

    entered = threading.Event()
    cancel_seen = threading.Event()
    allow_finish = threading.Event()

    def wait_for_cancel(_options, session, _entries, **_kwargs):
        session.start()
        entered.set()
        while not session.cancel_requested:
            cancel_seen.wait(0.01)
        cancel_seen.set()
        allow_finish.wait(2)
        session.finish()

    monkeypatch.setattr(
        translate_panel, "run_moddb_retranslate_service", wait_for_cancel
    )

    panel._start_retranslation(preview)
    assert entered.wait(2)
    session = panel.session
    assert session is not None
    handle = registry_a.active()[0]
    assert handle.task_session is session
    assert session.operation_registry is registry_a
    assert handle.descriptor.owner == "moddb-retranslate"
    assert handle.descriptor.commit == CommitPolicy.PARTIAL_ALLOWED
    assert handle.descriptor.durability == DurabilityPolicy.USER_ACTION
    assert handle.descriptor.shutdown == ShutdownPolicy.CANCEL_AND_DRAIN

    registry_a.begin_shutdown()
    assert cancel_seen.wait(2)
    assert handle.cancel_requested is True
    assert registry_a.active_count() == 1

    other_finished = threading.Event()
    other_handle = registry_b.launch(
        other_finished.set,
        OperationDescriptor(name="other page", owner="other-page"),
    )
    assert other_handle is not None
    assert other_finished.wait(2)
    assert registry_b.wait_for_idle(timeout=2)
    assert registry_a.active_count() == 1

    allow_finish.set()
    assert registry_a.wait_for_idle(timeout=2)
    assert handle.terminal_reason == "cancelled"
    db.close()


def test_retranslation_admission_rejection_never_starts_service(db_path, monkeypatch):
    db = TranslationDB(db_path)
    _ai_entry(db)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options()
    )
    page = mock_page()
    registry = OperationRegistry()
    page.operation_registry = registry
    panel = translate_panel.TranslatePanel(page, lambda: db)
    panel.version_dd.value = "1.21.1"
    panel._repair_preview = preview
    panel._repair_preview_db_identity = panel._database_identity(db)
    started = []
    snacks = []
    monkeypatch.setattr(
        translate_panel,
        "run_moddb_retranslate_service",
        lambda *_args: started.append(True),
    )
    monkeypatch.setattr(
        translate_panel,
        "show_snack",
        lambda _page, message, _tone: snacks.append(message),
    )
    registry.begin_shutdown()

    panel._start_retranslation(preview)

    assert started == []
    assert registry.active_count() == 0
    assert panel.session is None
    assert panel._running is False
    assert any("無法啟動新任務" in message for message in snacks)
    db.close()


def test_repair_preview_cards_are_paged_and_filters_return_to_page_one(
    db_path, monkeypatch
):
    """候選超過一頁時顯示頁碼列；換頁看到對應的 Key，改篩選則回到第 1 頁。"""
    panel = translate_panel.TranslatePanel(mock_page(), lambda: TranslationDB(db_path))
    size = retranslation_controller.PREVIEW_PAGE_SIZE
    groups = [[object()] for _ in range(size * 2 + 20)]
    panel._repair_preview = object()
    monkeypatch.setattr(retranslation_controller, "_preview_groups", lambda _r: groups)
    monkeypatch.setattr(
        retranslation_controller, "_preview_group_matches", lambda *_a: True
    )
    monkeypatch.setattr(
        retranslation_controller,
        "_build_preview_group_card",
        lambda _panel, _group, index, total: __import__("flet").Text(
            f"{index}/{total}"
        ),
    )

    retranslation_controller.refresh_preview_cards(panel, update=False)
    pager = panel.repair_preview_pager
    assert pager.visible is True and pager.total_pages == 3
    assert [c.value for c in panel.repair_samples.controls][:1] == [f"1/{len(groups)}"]
    assert len(panel.repair_samples.controls) == size

    pager.goto(3)  # 按頁碼：最後一頁只剩 20 筆，卡片編號延續
    assert len(panel.repair_samples.controls) == 20
    assert panel.repair_samples.controls[0].value == f"{size * 2 + 1}/{len(groups)}"

    retranslation_controller.refresh_preview_cards(panel, update=False)  # 改篩選
    assert pager.current_page == 1

    del groups[size:]  # 剩一頁：不顯示頁碼列
    retranslation_controller.refresh_preview_cards(panel, update=False)
    assert pager.visible is False


def _stored_ai_text(db, entry_id):
    return db._q(
        "SELECT zh_tw FROM translation WHERE entry_id=? AND source=?",
        (entry_id, SRC_AI),
    )[0][0]


def _newline_only_entry(db):
    entry = _ai_entry(db, key="a.newline_only", en="Line one\nLine two")
    db._conn.execute(
        "UPDATE translation SET zh_tw='第一行第二行' WHERE entry_id=? AND source=?",
        (entry.id, SRC_AI),
    )
    db._conn.commit()
    return entry


def test_repair_newline_only_option_includes_those_candidates_in_preview(db_path):
    db = TranslationDB(db_path)
    _newline_only_entry(db)

    default = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(), mode="quality_mismatch"
    )
    included = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, _options(repair_newline_only=True), mode="quality_mismatch"
    )
    db.close()

    assert default.selected_count == 0
    assert default.skipped_newline_only_candidates == 1
    assert default.newline_only_included is False
    assert [row.key for row in included.entries] == ["a.newline_only"]
    assert included.skipped_newline_only_candidates == 0
    assert included.newline_only_included is True
    assert included.input_newline_mismatch_candidates == 1


def test_repair_newline_only_option_requires_output_to_keep_source_newlines(
    db_path, monkeypatch
):
    db = TranslationDB(db_path)
    entry = _newline_only_entry(db)
    options = _options(write_cache=False, repair_newline_only=True)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, options, mode="quality_mismatch"
    )
    db.close()

    def run_with(text):
        monkeypatch.setattr(
            moddb_retranslate_service,
            "translate_batch_smart",
            lambda batch, _total: ([{**item, "text": text} for item in batch], "AUTO"),
        )
        return _run(db_path, preview.entries, options=options, mode="quality_mismatch")

    # 換行數仍與原文不同：視為格式不一致，不寫入、保留舊譯文
    flagged = run_with("第一行第二行（重翻）")
    assert flagged["summary"]["flagged"] == 1
    assert flagged["summary"]["updated"] == 0
    check = TranslationDB(db_path)
    assert _stored_ai_text(check, entry.id) == "第一行第二行"
    check.close()

    # 換行數與原文一致：寫入
    ok = run_with("第一行\n第二行")
    assert ok["summary"]["updated"] == 1
    check = TranslationDB(db_path)
    assert _stored_ai_text(check, entry.id) == "第一行\n第二行"
    check.close()


def test_replace_quality_mismatch_strict_newline_rejects_wrong_newline_count(db_path):
    db = TranslationDB(db_path)
    entry = _newline_only_entry(db)
    identity = {
        "expected_version": "1.21.1",
        "expected_kind": "lang",
        "expected_mod_id": "foo",
        "expected_key": "a.newline_only",
        "expected_en_us": "Line one\nLine two",
        "expected_revision": None,
    }

    strict = db.replace_translation_quality_mismatch(
        entry.id, SRC_AI, "第一行第二行", "還是沒換行", strict_newline=True, **identity
    )
    lenient = db.replace_translation_quality_mismatch(
        entry.id, SRC_AI, "第一行第二行", "還是沒換行", **identity
    )
    db.close()

    assert strict.status == "skipped_changed"
    assert lenient.status == "updated"


def test_repair_card_newline_switch_is_off_by_default_and_follows_mode():
    from app.views.moddb.repair_card import create_repair_card

    noop = lambda *_a, **_k: None
    kwargs = {
        "on_mode_change": noop,
        "on_preview": noop,
        "on_confirm": noop,
        "on_filter_change": noop,
        "on_newline_change": noop,
        "on_page": noop,
        "page_size": 50,
    }

    quality = create_repair_card("quality_mismatch", **kwargs)
    same_source = create_repair_card("same_source_ai", **kwargs)

    assert quality.newline_row.value is False
    assert quality.newline_row.visible is True
    assert same_source.newline_row.visible is False


def test_translate_panel_builds_newline_option_only_for_quality_mode(db_path):
    panel = translate_panel.TranslatePanel(mock_page(), lambda: TranslationDB(db_path))

    assert panel.build_options().repair_newline_only is False
    panel.repair_newline_row.value = True
    assert panel.build_options().repair_newline_only is True
    panel._repair_mode = "same_source_ai"
    assert panel.build_options().repair_newline_only is False


def _identity(en="Use %s"):
    return {
        "expected_version": "1.21.1",
        "expected_kind": "lang",
        "expected_mod_id": "foo",
        "expected_key": "k.sync",
        "expected_en_us": en,
        "expected_revision": None,
    }


def _stored(db, version, source=SRC_AI):
    return db._q(
        "SELECT t.zh_tw FROM translation t JOIN entry e ON e.id=t.entry_id "
        "WHERE e.mc_version=? AND e.key='k.sync' AND t.source=?",
        (version, source),
    )[0][0]


def test_quality_repair_can_sync_identical_old_text_to_other_versions(db_path):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "k.sync", SRC_AI, "使用", en="Use %s")
    _ingest(db, "1.20.1", "foo", "k.sync", SRC_AI, "使用", en="Use %s")  # 舊譯文相同
    _ingest(db, "1.19.2", "foo", "k.sync", SRC_AI, "別的舊譯", en="Use %s")  # 不同
    _ingest(db, "1.18.2", "foo", "k.sync", SRC_AI, "使用", en="Other %s")  # 原文不同
    root = db._q("SELECT id FROM entry WHERE mc_version='1.21.1' AND key='k.sync'")[0][
        0
    ]

    result = db.replace_translation_quality_mismatch(
        root, SRC_AI, "使用", "使用 %s", sync_other_versions=True, **_identity()
    )

    assert (result.status, result.synced_other_versions) == ("updated", 1)
    assert _stored(db, "1.21.1") == "使用 %s"
    assert _stored(db, "1.20.1") == "使用 %s"
    assert _stored(db, "1.19.2") == "別的舊譯"
    assert _stored(db, "1.18.2") == "使用"
    # 同步的版本也有自己的歷史，可個別還原
    synced_entry = db._q(
        "SELECT id FROM entry WHERE mc_version='1.20.1' AND key='k.sync'"
    )[0][0]
    assert db.entry_detail(synced_entry).history[0].action == "quality_repair"
    db.close()


def test_quality_repair_does_not_touch_other_versions_by_default(db_path):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "k.sync", SRC_AI, "使用", en="Use %s")
    _ingest(db, "1.20.1", "foo", "k.sync", SRC_AI, "使用", en="Use %s")
    root = db._q("SELECT id FROM entry WHERE mc_version='1.21.1' AND key='k.sync'")[0][
        0
    ]

    result = db.replace_translation_quality_mismatch(
        root, SRC_AI, "使用", "使用 %s", **_identity()
    )

    assert result.synced_other_versions == 0
    assert _stored(db, "1.20.1") == "使用"
    db.close()


def test_repair_service_reports_synced_other_versions(db_path, monkeypatch):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "k.sync", SRC_AI, "使用", en="Use %s")
    _ingest(db, "1.20.1", "foo", "k.sync", SRC_AI, "使用", en="Use %s")
    options = _options(write_cache=False, repair_sync_other_versions=True)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, options, mode="quality_mismatch"
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: (
            [{**item, "text": "使用 %s"} for item in batch],
            "AUTO",
        ),
    )

    result = _run(db_path, preview.entries, options=options, mode="quality_mismatch")

    assert result["summary"]["updated"] == 1
    assert result["summary"]["synced_other_versions"] == 1
    check = TranslationDB(db_path)
    assert _stored(check, "1.20.1") == "使用 %s"
    check.close()


def test_translate_panel_builds_sync_option_only_for_quality_mode(db_path):
    panel = translate_panel.TranslatePanel(mock_page(), lambda: TranslationDB(db_path))

    assert panel.repair_sync_row.value is False
    assert panel.build_options().repair_sync_other_versions is False
    panel.repair_sync_row.value = True
    assert panel.build_options().repair_sync_other_versions is True
    panel._repair_mode = "same_source_ai"
    assert panel.build_options().repair_sync_other_versions is False


def test_repair_summary_shows_synced_other_versions_count():
    from app.views.moddb.repair_summary import repair_summary_detail_lines

    panel = translate_panel.TranslatePanel(mock_page(), lambda: None)
    base = {
        "operation": "repair_special_character_mismatch",
        "candidates": 3,
        "updated": 3,
        "unchanged": 0,
        "flagged": 0,
        "skipped_changed": 0,
        "failed": 0,
        "remaining": 0,
        "status": "DONE",
    }

    panel._apply_repair_summary({**base, "sync_other_versions": False}, final=True)
    assert "同步其他版本" not in panel.repair_summary_text.value

    synced = {**base, "sync_other_versions": True, "synced_other_versions": 7}
    panel._apply_repair_summary(synced, final=True)
    assert "更新 3（另同步其他版本 7）" in panel.repair_summary_text.value
    assert "同步其他版本：7 筆" in "\n".join(repair_summary_detail_lines(synced))
    assert not any("同步其他版本" in line for line in repair_summary_detail_lines(base))


def test_repair_completion_log_reports_synced_count(db_path, monkeypatch):
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "k.sync", SRC_AI, "使用", en="Use %s")
    _ingest(db, "1.20.1", "foo", "k.sync", SRC_AI, "使用", en="Use %s")
    options = _options(write_cache=False, repair_sync_other_versions=True)
    preview = moddb_retranslate_service.preview_same_source_ai_retranslation(
        db, options, mode="quality_mismatch"
    )
    db.close()
    monkeypatch.setattr(
        moddb_retranslate_service,
        "translate_batch_smart",
        lambda batch, _total: ([{**i, "text": "使用 %s"} for i in batch], "AUTO"),
    )

    result = _run(db_path, preview.entries, options=options, mode="quality_mismatch")

    assert any("更新 1（另同步其他版本 1）" in str(line) for line in result["logs"])


def _review_group(db_path, *, sync):
    """1.21.1 有 AI 與人工兩個待審來源；1.20.1 的 AI 來源舊譯文相同。"""
    db = TranslationDB(db_path)
    _ingest(db, "1.21.1", "foo", "k.sync", SRC_AI, "舊", en="Use %s")
    _ingest(db, "1.21.1", "foo", "k.sync", SRC_MANUAL, "舊人工", en="Use %s")
    _ingest(db, "1.20.1", "foo", "k.sync", SRC_AI, "舊", en="Use %s")
    entry = next(
        row
        for row in db.list_entries("1.21.1")[0]
        if row.mod_id == "foo" and row.key == "k.sync"
    )
    store = moddb_repair_review_store.create_run(db_path, "run-sync")

    def item(source_id, old):
        return {
            "entry_id": entry.id,
            "version": "1.21.1",
            "mod_id": "foo",
            "kind": entry.kind,
            "source_id": source_id,
            "key": "k.sync",
            "en_us": "Use %s",
            "old_translation": old,
            "expected_revision": _translation_revision(db, entry.id, source_id),
            "database_identity": str(db_path.resolve()),
            "database_file_id": db_path.stat().st_ino,
            "database_priority": db.priority,
            "sync_other_versions": sync,
        }

    first = moddb_repair_review_store.append_item(store, "run-sync", item(SRC_AI, "舊"))
    moddb_repair_review_store.append_item(store, "run-sync", item(SRC_MANUAL, "舊人工"))
    loaded = moddb_repair_review_store.load_item(store, "run-sync", first)
    db.close()
    return store, loaded


def test_review_apply_syncs_other_versions_when_the_run_had_sync_on(db_path):
    store, item = _review_group(db_path, sync=True)

    result = moddb_retranslate_service.apply_and_record_repair_review(
        db_path, store, "run-sync", item, "新 %s"
    )

    check = TranslationDB(db_path)
    assert result == "applied"
    assert _stored(check, "1.21.1") == "新 %s"
    assert _stored(check, "1.20.1") == "新 %s"
    check.close()


def test_review_apply_leaves_other_versions_alone_when_sync_was_off(db_path):
    store, item = _review_group(db_path, sync=False)

    moddb_retranslate_service.apply_and_record_repair_review(
        db_path, store, "run-sync", item, "新 %s"
    )

    check = TranslationDB(db_path)
    assert _stored(check, "1.20.1") == "舊"
    check.close()


def test_review_apply_reuses_one_database_connection_for_all_matching_items(
    db_path, monkeypatch
):
    store, item = _review_group(db_path, sync=False)
    opened = []
    real = moddb_retranslate_service.TranslationDB

    def counting(*args, **kwargs):
        opened.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(moddb_retranslate_service, "TranslationDB", counting)

    moddb_retranslate_service.apply_and_record_repair_review(
        db_path, store, "run-sync", item, "新 %s"
    )

    assert len(opened) == 1
