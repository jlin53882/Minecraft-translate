"""智慧校對續跑、歷史兩批重用與未送出候選的回歸測試。"""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest

from translation_tool.core.openai_codex_client import ChatGPTAPIError
from translation_tool.translation_db import KIND_LANG, TranslationDB, WriteBackItem
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.schema import SRC_AI, SRC_MANUAL
from translation_tool.translation_db.smart_review import (
    resume_smart_review,
    run_smart_review,
)
from translation_tool.translation_db.smart_review_history import SmartReviewHistoryStore
from translation_tool.translation_db.smart_review_planner import plan_new_smart_review
from translation_tool.translation_db.smart_review_runs import _resume_counts
from translation_tool.translation_db.smart_review_status import SmartReviewStatus


def test_resume_preview_reads_one_run_without_mutating_history(tmp_path):
    """續跑確認只讀指定 run，並回傳模型、批次與待處理摘要。"""
    from app.services_impl.moddb_smart_review_service import (
        preview_moddb_smart_review_resume_from_path,
    )

    path = tmp_path / "resume-preview.db"
    db = TranslationDB(path)
    run_id = db.create_smart_review_run(
        [_snapshot()],
        provider="chatgpt",
        model="gpt-history",
        version="1.21.1",
        requested_limit=1,
        batch_size=12,
        scope={"version": "1.21.1"},
    )
    history = SmartReviewHistoryStore(path)
    history.finish_run(
        run_id,
        processed_count=0,
        valid_count=0,
        invalid_count=0,
        request_error_count=0,
        metrics={},
        status="cancelled",
    )
    before = history.get_run(run_id)
    db.close()

    preview = preview_moddb_smart_review_resume_from_path(path, (), run_id)

    assert preview["run_id"] == run_id
    assert preview["model"] == "gpt-history"
    assert preview["batch_size"] == 12
    assert preview["requested_limit"] == 1
    assert preview["selected_count"] == 1
    assert preview["pending_count"] == 1
    assert history.get_run(run_id) == before


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


def test_new_run_history_limit_is_scoped_to_selected_version(tmp_path):
    """最近兩份歷史額度只計入目前版本，不能被其他版本搶走。"""
    store = SmartReviewHistoryStore(tmp_path / "review-version.db")
    snapshot = _snapshot()
    versions = ["1.20.1", "1.21.1", "1.20.1", "1.21.1"]
    run_ids = []
    for index, version in enumerate(versions):
        current = replace(snapshot, mc_version=version)
        run_id = store.create_run(
            [current],
            provider="gemini",
            model="test-model",
            version=version,
            requested_limit=1,
            batch_size=1,
        )
        store.finish_run(
            run_id,
            processed_count=0,
            valid_count=0,
            invalid_count=0,
            request_error_count=0,
            metrics={},
            status="partial",
        )
        run_ids.append(run_id)
        time.sleep(0.02)

    consulted, historical = store.recent_candidate_payloads(
        max_runs=2, version="1.21.1"
    )

    assert consulted == 2
    assert {item["run_id"] for item in historical} == {run_ids[1], run_ids[3]}


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


def test_planner_does_not_reuse_a_different_model_result():
    snapshot = _snapshot()
    history = {
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
        "status": "no_change",
        "ai_status": "no_change",
        "decision": "no_change",
        "_history_provider": "gemini",
        "_history_model": "old-model",
    }
    plan = plan_new_smart_review(
        [snapshot],
        [history],
        skip_valid_history=True,
        history_runs_consulted=1,
        provider="gemini",
        model="new-model",
    )
    assert plan.reused_count == 0
    assert plan.actual_ai_count == 1


def test_planner_preview_can_compare_cas_without_context():
    """預估模式可略過 context 指紋，真正規劃仍保留完整比對。"""
    snapshot = replace(_snapshot(), context=({"id": "current"},))
    history = {
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
        "context": [{"id": "history"}],
        "status": "no_change",
        "ai_status": "no_change",
        "decision": "no_change",
        "suggested_zh_tw": "",
        "confidence": 0.9,
        "reason": "舊結果",
    }

    exact = plan_new_smart_review(
        [snapshot],
        [history],
        skip_valid_history=True,
        history_runs_consulted=1,
    )
    preview = plan_new_smart_review(
        [snapshot],
        [history],
        skip_valid_history=True,
        history_runs_consulted=1,
        compare_context=False,
    )

    assert exact.reused_count == 0
    assert preview.reused_count == 1


def test_planner_does_not_reuse_history_without_provider_model_metadata():
    snapshot = _snapshot()
    plan = plan_new_smart_review(
        [snapshot],
        [
            {
                "kind": snapshot.kind,
                "mc_version": snapshot.mc_version,
                "mod_id": snapshot.mod_id,
                "entry_key": snapshot.key,
                "en_us": snapshot.en_us,
                "current_zh_tw": snapshot.current_zh_tw,
                "effective_source": snapshot.effective_source,
                "review_status": snapshot.review_status,
                "effective_revision": snapshot.effective_revision,
                "status": "no_change",
                "ai_status": "no_change",
                "decision": "no_change",
            }
        ],
        skip_valid_history=True,
        history_runs_consulted=1,
        provider="gemini",
        model="test-model",
    )
    assert plan.reused_count == 0
    assert plan.actual_ai_count == 1


def test_resume_counts_rejected_candidate_from_its_ai_status(tmp_path):
    store = SmartReviewHistoryStore(tmp_path / "rejected.db")
    run_id = store.create_run(
        [_snapshot()],
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=1,
        batch_size=1,
    )
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
                "reason": "建議修改",
            }
        ],
    )
    store.update_candidate(run_id, 1, status=SmartReviewStatus.REJECTED)
    counts = _resume_counts(store.list_candidates(run_id))
    assert counts["processed"] == 1
    assert counts["valid"] == 1
    assert counts["suggestions"] == 1


def test_prepare_resume_rejects_running_run_under_write_lock(tmp_path):
    store = SmartReviewHistoryStore(tmp_path / "running.db")
    run_id = store.create_run(
        [_snapshot()],
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=1,
        batch_size=1,
    )
    with store._write_lock():
        payload = store.repository.read_unlocked(run_id)
        payload["status"] = "running"
        store.repository.write_unlocked(run_id, payload)
    with pytest.raises(RuntimeError, match="仍在執行中"):
        store.prepare_resume(run_id, {})


def test_resume_reuses_saved_context_when_cas_snapshot_is_unchanged(
    tmp_path, monkeypatch
):
    """續跑未變更候選時沿用 JSON context，不重做全量 evidence 查詢。"""
    db = TranslationDB(tmp_path / "resume-context.db")
    try:
        db.write_back(
            "1.21.1",
            [WriteBackItem(KIND_LANG, "demo", "hello", "Hello", "你好")],
            fill_other_versions=False,
        )
        snapshot = db.smart_review_candidates(version="1.21.1", limit=1)[0]
        history = SmartReviewHistoryStore(db.path)
        run_id = history.create_run(
            [snapshot],
            provider="gemini",
            model="test-model",
            version="1.21.1",
            requested_limit=1,
            batch_size=1,
            scope={},
        )
        history.finish_run(
            run_id,
            processed_count=0,
            valid_count=0,
            invalid_count=0,
            request_error_count=0,
            metrics={},
            status="partial",
        )

        def fail_context(*_args):
            raise AssertionError("unchanged resume should reuse persisted context")

        monkeypatch.setattr(db, "_smart_review_context", fail_context)
        snapshots, _counts, _run = db.prepare_smart_review_resume(run_id)

        assert len(snapshots) == 1
        assert snapshots[0].context == snapshot.context
    finally:
        db.close()


def test_resume_rebuilds_context_when_reviewed_reference_changes(tmp_path, monkeypatch):
    """待重送候選要更新已審核參考，但保留同一 run 與人工欄位。"""
    db = TranslationDB(tmp_path / "resume-reviewed-reference.db")
    try:
        # 讓 A 維持 AI 生效，同時保存一筆人工決策欄位供續跑驗證。
        db.set_priority((SRC_AI, SRC_MANUAL))
        db.write_back(
            "1.21.1",
            [WriteBackItem(KIND_LANG, "demo", "target", "Target", "目標")],
            fill_other_versions=False,
        )
        db.write_back(
            "1.20.1",
            [WriteBackItem(KIND_LANG, "demo", "target", "Target", "舊目標")],
            fill_other_versions=False,
        )
        reference = db.smart_review_candidates(
            version="1.20.1",
            effective_source=SRC_AI,
            limit=1,
        )[0]
        db.review_manual(
            reference.entry_id,
            expected_zh_tw="舊目標",
            expected_source=SRC_AI,
            propagate=False,
        )

        db.save_manual(
            db.smart_review_candidates(
                version="1.21.1", effective_source=SRC_AI, limit=1
            )[0].entry_id,
            "A 的人工決策",
            actor="reviewer",
            propagate=False,
        )
        candidate = db.smart_review_candidates(
            version="1.21.1",
            effective_source=SRC_AI,
            review_status="unreviewed",
            limit=1,
        )[0]
        old_reference = next(
            item
            for item in candidate.context
            if item.get("entry_id") == reference.entry_id
        )

        history = SmartReviewHistoryStore(db.path)
        run_id = history.create_run(
            [candidate],
            provider="gemini",
            model="test-model",
            version="1.21.1",
            requested_limit=1,
            batch_size=1,
            scope={"version": "1.21.1", "effective_source": SRC_AI},
        )
        history.mark_pending_cancelled(run_id)
        history.finish_run(
            run_id,
            processed_count=0,
            valid_count=0,
            invalid_count=0,
            request_error_count=0,
            metrics={},
            status="cancelled",
        )

        with db._tx(keep_review=True) as conn:
            conn.execute(
                "UPDATE translation SET zh_tw=?, revision=revision+1 "
                "WHERE entry_id=? AND source=?",
                ("新目標", reference.entry_id, SRC_AI),
            )

        payloads = []

        def fake_call(**kwargs):
            payloads.append(kwargs["payload"])
            return _response(kwargs["payload"]["items"])

        monkeypatch.setattr(
            "translation_tool.translation_db.smart_review_provider.call_gemini_requests",
            fake_call,
        )
        resumed = resume_smart_review(
            db,
            run_id=run_id,
            model_name="test-model",
            api_key="test-key",
        )

        assert resumed.run_id == run_id
        assert len(payloads) == 1
        sent_reference = next(
            item
            for item in payloads[0]["items"][0]["context"]
            if item.get("entry_id") == reference.entry_id
        )
        assert old_reference["zh_tw"] == "舊目標"
        assert sent_reference["zh_tw"] == "新目標"

        stored = history.list_candidates(run_id)
        assert len(stored) == 1
        assert stored[0].run_id == run_id
        assert stored[0].manual_zh_tw == "A 的人工決策"
        assert stored[0].context == tuple(payloads[0]["items"][0]["context"])
    finally:
        db.close()


def test_prepare_resume_does_not_recount_already_excluded_candidate(tmp_path):
    store = SmartReviewHistoryStore(tmp_path / "excluded-idempotent.db")
    run_id = store.create_run(
        [_snapshot()],
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=1,
        batch_size=1,
    )

    store.finish_run(
        run_id,
        processed_count=0,
        valid_count=0,
        invalid_count=0,
        request_error_count=0,
        metrics={},
        status="partial",
    )
    first = store.prepare_resume(run_id, {1: None})
    assert first["excluded"] == 1
    store.finish_run(
        run_id,
        processed_count=0,
        valid_count=0,
        invalid_count=0,
        request_error_count=0,
        metrics={"excluded_count": first["excluded"]},
        status="partial",
    )

    second = store.prepare_resume(run_id, {1: None})
    assert second["excluded"] == 0
    assert store.get_run(run_id)["metrics"]["excluded_count"] == 1
    candidate = store.list_candidates(run_id)[0]
    assert candidate.status == SmartReviewStatus.CANCELLED
    assert candidate.validation_error == "excluded_from_scope"


def test_resume_provider_initialization_failure_does_not_leave_running_status(
    tmp_path, monkeypatch
):
    db = TranslationDB(tmp_path / "resume-open-failure.db")
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "demo", "hello", "Hello", "你好")],
        fill_other_versions=False,
    )
    snapshot = db.smart_review_candidates(version="1.21.1", limit=1)[0]
    store = SmartReviewHistoryStore(db.path)
    run_id = store.create_run(
        [snapshot],
        provider="gemini",
        model="gemini-test",
        version="1.21.1",
        requested_limit=1,
        batch_size=1,
    )
    store.finish_run(
        run_id,
        processed_count=0,
        valid_count=0,
        invalid_count=0,
        request_error_count=1,
        metrics={},
        status="partial",
    )

    def fail_open(**_kwargs):
        raise ValueError("provider initialization failed")

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review_run.SmartReviewProviderSession.open",
        fail_open,
    )
    with pytest.raises(ValueError, match="provider initialization failed"):
        resume_smart_review(db, run_id=run_id, api_key="test-key")
    assert store.get_run(run_id)["status"] == "partial"
    db.close()


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


def test_cancelled_before_provider_request_does_not_count_actual_ai(tmp_path):
    db = TranslationDB(tmp_path / "cancel-before-request.db")
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "demo", "hello", "Hello", "你好")],
        fill_other_versions=False,
    )
    result = run_smart_review(
        db,
        model_name="gpt-test",
        provider="chatgpt",
        api_key="",
        limit=1,
        batch_size=1,
        cancel_check=lambda: True,
    )
    assert result.status == "cancelled"
    assert result.metrics["actual_ai_count"] == 0
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


def test_resume_rebuilds_context_for_invalidated_snapshot(tmp_path, monkeypatch):
    db = TranslationDB(tmp_path / "fresh-context.db")
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
    monkeypatch.setattr(
        db,
        "_smart_review_context",
        lambda *_args: [{"id": "fresh-context"}],
    )
    resume_smart_review(
        db, run_id=first.run_id, model_name="gemini-test", api_key="test-key"
    )
    candidate = SmartReviewHistoryStore(db.path).get_candidate(first.run_id, 1)
    assert candidate["context"] == [{"id": "fresh-context"}]
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
