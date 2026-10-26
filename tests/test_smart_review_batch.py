"""智慧校對批量動作服務的部分成功與取消測試。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

from app.services_impl import moddb_smart_review_service as service
from tests.smart_review_history_support import seed_ready_run
from translation_tool.translation_db import SmartReviewCandidate
from translation_tool.translation_db.repository import TranslationDB


def _candidate(ordinal: int) -> SmartReviewCandidate:
    """建立批量服務用的最小 ready 候選。"""
    return SmartReviewCandidate(
        run_id="run-1",
        ordinal=ordinal,
        entry_id=ordinal,
        kind="lang",
        mc_version="1.21.1",
        mod_id="demo",
        key=f"key{ordinal}",
        en_us="Hello",
        current_zh_tw="哈囉",
        effective_source=0,
        review_status="unreviewed",
        effective_revision=1,
        context=(),
        decision="suggest_revision",
        suggested_zh_tw="你好",
        status="ready",
    )


def _stale_candidate(ordinal: int = 1) -> SmartReviewCandidate:
    """建立可供批量覆蓋測試使用的 stale 候選。"""
    candidate = _candidate(ordinal)
    return replace(
        candidate,
        status="stale",
        draft_zh_tw="人工確認譯文",
    )


class _FakeDB:
    """只模擬 TranslationDB 的批量動作與 stale 對齊流程。"""

    def __init__(self):
        self.closed = False
        self.realign_calls = []
        self.manual_edit_calls = []

    def apply_smart_review_candidate(self, _run_id, ordinal, *, draft_zh_tw=None):
        assert draft_zh_tw is None
        return "applied" if ordinal == 1 else "stale"

    def reject_smart_review_candidate(self, _run_id, ordinal):
        return "rejected" if ordinal == 1 else "not_ready"

    def realign_smart_review_candidate(self, run_id, ordinal):
        self.realign_calls.append((run_id, ordinal))
        return "realigned"

    def apply_smart_review_manual_edit(self, run_id, ordinal, draft_zh_tw):
        self.manual_edit_calls.append((run_id, ordinal, draft_zh_tw))
        return "applied"

    def close(self):
        self.closed = True


def test_batch_service_reports_partial_results_and_progress(monkeypatch):
    """單筆 stale 不會中止其他候選，且每筆都有進度回報。"""
    database = _FakeDB()
    monkeypatch.setattr(service, "_open", lambda *_args, **_kwargs: database)
    progress = []

    result = service.apply_moddb_smart_review_batch_from_path(
        "db.sqlite",
        (),
        [_candidate(1), _candidate(2)],
        progress=progress.append,
    )

    assert result.requested == 2
    assert result.processed == 2
    assert result.cancelled is False
    assert result.count("applied") == 1
    assert result.count("stale") == 1
    assert [item["processed"] for item in progress] == [1, 2]
    assert database.closed is True


def test_batch_service_stops_before_next_candidate_when_cancelled(monkeypatch):
    """取消旗標只停止後續候選，已完成的寫入仍保留。"""
    database = _FakeDB()
    monkeypatch.setattr(service, "_open", lambda *_args, **_kwargs: database)
    progress = []

    result = service.apply_moddb_smart_review_batch_from_path(
        "db.sqlite",
        (),
        [_candidate(1), _candidate(2), _candidate(3)],
        progress=progress.append,
        cancel_check=lambda: len(progress) >= 1,
    )

    assert result.requested == 3
    assert result.processed == 1
    assert result.cancelled is True
    assert result.items[0].status == "applied"
    assert database.closed is True


def test_batch_service_override_realigns_json_snapshot_before_apply(monkeypatch):
    """批量覆蓋先對齊 JSON 來源譯文，再沿用人工 CAS 套用候選草稿。"""
    database = _FakeDB()
    monkeypatch.setattr(service, "_open", lambda *_args, **_kwargs: database)

    result = service.apply_moddb_smart_review_batch_from_path(
        "db.sqlite",
        (),
        [_stale_candidate()],
        action="override",
    )

    assert result.action == "override"
    assert result.count("applied") == 1
    assert database.realign_calls == [("run-1", 1)]
    assert database.manual_edit_calls == [("run-1", 1, "人工確認譯文")]
    assert database.closed is True


def _load_ready_candidates(path, run_id, limit):
    """從 JSON 批次載入可套用候選，避免測試繞過公開資料層。"""
    db = TranslationDB(path)
    try:
        total, candidates = db.load_smart_review_candidate_page(
            run_id, status="ready", limit=limit
        )
        assert total == limit
        assert len(candidates) == limit
        return candidates
    finally:
        db.close()


def test_json_batch_apply_bounds_transactions_and_projection_refresh(
    monkeypatch, tmp_path
):
    """大量套用共用 bounded transactions，並在每批只刷新一次 effective。"""
    path = tmp_path / "smart-review.db"
    _store, run_id, _snapshots = seed_ready_run(path, 1000, chunk_size=200)
    candidates = _load_ready_candidates(path, run_id, 1000)

    counters = {"tx": 0, "refresh": 0, "refresh_ids": 0}
    original_tx = TranslationDB._tx
    original_refresh = TranslationDB._refresh

    def counted_tx(self, *args, **kwargs):
        counters["tx"] += 1
        return original_tx(self, *args, **kwargs)

    def counted_refresh(self, conn, entry_ids):
        ids = list(entry_ids)
        counters["refresh"] += 1
        counters["refresh_ids"] += len(ids)
        return original_refresh(self, conn, ids)

    monkeypatch.setattr(TranslationDB, "_tx", counted_tx)
    monkeypatch.setattr(TranslationDB, "_refresh", counted_refresh)

    result = service.apply_moddb_smart_review_batch_from_path(path, (), candidates)

    assert result.processed == 1000
    assert result.count("applied") == 1000
    # One open/sync transaction, three 400-row chunks and one projection marker.
    assert counters["tx"] <= 5
    assert counters["refresh"] == 3
    assert counters["refresh_ids"] == 1000

    db = TranslationDB(path)
    try:
        assert (
            db.count_smart_review_candidates_for_run(run_id, status="applied") == 1000
        )
        assert (
            db._one(
                "SELECT COUNT(*) FROM history WHERE action='smart_review_apply'", ()
            )[0]
            == 1000
        )
        assert (
            db._one("SELECT COUNT(*) FROM effective WHERE zh_tw LIKE '%（修訂）'", ())[
                0
            ]
            == 1000
        )
    finally:
        db.close()


def test_json_batch_apply_savepoint_keeps_other_candidates(monkeypatch, tmp_path):
    """單筆資料層錯誤只回復該候選，不污染同一批其他寫入。"""
    path = tmp_path / "smart-review-savepoint.db"
    _store, run_id, _snapshots = seed_ready_run(path, 3, chunk_size=3)
    candidates = _load_ready_candidates(path, run_id, 3)
    failed_entry_id = candidates[1].entry_id
    original_write = TranslationDB._write_smart_review_manual_row

    def fail_one(self, conn, entry_id, proposed, **kwargs):
        if entry_id == failed_entry_id:
            raise RuntimeError("injected candidate failure")
        return original_write(self, conn, entry_id, proposed, **kwargs)

    monkeypatch.setattr(TranslationDB, "_write_smart_review_manual_row", fail_one)

    result = service.apply_moddb_smart_review_batch_from_path(path, (), candidates)

    assert [item.status for item in result.items] == ["applied", "error", "applied"]
    db = TranslationDB(path)
    try:
        assert db.count_smart_review_candidates_for_run(run_id, status="applied") == 2
        assert (
            db._one(
                "SELECT COUNT(*) FROM history WHERE action='smart_review_apply'", ()
            )[0]
            == 2
        )
        assert (
            db._one(
                "SELECT COUNT(*) FROM history "
                "WHERE action='smart_review_apply' AND entry_id=?",
                (failed_entry_id,),
            )[0]
            == 0
        )
        assert (
            db._one("SELECT zh_tw FROM effective WHERE entry_id=?", (failed_entry_id,))[
                0
            ]
            == "文字 2"
        )
    finally:
        db.close()


def test_json_batch_apply_reports_every_candidate_when_transaction_cannot_start(
    monkeypatch, tmp_path
):
    """整批 transaction 無法開始時，每個候選都要收到錯誤結果。"""
    path = tmp_path / "smart-review-transaction-start.db"
    _store, run_id, _snapshots = seed_ready_run(path, 3, chunk_size=3)
    candidates = _load_ready_candidates(path, run_id, 3)
    payload = [
        {
            "run_id": candidate.run_id,
            "ordinal": candidate.ordinal,
            "status": candidate.status,
            "draft_zh_tw": candidate.draft_zh_tw,
            "suggested_zh_tw": candidate.suggested_zh_tw,
        }
        for candidate in candidates
    ]

    @contextmanager
    def fail_transaction(*_args, **_kwargs):
        if False:
            yield None
        raise RuntimeError("injected transaction start failure")

    monkeypatch.setattr(TranslationDB, "_tx", fail_transaction)
    db = TranslationDB(path)
    try:
        result, cancelled = db.apply_smart_review_candidate_batch(run_id, payload)
    finally:
        db.close()

    assert cancelled is False
    assert [ordinal for ordinal, _status, _error in result] == [1, 2, 3]
    assert [status for _ordinal, status, _error in result] == [
        "error",
        "error",
        "error",
    ]
    assert all("injected transaction start failure" in error for _, _, error in result)


def test_json_batch_apply_reports_every_candidate_when_transaction_commit_fails(
    monkeypatch, tmp_path
):
    """commit 失敗回滾整批時，已處理與未處理候選都要回報錯誤。"""
    path = tmp_path / "smart-review-transaction-commit.db"
    _store, run_id, _snapshots = seed_ready_run(path, 3, chunk_size=3)
    candidates = _load_ready_candidates(path, run_id, 3)
    payload = [
        {
            "run_id": candidate.run_id,
            "ordinal": candidate.ordinal,
            "status": candidate.status,
            "draft_zh_tw": candidate.draft_zh_tw,
            "suggested_zh_tw": candidate.suggested_zh_tw,
        }
        for candidate in candidates
    ]
    original_tx = TranslationDB._tx

    @contextmanager
    def fail_commit(self, *args, **kwargs):
        with original_tx(self, *args, **kwargs) as conn:
            yield conn
            raise RuntimeError("injected transaction commit failure")

    monkeypatch.setattr(TranslationDB, "_tx", fail_commit)
    db = TranslationDB(path)
    try:
        result, cancelled = db.apply_smart_review_candidate_batch(run_id, payload)
    finally:
        db.close()

    assert cancelled is False
    assert [ordinal for ordinal, _status, _error in result] == [1, 2, 3]
    assert [status for _ordinal, status, _error in result] == [
        "error",
        "error",
        "error",
    ]
    assert all("injected transaction commit failure" in error for _, _, error in result)


class _OrdinalBatchDB(_FakeDB):
    """回傳非連續 ordinal，驗證服務層不以結果長度猜候選。"""

    def apply_smart_review_candidate_batch(
        self, _run_id, candidates, *, progress_cancel, progress, **_kwargs
    ):
        del progress_cancel
        selected = tuple(candidates)
        result = (selected[-1]["ordinal"], "applied", None)
        if progress is not None:
            progress(len(selected), len(selected), result)
        return (result,), False


def test_fast_batch_progress_matches_candidate_by_ordinal(monkeypatch):
    """服務層進度應用實際 ordinal 對應 key，而非結果前綴位置。"""
    database = _OrdinalBatchDB()
    monkeypatch.setattr(service, "_open", lambda *_args, **_kwargs: database)
    progress = []

    result = service.apply_moddb_smart_review_batch_from_path(
        "db.sqlite",
        (),
        [_candidate(3), _candidate(9)],
        progress=progress.append,
    )

    assert result.processed == 1
    assert result.items[0].ordinal == 9
    assert result.items[0].key == "key9"
    assert progress[0]["item"].ordinal == 9
    assert progress[0]["item"].key == "key9"
    assert database.closed is True
