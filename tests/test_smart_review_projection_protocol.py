"""JSON 投影協議：projection revision／state 與 SQLite audit 差異回報。"""

import pytest

from tests.smart_review_history_support import create_store_run, seed_ready_run
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)
from translation_tool.translation_db.smart_review_status import ProjectionState

CHUNK_SIZE = {"single": 1000, "chunked": 3}


@pytest.fixture(params=["single", "chunked"])
def layout(request):
    return request.param


def _projection(store, run_id):
    return store.get_run(run_id).get("projection") or {}


def test_enum_values():
    assert {s.value for s in ProjectionState} == {
        "projected",
        "pending_projection",
        "projection_failed",
        "reconciled",
    }


def test_revision_increases_on_each_candidate_change(tmp_path, layout):
    store, run_id, _ = seed_ready_run(
        tmp_path / "rev.db", 6, chunk_size=CHUNK_SIZE[layout]
    )
    assert store.update_candidate(run_id, 1, status="rejected")
    first = _projection(store, run_id)
    assert first["state"] == "projected" and first["revision"] >= 1
    assert store.update_candidate(run_id, 2, status="rejected")
    assert _projection(store, run_id)["revision"] == first["revision"] + 1


def test_cancelling_pending_candidates_marks_projection(tmp_path, layout):
    store, run_id = create_store_run(
        tmp_path / "cancel.db", 6, chunk_size=CHUNK_SIZE[layout]
    )

    assert store.mark_pending_cancelled(run_id) == 6
    projection = _projection(store, run_id)
    assert projection["state"] == "projected"
    assert projection["revision"] == 1


def test_reconcile_marks_reconciled_and_repeat_is_noop(tmp_path, layout):
    store, run_id, _ = seed_ready_run(
        tmp_path / "rec.db", 6, chunk_size=CHUNK_SIZE[layout]
    )
    record = {"entry_id": 0, "batch": "b1", "applied_at": "2026-01-01 00:00:00"}
    candidate = store.get_candidate(run_id, 2)
    record["entry_id"] = candidate["entry_id"]
    assert store.reconcile_applied(run_id, {2: record}) == 1
    info = _projection(store, run_id)
    assert info["state"] == "reconciled"
    assert store.reconcile_applied(run_id, {2: record}) == 0
    assert _projection(store, run_id) == info


def test_projection_report_detects_pending_then_clears(tmp_path, monkeypatch, layout):
    path = tmp_path / "report.db"
    _store, run_id, _ = seed_ready_run(path, 6, chunk_size=CHUNK_SIZE[layout])
    db = TranslationDB(path)
    try:
        assert db.smart_review_projection_report(run_id)["pending_ordinals"] == []
        with monkeypatch.context() as patch:
            patch.setattr(
                SmartReviewHistoryStore,
                "update_candidate",
                lambda *_a, **_k: (_ for _ in ()).throw(OSError("json down")),
            )
            patch.setattr(
                SmartReviewHistoryStore, "reconcile_applied", lambda *_a, **_k: 0
            )
            assert db.apply_smart_review_candidate(run_id, 3) == "applied"
        report = db.smart_review_projection_report(run_id)
        assert report["state"] == "pending_projection"
        assert report["pending_ordinals"] == [3]
        db.list_smart_review_candidates(run_id)  # 讀取觸發對帳
        report = db.smart_review_projection_report(run_id)
        assert report["pending_ordinals"] == []
        assert report["state"] == "reconciled"
    finally:
        db.close()
