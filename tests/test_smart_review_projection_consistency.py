"""SQLite 套用紀錄與 JSON 歷史之間的一致性測試（P3-03）。

翻譯寫入 SQLite、候選狀態寫入 JSON，兩者之間沒有共同交易。這裡驗證：
SQLite 已提交而 JSON 沒跟上（含程序在 JSON 寫入的各個替換點被殺掉）時，
讀取路徑的對帳會補齊候選狀態、不會重複套用，而且重複對帳結果不變。
"""

from __future__ import annotations

import sqlite3
import sys

import pytest

from tests.smart_review_history_support import (
    CRASH_EXIT_CODE,
    assert_disk_manifest_matches_candidates,
    assert_summary_consistent,
    create_store_run,
    make_result,
    raw_candidates,
    run_worker,
    seed_ready_run,
    snapshot_bytes,
)
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)

SINGLE = "single"
CHUNKED = "chunked"
CHUNK_SIZE = {SINGLE: 1000, CHUNKED: 3}


def _apply_records(path) -> dict[int, dict]:
    """讀取 SQLite 的套用紀錄，轉成 reconcile_applied 使用的對照。"""
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT id, note, entry_id, batch, at FROM history "
            "WHERE action='smart_review_apply' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    records: dict[int, dict] = {}
    for _id, note, entry_id, batch, applied_at in rows:
        ordinal = int(str(note).rsplit("ordinal=", 1)[1])
        records[ordinal] = {
            "entry_id": entry_id,
            "batch": batch,
            "applied_at": applied_at,
        }
    return records


@pytest.fixture(params=[SINGLE, CHUNKED])
def layout(request):
    """單檔與分片兩種儲存格式。"""
    return request.param


def _assert_consistent(store, run_id, total, layout):
    assert_summary_consistent(store, run_id, total)
    if layout == CHUNKED:
        assert_disk_manifest_matches_candidates(store, run_id, total)


# ---------------------------------------------------------------------------
# 同程序內的 JSON 寫入失敗
# ---------------------------------------------------------------------------


def test_sqlite_commit_with_failed_json_write_survives_history_retention(
    tmp_path, monkeypatch, layout
):
    """JSON 回寫失敗後 SQLite 已套用；下一次讀取補齊狀態，重複讀取不再改檔。"""
    total = 12
    path = tmp_path / "reconcile.db"
    store, run_id, snapshots = seed_ready_run(
        path, total, chunk_size=CHUNK_SIZE[layout]
    )
    db = TranslationDB(path)
    try:
        original = SmartReviewHistoryStore.update_candidate

        def failing(self, run, ordinal, **updates):
            if updates.get("status") == "applied":
                raise OSError("模擬 JSON 原子替換失敗")
            return original(self, run, ordinal, **updates)

        # JSON 暫時不可寫：回寫與（套用前的）對帳都沒有發生。
        with monkeypatch.context() as patch:
            patch.setattr(SmartReviewHistoryStore, "update_candidate", failing)
            patch.setattr(
                SmartReviewHistoryStore, "reconcile_applied", lambda *_a, **_k: 0
            )
            assert db.apply_smart_review_candidate(run_id, 2) == "applied"
            assert db.apply_smart_review_candidate(run_id, 7) == "applied"

        # JSON 尚未跟上；SQLite 已有兩筆套用紀錄。
        assert store.get_candidate(run_id, 2)["status"] == "ready"
        entry_id = next(
            snapshot.entry_id for snapshot in snapshots if snapshot.ordinal == 2
        )
        # 模擬同一條目之後又有五次歷史寫入；pending marker 仍必須可供恢復。
        for number in range(5):
            with db._tx() as conn:
                conn.execute(
                    "INSERT INTO history "
                    "(entry_id, batch, action, old_zh_tw, new_zh_tw, note) "
                    "VALUES (?, ?, 'manual', '', ?, ?)",
                    (entry_id, f"follow-up-{number}", f"後續-{number}", str(number)),
                )
        assert db._one(
            "SELECT projection_state FROM history "
            "WHERE entry_id=? AND action='smart_review_apply'",
            (entry_id,),
        ) == ("pending",)
        records = _apply_records(path)
        assert sorted(records) == [2, 7]

        # 重開資料庫後仍須保留 pending marker，確認故障復原不依賴原連線狀態。
        db.close()
        db = TranslationDB(path)
        store = SmartReviewHistoryStore(path)
        assert db._one(
            "SELECT projection_state FROM history "
            "WHERE entry_id=? AND action='smart_review_apply'",
            (entry_id,),
        ) == ("pending",)

        rows = db.list_smart_review_candidates(run_id)
        statuses = {row.ordinal: row.status for row in rows}
        assert statuses[2] == statuses[7] == "applied"
        assert {s for o, s in statuses.items() if o not in (2, 7)} == {"ready"}
        for ordinal in (2, 7):
            row = next(r for r in rows if r.ordinal == ordinal)
            assert row.applied_batch == records[ordinal]["batch"]
        _assert_consistent(store, run_id, total, layout)

        # 冪等：再讀、再對帳都不改任何檔案，也不會重複套用。
        before = snapshot_bytes(store)
        db.list_smart_review_candidates(run_id)
        db.load_smart_review_candidate_page(run_id, limit=5)
        db.smart_review_run_breakdown(run_id)
        assert db._one(
            "SELECT projection_state FROM history "
            "WHERE entry_id=? AND action='smart_review_apply'",
            (entry_id,),
        ) == ("projected",)
        assert db.count_smart_review_candidates_for_run(run_id, status="applied") == 2
        assert store.reconcile_applied(run_id, _apply_records(path)) == 0
        assert snapshot_bytes(store) == before
        assert db.apply_smart_review_candidate(run_id, 2) == "not_ready"
        assert len(_apply_records(path)) == 2
    finally:
        db.close()


def test_only_candidates_missing_from_json_are_reconciled(
    tmp_path, monkeypatch, layout
):
    """部分候選的 JSON 回寫失敗：對帳只補這些，已同步的不重寫。"""
    total = 10
    path = tmp_path / "partial.db"
    store, run_id, _snapshots = seed_ready_run(
        path, total, chunk_size=CHUNK_SIZE[layout]
    )
    db = TranslationDB(path)
    try:
        original = SmartReviewHistoryStore.update_candidate

        def fail_odd(self, run, ordinal, **updates):
            if updates.get("status") == "applied" and ordinal % 2:
                raise OSError("模擬 JSON 原子替換失敗")
            return original(self, run, ordinal, **updates)

        with monkeypatch.context() as patch:
            patch.setattr(SmartReviewHistoryStore, "update_candidate", fail_odd)
            patch.setattr(
                SmartReviewHistoryStore, "reconcile_applied", lambda *_a, **_k: 0
            )
            results = [db.apply_smart_review_candidate(run_id, o) for o in range(1, 11)]
        assert results == ["applied"] * total

        assert [c["status"] for c in raw_candidates(store, run_id, total)] == [
            "ready" if o % 2 else "applied" for o in range(1, 11)
        ]
        assert store.reconcile_applied(run_id, _apply_records(path)) == 5
        assert store.reconcile_applied(run_id, _apply_records(path)) == 0
        assert store.breakdown(run_id)["status"] == {"applied": total}
        _assert_consistent(store, run_id, total, layout)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 程序在 JSON 寫入的各個替換點被殺掉
# ---------------------------------------------------------------------------

CRASH_CASES = [
    (SINGLE, "before_manifest", "ready"),
    (SINGLE, "after_manifest", "applied"),
    (CHUNKED, "before_chunk", "ready"),
    (CHUNKED, "after_chunk", "applied"),
    (CHUNKED, "before_manifest", "applied"),
    (CHUNKED, "after_manifest", "applied"),
]


@pytest.mark.skipif(sys.platform == "win32", reason="以 POSIX 程序終止語意為準")
@pytest.mark.parametrize(
    ("layout_name", "mode", "json_status_after_crash"),
    CRASH_CASES,
    ids=[f"{name}-{mode}" for name, mode, _status in CRASH_CASES],
)
def test_process_killed_after_sqlite_commit_is_reconciled(
    tmp_path, layout_name, mode, json_status_after_crash
):
    """SQLite 套用提交後，程序在 JSON 寫入的各個替換點被殺：讀取時補齊，且冪等。"""
    total, ordinal = 9, 4
    path = tmp_path / "crash.db"
    store, run_id, snapshots = seed_ready_run(
        path, total, chunk_size=CHUNK_SIZE[layout_name]
    )
    entry_id = next(s.entry_id for s in snapshots if s.ordinal == ordinal)

    run_worker(
        "crash_apply",
        tmp_path,
        expect=CRASH_EXIT_CODE,
        db=path,
        run=run_id,
        ordinal=ordinal,
        mode=mode,
    )

    # SQLite 已提交；JSON 停在崩潰點對應的狀態。
    records = _apply_records(path)
    assert list(records) == [ordinal]
    assert store.get_candidate(run_id, ordinal)["status"] == json_status_after_crash

    db = TranslationDB(path)
    try:
        # 不開啟 reviewer，單靠條目標籤查詢也會對帳。
        marks = db.smart_review_marks([entry_id])
        assert marks[entry_id].status == "applied"
        rows = db.list_smart_review_candidates(run_id)
        statuses = {row.ordinal: row.status for row in rows}
        assert statuses[ordinal] == "applied"
        assert {s for o, s in statuses.items() if o != ordinal} == {"ready"}
        assert (
            next(r for r in rows if r.ordinal == ordinal).applied_batch
            == (records[ordinal]["batch"])
        )
        assert store.breakdown(run_id)["status"] == {
            "applied": 1,
            "ready": total - 1,
        }
        _assert_consistent(store, run_id, total, layout_name)

        # 冪等且不重複套用。
        before = snapshot_bytes(store)
        for _ in range(2):
            db.list_smart_review_candidates(run_id)
            assert store.reconcile_applied(run_id, records) == 0
        assert snapshot_bytes(store) == before
        assert db.apply_smart_review_candidate(run_id, ordinal) == "not_ready"
        assert list(_apply_records(path)) == [ordinal]
        # 其他候選仍可正常套用。
        assert db.apply_smart_review_candidate(run_id, 1) == "applied"
        assert store.breakdown(run_id)["status"] == {
            "applied": 2,
            "ready": total - 2,
        }
    finally:
        db.close()


def test_marks_reconcile_only_requested_page_entries(tmp_path, monkeypatch):
    """條目清單只對帳目前頁面的 entry_id，不掃描整個批次候選。"""
    path = tmp_path / "targeted-marks.db"
    _store, run_id, snapshots = seed_ready_run(path, 4, chunk_size=CHUNK_SIZE[CHUNKED])
    db = TranslationDB(path)
    try:
        target, unrelated = snapshots[0], snapshots[1]
        records = {
            target.ordinal: {
                "entry_id": target.entry_id,
                "batch": "target-batch",
                "applied_at": "2026-01-01 00:00:00",
            },
            unrelated.ordinal: {
                "entry_id": unrelated.entry_id,
                "batch": "unrelated-batch",
                "applied_at": "2026-01-01 00:00:00",
            },
        }
        captured = []
        monkeypatch.setattr(db, "_smart_review_apply_records", lambda _run: records)

        def capture_reconcile(history, current_run_id, current_records, **_kwargs):
            captured.append((current_run_id, dict(current_records)))
            return 0

        monkeypatch.setattr(
            SmartReviewHistoryStore, "reconcile_applied", capture_reconcile
        )
        db.smart_review_marks([target.entry_id])

        assert captured == [(run_id, {target.ordinal: records[target.ordinal]})]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# reconcile_applied 本身的語意（不需要資料庫）
# ---------------------------------------------------------------------------


def test_reconcile_applied_semantics_and_idempotence(tmp_path, layout):
    """依 entry_id 驗證、以最新紀錄為準、不同狀態都能補成 applied，重複呼叫不變。"""
    total = 8
    path = tmp_path / "semantics.db"
    store, run_id = create_store_run(path, total, chunk_size=CHUNK_SIZE[layout])
    store.record_results(
        run_id, [make_result(o) for o in range(1, 8)]
    )  # 第 8 筆維持 pending
    store.update_candidate(run_id, 2, status="rejected")
    store.update_candidate(run_id, 3, status="stale", validation_error="snapshot_stale")
    store.update_candidate(
        run_id,
        4,
        status="invalid_format",
        validation_error="suggested_zh_tw_format_invalid",
    )
    store.update_candidate(
        run_id,
        5,
        status="applied",
        applied_batch="old",
        applied_at="2025-01-01 00:00:00",
    )

    def record(ordinal, batch="b1"):
        return {
            "entry_id": ordinal,  # make_snapshots 的 entry_id == ordinal
            "batch": batch,
            "applied_at": "2026-01-01 00:00:00",
        }

    records = {o: record(o) for o in (1, 2, 3, 4, 5)}
    records[6] = {**record(6), "entry_id": 999}  # entry_id 不符：必須忽略
    records[99] = record(99)  # 不存在的 ordinal：忽略

    assert store.reconcile_applied(run_id, records) == 5
    by_ordinal = {c["ordinal"]: c for c in raw_candidates(store, run_id, total)}
    for ordinal in (1, 2, 3, 4, 5):
        assert by_ordinal[ordinal]["status"] == "applied"
        assert by_ordinal[ordinal]["applied_batch"] == "b1"
        assert by_ordinal[ordinal]["applied_at"] == "2026-01-01 00:00:00"
    assert by_ordinal[6]["status"] == "ready"
    assert by_ordinal[8]["status"] == "pending"
    _assert_consistent(store, run_id, total, layout)

    before = snapshot_bytes(store)
    assert store.reconcile_applied(run_id, records) == 0
    assert store.reconcile_applied(run_id, {}) == 0
    assert snapshot_bytes(store) == before

    # 同一候選之後又被套用（新的 batch）：以最新紀錄為準，狀態計數不變。
    assert store.reconcile_applied(run_id, {1: record(1, batch="b2")}) == 1
    assert store.get_candidate(run_id, 1)["applied_batch"] == "b2"
    assert store.breakdown(run_id)["status"] == {"applied": 5, "ready": 2, "pending": 1}
    _assert_consistent(store, run_id, total, layout)

    # 批次不存在時不丟例外。
    assert store.reconcile_applied("missing_run", records) == 0


# ---------------------------------------------------------------------------
# 已知的實作不一致
# ---------------------------------------------------------------------------


def test_single_file_run_summary_status_counts_follow_candidate_updates(tmp_path):
    """單檔批次 get_run() 的 status_counts 應與分片批次一樣隨候選狀態更新。"""
    total = 4
    path = tmp_path / "single-counts.db"
    store, run_id = create_store_run(path, total, chunk_size=1000)
    store.record_results(run_id, [make_result(o) for o in range(1, total + 1)])
    store.update_candidate(run_id, 1, status="applied")
    store.reconcile_applied(
        run_id, {2: {"entry_id": 2, "batch": "b", "applied_at": "2026-01-01 00:00:00"}}
    )
    assert store.get_run(run_id)["status_counts"] == {"applied": 2, "ready": 2}
