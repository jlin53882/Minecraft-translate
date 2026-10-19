from __future__ import annotations

import json

import pytest

from translation_tool.translation_db import KIND_LANG, TranslationDB, WriteBackItem
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.schema import SRC_MANUAL
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
            }
        ]
    }
    assert parse_smart_review_response(json.dumps(valid), [snapshot])[0]["decision"] == (
        "suggest_revision"
    )

    invalid = dict(valid)
    invalid["items"] = [dict(valid["items"][0], suggested_zh_tw="你好啊")]
    result = parse_smart_review_response(json.dumps(invalid), [snapshot])
    assert result[0]["validation_error"] == "suggested_zh_tw 未通過格式與結構驗證"

    with pytest.raises(ValueError, match="id 集合"):
        parse_smart_review_response(
            json.dumps({"items": [dict(valid["items"][0], id="wrong")]}),
            [snapshot],
        )


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
                    }
                    for item in items
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    before = db._q("SELECT COUNT(*) FROM translation WHERE source=?", (SRC_MANUAL,))[0][0]
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
    assert db._q("SELECT COUNT(*) FROM translation WHERE source=?", (SRC_MANUAL,))[0][0] == before
    stored = db.list_smart_review_candidates(result.run_id)
    assert [item.status for item in stored] == ["no_change", "no_change"]


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
    assert db._q(
        "SELECT zh_tw FROM translation WHERE entry_id=1 AND source=?",
        (SRC_MANUAL,),
    ) == before


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
