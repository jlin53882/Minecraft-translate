"""智慧校對續跑、歷史兩批重用與未送出候選的回歸測試。"""

from __future__ import annotations

import json
import time

from translation_tool.core.openai_codex_client import ChatGPTAPIError
from translation_tool.translation_db import KIND_LANG, TranslationDB, WriteBackItem
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.schema import SRC_AI
from translation_tool.translation_db.smart_review import (
    resume_smart_review,
    run_smart_review,
)
from translation_tool.translation_db.smart_review_history import SmartReviewHistoryStore
from translation_tool.translation_db.smart_review_planner import plan_new_smart_review


def _response(items: list[dict[str, str]]) -> str:
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
                    "reason": "目前譯文可保留。",
                }
                for item in items
            ]
        }
    )


def _snapshot() -> SmartReviewSnapshot:
    return SmartReviewSnapshot(
        run_id="pending",
        ordinal=1,
        entry_id=1,
        kind="lang",
        mc_version="1.21.1",
        mod_id="demo",
        key="hello",
        en_us="Hello",
        current_zh_tw="你好",
        effective_source=3,
        review_status="unreviewed",
        effective_revision=1,
    )


def test_new_run_consults_only_latest_two_parseable_manifests(tmp_path):
    store = SmartReviewHistoryStore(tmp_path / "review.db")
    snapshot = _snapshot()
    run_ids: list[str] = []
    for index in range(3):
        run_id = store.create_run(
            [snapshot],
            provider="gemini",
            model="test-model",
            version="1.21.1",
            requested_limit=1,
            batch_size=1,
        )
        if index == 0:
            store.record_results(
                run_id,
                [
                    {
                        "id": "1",
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "哈囉",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.9,
                        "reason": "舊結果",
                    }
                ],
            )
        store.finish_run(
            run_id,
            processed_count=1 if index == 0 else 0,
            valid_count=1 if index == 0 else 0,
            invalid_count=0,
            request_error_count=0,
            metrics={},
            status="completed" if index == 0 else "partial",
        )
        run_ids.append(run_id)
        time.sleep(0.02)

    consulted, historical = store.recent_candidate_payloads(max_runs=2)
    assert consulted == 2
    plan = plan_new_smart_review(
        [snapshot],
        historical,
        skip_valid_history=True,
        history_runs_consulted=consulted,
    )
    assert plan.history_runs_consulted == 2
    assert plan.reused_count == 0
    assert len(plan.to_process) == 1
    assert run_ids[0] not in {item.get("run_id") for item in historical}


def test_planner_uses_older_valid_result_after_newest_request_failure():
    snapshot = _snapshot()
    fingerprint = {
        "kind": snapshot.kind,
        "mc_version": snapshot.mc_version,
        "mod_id": snapshot.mod_id,
        "entry_key": snapshot.key,
        "en_us": snapshot.en_us,
        "current_zh_tw": snapshot.current_zh_tw,
        "effective_source": snapshot.effective_source,
        "review_status": snapshot.review_status,
        "effective_revision": snapshot.effective_revision,
        "manual_zh_tw": snapshot.manual_zh_tw,
        "manual_checker": snapshot.manual_checker,
        "manual_review_status": snapshot.manual_review_status,
        "manual_revision": snapshot.manual_revision,
    }
    newest_failed = {
        **fingerprint,
        "status": "request_failed",
        "ai_status": "request_failed",
    }
    older_valid = {
        **fingerprint,
        "status": "no_change",
        "ai_status": "no_change",
        "decision": "no_change",
        "suggested_zh_tw": "",
        "confidence": 0.9,
        "reason": "舊結果",
    }
    plan = plan_new_smart_review(
        [snapshot],
        [newest_failed, older_valid],
        skip_valid_history=True,
        history_runs_consulted=2,
    )
    assert plan.reused_count == 1
    assert plan.actual_ai_count == 0
    assert plan.no_change_skipped_count == 1


def test_planner_keeps_ready_for_review_and_reruns_skipped_status_when_disabled():
    snapshot = _snapshot()
    common = {
        "kind": snapshot.kind,
        "mc_version": snapshot.mc_version,
        "mod_id": snapshot.mod_id,
        "entry_key": snapshot.key,
        "en_us": snapshot.en_us,
        "current_zh_tw": snapshot.current_zh_tw,
        "effective_source": snapshot.effective_source,
        "review_status": snapshot.review_status,
        "effective_revision": snapshot.effective_revision,
        "manual_zh_tw": snapshot.manual_zh_tw,
        "manual_checker": snapshot.manual_checker,
        "manual_review_status": snapshot.manual_review_status,
        "manual_revision": snapshot.manual_revision,
        "decision": "suggest_revision",
        "suggested_zh_tw": "哈囉",
        "issue_types": [],
        "evidence_ids": [],
        "confidence": 0.9,
        "reason": "建議修改",
    }
    ready_plan = plan_new_smart_review(
        [snapshot],
        [{**common, "status": "ready", "ai_status": "ready"}],
        skip_valid_history=True,
        history_runs_consulted=1,
    )
    assert ready_plan.reused_count == 1
    assert ready_plan.pending_review_count == 1
    applied_plan = plan_new_smart_review(
        [snapshot],
        [{**common, "status": "applied", "ai_status": "ready"}],
        skip_valid_history=False,
        history_runs_consulted=1,
    )
    assert applied_plan.reused_count == 0
    assert applied_plan.actual_ai_count == 1


def test_chatgpt_explicit_quota_sets_run_stop_reason_without_processing_pending(
    tmp_path, monkeypatch
):
    db = TranslationDB(tmp_path / "chatgpt-quota.db")
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "hello", "Hello", "你好"),
            WriteBackItem(KIND_LANG, "demo", "bye", "Goodbye", "再見"),
        ],
        fill_other_versions=False,
    )

    def quota_error(**_kwargs):
        raise ChatGPTAPIError(
            "insufficient_quota",
            "usage limit",
            status=429,
            error_type="rate_limit_error",
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review_provider.call_gemini_requests",
        quota_error,
    )
    result = run_smart_review(
        db,
        model_name="gpt-test",
        provider="chatgpt",
        api_key="",
        limit=2,
        batch_size=1,
    )
    assert result.stop_reason == "quota_exhausted"
    assert result.processed_count == 1
    assert [item.status for item in db.list_smart_review_candidates(result.run_id)] == [
        "request_failed",
        "pending",
    ]
    db.close()


def test_resume_keeps_run_id_and_retries_only_pending(tmp_path, monkeypatch):
    db = TranslationDB(tmp_path / "resume.db")
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "hello", "Hello", "你好"),
            WriteBackItem(KIND_LANG, "demo", "bye", "Goodbye", "再見"),
        ],
        fill_other_versions=False,
    )
    cancelled = False

    def fake_call(**kwargs):
        nonlocal cancelled
        cancelled = True
        return _response(kwargs["payload"]["items"])

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review_provider.call_gemini_requests",
        fake_call,
    )
    first = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
        cancel_check=lambda: cancelled,
    )
    assert first.status == "cancelled"
    assert [item.status for item in db.list_smart_review_candidates(first.run_id)] == [
        "no_change",
        "cancelled",
    ]

    cancelled = False
    resumed = resume_smart_review(
        db,
        run_id=first.run_id,
        model_name="gemini-test",
        api_key="test-key",
    )
    assert resumed.run_id == first.run_id
    assert resumed.status == "completed"
    assert resumed.processed_count == 2
    assert all(
        item.status == "no_change"
        for item in db.list_smart_review_candidates(first.run_id)
    )
    history = SmartReviewHistoryStore(db.path)
    assert history.get_run(first.run_id)["resume_count"] == 1
    db.close()


def test_resume_invalidates_completed_result_after_manual_snapshot_change(
    tmp_path, monkeypatch
):
    db = TranslationDB(tmp_path / "manual-change.db")
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "demo", "hello", "Hello", "你好")],
        fill_other_versions=False,
    )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review_provider.call_gemini_requests",
        lambda **kwargs: _response(kwargs["payload"]["items"]),
    )
    first = run_smart_review(
        db, model_name="gemini-test", api_key="test-key", limit=1, batch_size=1
    )
    entry_id = db.smart_review_candidates(version="1.21.1", limit=1)[0].entry_id
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET zh_tw=?, revision=revision+1 "
            "WHERE entry_id=? AND source=?",
            ("您好", entry_id, SRC_AI),
        )
    resumed = resume_smart_review(
        db, run_id=first.run_id, model_name="gemini-test", api_key="test-key"
    )
    assert resumed.run_id == first.run_id
    assert resumed.status == "completed"
    candidate = SmartReviewHistoryStore(db.path).get_candidate(first.run_id, 1)
    assert candidate["status"] == "no_change"
    assert candidate["attempt_history"]
    assert candidate["attempt_history"][0]["status"] == "no_change"
    db.close()


def test_new_run_reuses_snapshot_valid_results_without_provider_call(
    tmp_path, monkeypatch
):
    db = TranslationDB(tmp_path / "dedupe.db")
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "hello", "Hello", "你好"),
            WriteBackItem(KIND_LANG, "demo", "bye", "Goodbye", "再見"),
        ],
        fill_other_versions=False,
    )
    calls = 0

    def fake_call(**kwargs):
        nonlocal calls
        calls += 1
        return _response(kwargs["payload"]["items"])

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review_provider.call_gemini_requests",
        fake_call,
    )
    first = run_smart_review(
        db, model_name="gemini-test", api_key="test-key", limit=2, batch_size=2
    )
    second = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=2,
        use_recent_history=True,
        skip_valid_history=True,
    )
    assert first.status == second.status == "completed"
    assert calls == 1
    assert second.metrics["reused_count"] == 2
    assert second.metrics["actual_ai_count"] == 0
    assert second.processed_count == second.selected_count == 2
    db.close()
