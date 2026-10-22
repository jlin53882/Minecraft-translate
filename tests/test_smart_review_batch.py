"""智慧校對批量動作服務的部分成功與取消測試。"""

from __future__ import annotations

from app.services_impl import moddb_smart_review_service as service
from translation_tool.translation_db import SmartReviewCandidate


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


class _FakeDB:
    """只模擬 TranslationDB 的兩個批量動作。"""

    def __init__(self):
        self.closed = False

    def apply_smart_review_candidate(self, _run_id, ordinal, *, draft_zh_tw=None):
        assert draft_zh_tw is None
        return "applied" if ordinal == 1 else "stale"

    def reject_smart_review_candidate(self, _run_id, ordinal):
        return "rejected" if ordinal == 1 else "not_ready"

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
