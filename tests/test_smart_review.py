from __future__ import annotations

import json
import sqlite3

import pytest

from translation_tool.translation_db import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    KIND_LANG,
    KIND_PATCHOULI,
    TranslationDB,
    WriteBackItem,
)
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.schema import SRC_AI, SRC_MANUAL
from translation_tool.translation_db.smart_review import (
    parse_smart_review_response,
    run_smart_review,
)


def _seed(db: TranslationDB) -> None:
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "hello", "Hello %s", "你好 %s"),
            WriteBackItem(KIND_LANG, "demo", "bye", "Goodbye", "再見"),
        ],
        fill_other_versions=False,
    )


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "smart-review.db")
    _seed(database)
    yield database
    database.close()


def test_snapshot_uses_implicit_unreviewed_for_non_manual_sources(db):
    snapshots = db.smart_review_candidates(version="1.21.1", limit=1000)
    assert [item.key for item in snapshots] == ["bye", "hello"]
    assert all(item.review_status == "unreviewed" for item in snapshots)
    assert all(item.effective_source != SRC_MANUAL for item in snapshots)


def test_snapshot_can_require_unique_reviewed_same_key_reference(db):
    db.write_back(
        "1.20.1",
        [WriteBackItem(KIND_LANG, "demo", "hello", "Hello %s", "嗨 %s")],
        fill_other_versions=False,
    )
    reviewed = next(
        item for item in db.list_entries("1.21.1")[0] if item.key == "hello"
    )
    db.review_manual(reviewed.id, expected_zh_tw="你好 %s", propagate=False)

    snapshots = db.smart_review_candidates(
        version="1.20.1", limit=1000, require_reviewed_reference=True
    )
    assert [item.key for item in snapshots] == ["hello"]


def test_reviewed_reference_requires_same_english_source(db):
    db.write_back(
        "1.20.1",
        [WriteBackItem(KIND_LANG, "demo", "hello", "Hello changed", "嗨")],
        fill_other_versions=False,
    )
    changed = next(item for item in db.list_entries("1.20.1")[0] if item.key == "hello")
    db.review_manual(changed.id, expected_zh_tw="嗨", propagate=False)

    assert (
        db.smart_review_candidates(
            version="1.21.1", limit=1000, require_reviewed_reference=True
        )
        == []
    )


def test_local_context_covers_non_lang_terms_and_preserves_traceability(db):
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(
                KIND_LANG,
                "demo",
                "phenolic_resin",
                "Phenolic Resin",
                "酚醛樹脂",
            ),
            WriteBackItem(
                KIND_PATCHOULI,
                "demo",
                "book/page",
                "Phenolic Resin is useful",
                "酚醛樹脂很有用",
            ),
            WriteBackItem(
                KIND_GUIDE,
                "demo",
                "guide/page",
                "Phenolic Resin guide",
                "酚醛樹脂指南",
            ),
            WriteBackItem(
                KIND_ADVANCEMENT,
                "demo",
                "advancement/test",
                "Phenolic Resin advancement",
                "酚醛樹脂進度",
            ),
        ],
        fill_other_versions=False,
    )
    term = next(
        item for item in db.list_entries("1.21.1")[0] if item.key == "phenolic_resin"
    )
    db.review_manual(term.id, expected_zh_tw="酚醛樹脂", propagate=False)
    candidate = db.smart_review_candidates(
        version="1.21.1",
        mod_id="demo",
        kind=KIND_PATCHOULI,
        effective_source=SRC_AI,
        limit=1000,
    )[0]
    glossary = [
        item
        for item in candidate.context
        if item["reference_type"] == "reviewed_lang_glossary"
    ]
    assert glossary
    assert glossary[0]["id"].startswith("db:")
    assert glossary[0]["en_us"] == "Phenolic Resin"
    assert glossary[0]["zh_tw"] == "酚醛樹脂"
    assert glossary[0]["mod_id"] == "demo"
    assert glossary[0]["review_status"] == "reviewed"


def test_response_validation_requires_exact_ids_and_format_safe_suggestion(db):
    snapshot = db.smart_review_candidates(version="1.21.1", limit=2)[1]
    valid = {
        "items": [
            {
                "id": str(snapshot.ordinal),
                "decision": "suggest_revision",
                "suggested_zh_tw": "你好啊 %s",
                "issue_types": ["style"],
                "evidence_ids": [],
                "confidence": 0.9,
                "reason": "原文 Hello %s 與目前譯文 你好 %s 的語氣需要調整。",
            }
        ]
    }
    assert parse_smart_review_response(json.dumps(valid), [snapshot])[0][
        "decision"
    ] == ("suggest_revision")

    invalid = dict(valid)
    invalid["items"] = [dict(valid["items"][0], suggested_zh_tw="你好啊")]
    result = parse_smart_review_response(json.dumps(invalid), [snapshot])
    assert result[0]["validation_error"] == "suggested_zh_tw 未通過格式與結構驗證"

    with pytest.raises(ValueError, match="id 集合"):
        parse_smart_review_response(
            json.dumps({"items": [dict(valid["items"][0], id="wrong")]}),
            [snapshot],
        )


def test_response_validation_requires_concrete_reason_and_rejects_external_urls(db):
    snapshot = db.smart_review_candidates(version="1.21.1", limit=2)[1]
    base = {
        "id": str(snapshot.ordinal),
        "decision": "suggest_revision",
        "suggested_zh_tw": "你好啊 %s",
        "issue_types": ["terminology"],
        "evidence_ids": [],
        "confidence": 0.9,
    }
    missing = {"items": [base]}
    result = parse_smart_review_response(json.dumps(missing), [snapshot])
    assert "欄位" in result[0]["validation_error"]

    vague = {"items": [dict(base, reason="建議優化。")]}  # 沒有可核對內容
    result = parse_smart_review_response(json.dumps(vague), [snapshot])
    assert "reason" in result[0]["validation_error"]

    external = {
        "items": [
            dict(
                base,
                reason="原文 Hello %s 與目前譯文 你好 %s 不一致，請見 https://example.invalid。",
            )
        ]
    }
    result = parse_smart_review_response(json.dumps(external), [snapshot])
    assert "URL" in result[0]["validation_error"]


def test_run_persists_candidates_without_writing_translation(db, monkeypatch):
    def fake_call(**kwargs):
        items = kwargs["payload"]["items"]
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "no_change",
                        "suggested_zh_tw": "",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.8,
                        "reason": "英文與目前譯文一致，暫不修改。",
                    }
                    for item in items
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    before = db._q("SELECT COUNT(*) FROM translation WHERE source=?", (SRC_MANUAL,))[0][
        0
    ]
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
    )
    assert result.selected_count == result.processed_count == 2
    assert result.valid_count == 2
    assert result.no_change_count == 2
    assert (
        db._q("SELECT COUNT(*) FROM translation WHERE source=?", (SRC_MANUAL,))[0][0]
        == before
    )
    stored = db.list_smart_review_candidates(result.run_id)
    assert [item.status for item in stored] == ["no_change", "no_change"]
    run = db.get_smart_review_run(result.run_id)
    assert run["scope"]["effective_source"] == SRC_AI
    assert db.list_smart_review_runs(limit=1)[0]["id"] == result.run_id


def test_request_failures_and_cancellation_are_distinct_from_invalid(db, monkeypatch):
    def fail_call(**_kwargs):
        raise RuntimeError("simulated request failure")

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fail_call,
    )
    failed = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
    )
    assert failed.status == "partial"
    assert failed.request_error_count == 2
    assert [item.status for item in db.list_smart_review_candidates(failed.run_id)] == [
        "request_failed",
        "request_failed",
    ]

    called = False

    def should_not_call(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("cancelled run must not send a request")

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        should_not_call,
    )
    cancelled = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
        cancel_check=lambda: True,
    )
    assert not called
    assert cancelled.status == "cancelled"
    assert [
        item.status for item in db.list_smart_review_candidates(cancelled.run_id)
    ] == ["cancelled", "cancelled"]


def test_apply_candidate_is_cas_guarded_and_writes_history(db, monkeypatch):
    def fake_call(**kwargs):
        return json.dumps(
            {
                "items": [
                    {
                        "id": kwargs["payload"]["items"][0]["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "再見！",
                        "issue_types": ["style"],
                        "evidence_ids": [],
                        "confidence": 0.95,
                        "reason": "原文 Goodbye 與目前譯文 再見 仍可更精確地表達語氣。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(db, model_name="gemini-test", api_key="test-key", limit=1)
    assert db.apply_smart_review_candidate(result.run_id, 1) == "applied"
    candidate = db.list_smart_review_candidates(result.run_id)[0]
    assert candidate.status == "applied"
    assert db.entry_detail(candidate.entry_id).entry.source == SRC_MANUAL
    assert any(
        row.action == "smart_review_apply"
        for row in db.entry_detail(candidate.entry_id).history
    )

    # A second apply is no longer eligible, and a changed effective row cannot be overwritten.
    assert db.apply_smart_review_candidate(result.run_id, 1) == "not_ready"
    history = next(
        row
        for row in db.entry_detail(candidate.entry_id).history
        if row.action == "smart_review_apply"
    )
    assert db.revert(history.id) == 1
    assert db.entry_detail(candidate.entry_id).entry.source != SRC_MANUAL


def test_draft_is_persisted_and_apply_uses_the_human_value(db, monkeypatch):
    def fake_call(**kwargs):
        return json.dumps(
            {
                "items": [
                    {
                        "id": kwargs["payload"]["items"][0]["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "再見！",
                        "issue_types": ["terminology"],
                        "evidence_ids": [],
                        "confidence": 0.95,
                        "reason": "原文 Goodbye 與目前譯文 再見 的專有名詞語氣需要確認。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(db, model_name="gemini-test", api_key="test-key", limit=1)
    assert db.save_smart_review_draft(result.run_id, 1, "再會！") == "saved"
    stored = db.list_smart_review_candidates(result.run_id)[0]
    assert stored.draft_zh_tw == "再會！"
    assert db.apply_smart_review_candidate(result.run_id, 1) == "applied"
    assert db.entry_detail(stored.entry_id).entry.zh_tw == "再會！"


def test_reject_candidate_keeps_translation_unchanged(db, monkeypatch):
    def fake_call(**kwargs):
        return json.dumps(
            {
                "items": [
                    {
                        "id": kwargs["payload"]["items"][0]["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "再見！",
                        "issue_types": ["style"],
                        "evidence_ids": [],
                        "confidence": 0.9,
                        "reason": "原文 Goodbye 與目前譯文 再見 需要人工確認語氣。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    before = db._q(
        "SELECT zh_tw FROM translation WHERE entry_id=1 AND source=?",
        (SRC_MANUAL,),
    )
    result = run_smart_review(db, model_name="gemini-test", api_key="test-key", limit=1)
    assert db.reject_smart_review_candidate(result.run_id, 1) == "rejected"
    assert db.reject_smart_review_candidate(result.run_id, 1) == "not_ready"
    assert (
        db._q(
            "SELECT zh_tw FROM translation WHERE entry_id=1 AND source=?",
            (SRC_MANUAL,),
        )
        == before
    )


def test_smart_review_snapshot_is_immutable_value_object():
    snapshot = SmartReviewSnapshot(
        run_id="",
        ordinal=1,
        entry_id=1,
        kind="lang",
        mc_version="1.21.1",
        mod_id="demo",
        key="hello",
        en_us="Hello",
        current_zh_tw="你好",
        effective_source=0,
        review_status="unreviewed",
        effective_revision=1,
    )
    with pytest.raises((AttributeError, TypeError)):
        snapshot.current_zh_tw = "changed"


def test_smart_review_dialog_builds_preview_and_run_controls(db):
    from app.views.moddb.smart_review_dialog import SmartReviewDialog
    from tests.conftest import mock_page

    dialog = SmartReviewDialog(mock_page(), lambda: db, "1.21.1", None)
    assert dialog.dialog.title.value == "AI 智慧校對（候選模式）"
    assert dialog.model_dd.value == "gemini-3.1-flash-lite"
    assert dialog.limit_field.value == "1000"
    assert dialog.run_btn.disabled is False


def test_v7_smart_review_is_readable_and_migrates_to_v8(tmp_path):
    path = tmp_path / "v7-smart-review.db"
    writable = TranslationDB(path)
    _seed(writable)
    snapshots = writable.smart_review_candidates(version="1.21.1", limit=1)
    run_id = writable.create_smart_review_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=1,
        batch_size=1,
        scope={"version": "1.21.1", "effective_source": SRC_AI},
    )
    writable.close()

    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE smart_review_run DROP COLUMN scope_json")
    connection.execute("ALTER TABLE smart_review_candidate DROP COLUMN draft_zh_tw")
    connection.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
    connection.commit()
    connection.close()

    readonly = TranslationDB(path, readonly=True)
    old_run = readonly.get_smart_review_run(run_id)
    old_candidate = readonly.list_smart_review_candidates(run_id)[0]
    assert old_run["scope"] == {}
    assert old_candidate.draft_zh_tw is None
    readonly.close()

    migrated = TranslationDB(path)
    assert migrated.get_smart_review_run(run_id)["scope"] == {}
    assert migrated._smart_review_has_column("smart_review_run", "scope_json")
    assert migrated._smart_review_has_column("smart_review_candidate", "draft_zh_tw")
    migrated.close()
    assert list(tmp_path.glob("v7-smart-review.db.pre-schema-v8-*.bak"))
