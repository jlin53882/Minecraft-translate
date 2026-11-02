"""needs_context 升級：補相鄰鍵證據後重判，失敗時維持第一次結果。"""

from __future__ import annotations

import json

import pytest

from translation_tool.translation_db import KIND_LANG, TranslationDB, WriteBackItem
from translation_tool.translation_db.smart_review import run_smart_review
from translation_tool.translation_db.smart_review_escalation import (
    adjacent_key_context,
    merge_escalated_results,
)

CALL = "translation_tool.translation_db.smart_review_provider.call_gemini_requests"
LM_CONFIG = {"rate_limit": {"sleep_seconds_between_batches": 0}}


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "escalation.db")
    database.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "config.speed", "Speed", "速度"),
            WriteBackItem(
                KIND_LANG,
                "demo",
                "config.speed.tooltip",
                "How fast it moves",
                "移動有多快",
            ),
            WriteBackItem(KIND_LANG, "demo", "other.thing", "Thing", "東西"),
        ],
        fill_other_versions=False,
    )
    # write_back 不會建立沒有譯文的條目；直接補一個未翻譯的相鄰鍵。
    with database._tx() as conn:
        conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES (?,?,?,?,?)",
            (KIND_LANG, "1.21.1", "demo", "config.speed.max", "Maximum"),
        )
    yield database
    database.close()


def _snapshot_for(db, key):
    from translation_tool.translation_db.models import SmartReviewSnapshot

    row = db._one(
        "SELECT id, kind, mc_version, mod_id, key, en_us FROM entry WHERE key=?", (key,)
    )
    return SmartReviewSnapshot(
        run_id="r",
        ordinal=1,
        entry_id=row[0],
        kind=row[1],
        mc_version=row[2],
        mod_id=row[3],
        key=row[4],
        en_us=row[5],
        current_zh_tw="x",
        effective_source=None,
        review_status=None,
        effective_revision=None,
    )


def test_adjacent_context_collects_parent_children_and_marks_untranslated(db):
    snapshot = _snapshot_for(db, "config.speed.tooltip")
    keys = {item["key"]: item for item in adjacent_key_context(db, snapshot)}

    assert set(keys) == {"config.speed", "config.speed.max"}
    assert keys["config.speed"]["zh_tw"] == "速度"
    assert keys["config.speed.max"]["review_status"] == "untranslated"
    assert all(item["reference_type"] == "adjacent_key" for item in keys.values())
    assert "other.thing" not in keys


def test_adjacent_context_includes_children_of_the_key(db):
    keys = {
        i["key"] for i in adjacent_key_context(db, _snapshot_for(db, "config.speed"))
    }
    assert {"config.speed.tooltip", "config.speed.max"} <= keys


def _responses(first_decision, second_decision):
    calls = []

    def call(**kwargs):
        calls.append(kwargs)
        decision = first_decision if len(calls) == 1 else second_decision
        items = []
        for item in kwargs["payload"]["items"]:
            adjacent = [
                c["id"]
                for c in item["context"]
                if c.get("reference_type") == "adjacent_key"
            ]
            this = decision if (len(calls) == 1 or adjacent) else "needs_context"
            items.append(
                {
                    "id": item["id"],
                    "decision": this,
                    "suggested_zh_tw": "",
                    "issue_types": [],
                    "evidence_ids": adjacent[:1] if len(calls) > 1 else [],
                    "confidence": 0.8,
                    "reason": "第一次缺資料" if len(calls) == 1 else "相鄰鍵顯示可保留",
                }
            )
        return json.dumps({"items": items})

    return call, calls


def _run(db, monkeypatch, call, **kwargs):
    monkeypatch.setattr(CALL, call)
    return run_smart_review(
        db,
        model_name="gemini-test",
        api_key="k",
        limit=1,
        batch_size=1,
        lm_config=LM_CONFIG,
        **kwargs,
    )


def test_needs_context_is_reasked_with_adjacent_keys_and_resolved(db, monkeypatch):
    call, calls = _responses("needs_context", "no_change")
    result = _run(db, monkeypatch, call)

    assert len(calls) == 2
    first_ctx = calls[0]["payload"]["items"][0]["context"]
    second_ctx = calls[1]["payload"]["items"][0]["context"]
    assert not any(c.get("reference_type") == "adjacent_key" for c in first_ctx)
    assert any(c.get("reference_type") == "adjacent_key" for c in second_ctx)
    assert result.metrics["context_escalation_attempts"] == 1
    assert result.metrics["context_escalation_resolved"] == 1
    assert result.needs_context_count == 0
    assert result.no_change_count == 1


def test_no_escalation_when_first_pass_is_decisive(db, monkeypatch):
    call, calls = _responses("no_change", "no_change")
    result = _run(db, monkeypatch, call)

    assert len(calls) == 1
    assert result.metrics["context_escalation_attempts"] == 0


def test_escalation_request_failure_keeps_first_result(db, monkeypatch):
    state = {"n": 0}
    first, _ = _responses("needs_context", "needs_context")

    def call(**kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return first(**kwargs)
        raise RuntimeError("boom")

    result = _run(db, monkeypatch, call)

    assert result.needs_context_count == 1
    assert result.metrics["context_escalation_request_errors"] == 1


def test_adjacent_citations_move_into_reason_not_evidence_ids():
    from translation_tool.translation_db.models import SmartReviewSnapshot

    snapshot = SmartReviewSnapshot(
        "r",
        1,
        1,
        "lang",
        "1.21.1",
        "demo",
        "k",
        "K",
        "x",
        None,
        None,
        None,
        context=(
            {
                "id": "adj:9",
                "reference_type": "adjacent_key",
                "key": "k.tip",
                "zh_tw": "提示",
                "en_us": "Tip",
                "review_status": "reviewed",
            },
        ),
    )
    merged, resolved = merge_escalated_results(
        [{"id": "1", "decision": "needs_context", "reason": "缺", "evidence_ids": []}],
        [
            {
                "id": "1",
                "decision": "no_change",
                "reason": "可保留",
                "evidence_ids": ["adj:9"],
            }
        ],
        [snapshot],
    )

    assert resolved == 1
    assert merged[0]["evidence_ids"] == []
    assert "k.tip＝提示（reviewed）" in merged[0]["reason"]
