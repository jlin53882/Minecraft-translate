"""Explicit repair of existing AI translations equal to their source."""

from __future__ import annotations

import pytest

from app.services_impl import (
    moddb_retranslate_service,
    moddb_service,
    moddb_translate_service,
)
from app.services_impl.moddb_translate_service import TranslateOptions
from app.tasks.task_session import TaskSession
from app.views.moddb import translate_panel
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
    assert "舊 AI 機翻修復候選 66 筆" in logs
    assert "來源：AI 機翻" in logs
    assert "Lang 60 筆" in logs
    assert "Patchouli 6 筆" in logs
    assert "略過舊快取直接重新翻譯" in logs
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
    assert [call[:3] for call in budget_calls] == [
        (60, "lang", 100),
        (35, "lang", 100),
        (10, "lang", 100),
    ]
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
    assert db.get_entry(target.id).source == SRC_AI
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
        "add_to_cache",
        lambda *args, **kwargs: events.append(("add", args, kwargs)) or True,
    )
    monkeypatch.setattr(
        moddb_retranslate_service,
        "save_translation_cache",
        lambda cache_type: events.append(("save", cache_type)) or True,
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


def _run(db_path, entries, *, options=None):
    session = TaskSession()
    moddb_retranslate_service.run_moddb_retranslate_service(
        options or _options(), session, entries
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
    original_add = moddb_retranslate_service.add_to_cache

    def check_db_then_add(*args, **kwargs):
        check = TranslationDB(db_path)
        assert check.get_entry(entry.id).zh_tw == "我的世界"
        check.close()
        return original_add(*args, **kwargs)

    monkeypatch.setattr(moddb_retranslate_service, "add_to_cache", check_db_then_add)
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
    assert snap["summary"]["failed"] == 2  # invalid result + skipped next batch
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
    assert snap["summary"]["failed"] == 1  # skipped next batch
    check = TranslationDB(db_path)
    assert check.get_entry(calls[0][0]).zh_tw == "我的世界"
    pending_id = next(
        row.entry_id for row in preview.entries if row.entry_id not in calls[0]
    )
    assert check.get_entry(pending_id).zh_tw == "Minecraft"
    check.close()
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
        moddb_retranslate_service, "add_to_cache", lambda *a, **k: False
    )

    snap = _run(db_path, preview.entries)

    assert snap["summary"]["updated"] == 1
    assert snap["summary"]["cache_failed"] == 1
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

    def add_to_cache(*_args, **_kwargs):
        fail_once("add")
        return True

    def save_translation_cache(_cache_type):
        fail_once("save")
        return True

    monkeypatch.setattr(moddb_retranslate_service, "add_to_cache", add_to_cache)
    monkeypatch.setattr(
        moddb_retranslate_service, "save_translation_cache", save_translation_cache
    )

    snap = _run(db_path, preview.entries)

    assert batches == [{"lang"}, {"patchouli"}]
    assert snap["summary"]["status"] == "DONE"
    assert snap["status"] == "DONE"
    assert snap["summary"]["updated"] == 2
    assert snap["summary"]["cache_failed"] == 1
    assert snap["summary"]["failed"] == 0
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


def test_preview_confirmation_cancel_and_scope_invalidation(db_path, monkeypatch):
    db = TranslationDB(db_path)
    _ai_entry(db)
    page = mock_page()
    panel = translate_panel.TranslatePanel(page, lambda: db)
    panel.version_dd.value = "1.21.1"
    panel.limit_field.value = "1"

    snacks = []
    monkeypatch.setattr(
        translate_panel,
        "show_snack",
        lambda _page, message, _tone: snacks.append(message),
    )
    instructions = panel.repair_card.body.content.controls[0].value
    assert "檢查候選筆數與樣本" in instructions
    assert "預覽會失效，必須重新預覽" in instructions
    assert panel.repair_start_btn.disabled is False
    panel.confirm_retranslation()
    assert "請先按「預覽符合條件的舊 AI 譯文」" in snacks[-1]
    assert page.overlay == []

    panel.preview_retranslation()

    assert panel._repair_preview.selected_count == 1
    assert panel.repair_start_btn.disabled is False
    panel.confirm_retranslation()
    page.overlay[-1].actions[0].on_click(None)
    assert panel._running is False

    panel.confirm_retranslation()
    assert "確認重新翻譯舊 AI 譯文" in str(page.overlay[-1].title.value)
    preview = panel._repair_preview
    calls = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            calls.append((target, args, daemon))

        def start(self):
            pass

    monkeypatch.setattr(translate_panel.threading, "Thread", FakeThread)
    panel._poller.start = lambda *_args: None
    page.overlay[-1].actions[1].on_click(None)
    assert calls[0][0] is moddb_retranslate_service.run_moddb_retranslate_service
    assert calls[0][1][0].version == "1.21.1"
    assert calls[0][1][0].limit == 1
    assert calls[0][1][2] is preview.entries

    panel._running = False
    panel.mod_dd.value = "foo"
    panel._on_scope_changed()
    assert panel._repair_preview is None
    assert panel.repair_start_btn.disabled is False
    panel.confirm_retranslation()
    assert "請先按「預覽符合條件的舊 AI 譯文」" in snacks[-1]
    panel.preview_retranslation()
    assert panel._repair_preview is not None
    panel._get_db = lambda: None
    panel.preview_retranslation()
    assert panel._repair_preview is None
    assert panel.repair_start_btn.disabled is False
    panel.confirm_retranslation()
    assert "請先按「預覽符合條件的舊 AI 譯文」" in snacks[-1]
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
    panel.version_dd.value = "1.21.1"
    panel.preview_retranslation()
    panel.confirm_retranslation()
    stale_dialog = page.overlay[-1]

    snacks = []
    monkeypatch.setattr(
        translate_panel,
        "show_snack",
        lambda _page, message, _tone: snacks.append(message),
    )
    starts = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            starts.append((target, args, daemon))

        def start(self):
            pass

    monkeypatch.setattr(translate_panel.threading, "Thread", FakeThread)
    if identity_change == "path":
        active_db[0] = current_db
    else:
        preview_db.set_priority(tuple(reversed(preview_db.priority)))
    panel.refresh_scope()

    assert panel._repair_preview is None
    stale_dialog.actions[1].on_click(None)
    assert starts == []
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
    panel.version_dd.value = "1.21.1"
    panel.preview_retranslation()

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
    assert "預估：約 2 批" in text
    samples = [control.value for control in panel.repair_samples.controls]
    assert any("[Lang] [AI 機翻]" in sample for sample in samples)
    assert any("[Patchouli] [AI 機翻]" in sample for sample in samples)
    assert all("manual" not in sample and "jar" not in sample for sample in samples)
    db.close()
