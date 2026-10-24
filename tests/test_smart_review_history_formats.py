"""智慧校對歷史三種儲存格式的混合讀取測試（P3-03）。

涵蓋：舊版單檔 JSON、舊版分片 JSON（沒有分片清單與 checkpoint）、目前的單檔
與分片 JSON，以及更舊的 SQLite 批次表。同一個歷史目錄與資料庫內三種格式並存時，
列表、分頁、統計、套用與條目標籤都必須維持一致。
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.smart_review_history_support import (
    assert_summary_consistent,
    create_store_run,
    make_result,
    raw_candidates,
    seed_translation_db,
)
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
    history_directory_for_db,
)
from translation_tool.translation_db.smart_review_history_query import HistoryQuery


def _legacy_candidate(
    ordinal: int, *, status: str = "pending", error: str | None = None, **extra: Any
) -> dict[str, Any]:
    """舊版候選：沒有 ai_status、original_snapshot、twin_entry_ids。"""
    candidate: dict[str, Any] = {
        "run_id": "legacy",
        "ordinal": ordinal,
        "entry_id": 1000 + ordinal,
        "kind": "lang",
        "mc_version": "1.21.1",
        "mod_id": "demo",
        "entry_key": f"key_{ordinal}",
        "en_us": f"Text {ordinal}",
        "current_zh_tw": f"文字 {ordinal}",
        "effective_source": 3,
        "review_status": "unreviewed",
        "effective_revision": 1,
        "context": [],
        "decision": "suggest_revision" if status == "ready" else None,
        "suggested_zh_tw": f"文字 {ordinal}（修訂）" if status == "ready" else None,
        "issue_types": [],
        "evidence_ids": [],
        "confidence": 0.8 if status != "pending" else None,
        "reason": "舊版理由" if status == "ready" else None,
        "validation_error": error,
        "status": status,
        "applied_batch": None,
        "applied_at": None,
    }
    candidate.update(extra)
    return candidate


def _legacy_header(run_id: str, *, started_at: str, selected: int) -> dict[str, Any]:
    """舊版批次標頭：沒有 owner_pid、checkpoint_at、status_counts。"""
    return {
        "schema_version": 1,
        "id": run_id,
        "provider": "gemini",
        "model": "old-model",
        "scope_version": "1.21.1",
        "requested_limit": selected,
        "batch_size": selected,
        "scope": {},
        "started_at": started_at,
        "finished_at": started_at,
        "status": "completed",
        "selected_count": selected,
        "processed_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "request_error_count": 0,
        "metrics": {"legacy_metric": 7},
        "legacy_extra_field": {"keep": "me"},
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _set_started_at(
    store: SmartReviewHistoryStore, run_id: str, started_at: str
) -> None:
    """改寫批次 manifest 的開始時間，讓跨格式排序不依賴實際時鐘。"""
    path = store.path_for(run_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["started_at"] = started_at
    _write_json(path, payload)


# ---------------------------------------------------------------------------
# 舊版單檔 JSON
# ---------------------------------------------------------------------------


@pytest.fixture
def legacy_single(tmp_path):
    """寫入手工建立的舊版單檔批次。"""
    db_path = tmp_path / "legacy.db"
    directory = history_directory_for_db(db_path)
    candidates = [
        _legacy_candidate(1, status="ready"),
        _legacy_candidate(2, status="ready"),
        _legacy_candidate(3, status="no_change", decision="no_change"),
        _legacy_candidate(4, status="invalid", error="reason_too_short"),
        _legacy_candidate(5),
    ]
    payload = _legacy_header(
        "legacy_single", started_at="2025-01-01 00:00:00", selected=5
    )
    payload["candidates"] = candidates
    _write_json(directory / "legacy_single.json", payload)
    return SmartReviewHistoryStore(db_path), "legacy_single"


def test_legacy_single_file_json_is_readable(legacy_single):
    """舊版單檔：摘要、分頁、計數、統計與單筆讀取都可用，且不暴露候選內容。"""
    store, run_id = legacy_single
    run = store.get_run(run_id)
    assert run is not None
    assert "candidates" not in run
    assert run["legacy_extra_field"] == {"keep": "me"}
    assert run["selected_count"] == 5

    total, rows = store.page_candidates(run_id, status="ready")
    assert total == 2
    assert [row.ordinal for row in rows] == [1, 2]
    assert rows[0].suggested_zh_tw == "文字 1（修訂）"
    assert rows[0].reason == "舊版理由"

    total, rows = store.page_candidates(run_id, offset=3, limit=5)
    assert (total, [row.ordinal for row in rows]) == (5, [4, 5])
    assert store.count_candidates(run_id, status="pending") == 1
    assert store.breakdown(run_id) == {
        "status": {"ready": 2, "no_change": 1, "invalid": 1, "pending": 1},
        "errors": [("reason_too_short", 1)],
    }
    assert store.get_candidate(run_id, 3)["status"] == "no_change"
    assert store.get_candidate(run_id, 99) is None
    assert [r["id"] for r in store.all_runs()] == [run_id]


def test_legacy_single_file_json_accepts_updates_and_keeps_unknown_fields(
    legacy_single,
):
    """舊版單檔被更新後仍是單檔，未知欄位與舊指標保留，其他候選不變。"""
    store, run_id = legacy_single
    assert store.update_candidate(run_id, 1, status="rejected") is True
    assert store.update_candidate(run_id, 99, status="rejected") is False
    store.record_results(run_id, [make_result(5)])
    store.mark_request_error(run_id, [4], error_code="timeout")
    assert (
        store.reconcile_applied(
            run_id,
            {2: {"entry_id": 1002, "batch": "b", "applied_at": "2026-01-01 00:00:00"}},
        )
        == 1
    )
    store.finish_run(
        run_id,
        processed_count=5,
        valid_count=3,
        invalid_count=0,
        request_error_count=1,
        metrics={"after": 1},
        status="partial",
    )

    payload = json.loads(store.path_for(run_id).read_text(encoding="utf-8"))
    assert "storage_format" not in payload
    assert payload["legacy_extra_field"] == {"keep": "me"}
    assert payload["status"] == "partial"
    assert not list(store.directory.glob("*.chunks"))
    statuses = {c["ordinal"]: c["status"] for c in raw_candidates(store, run_id, 5)}
    assert statuses == {
        1: "rejected",
        2: "applied",
        3: "no_change",
        4: "request_failed",
        5: "ready",
    }
    assert store.breakdown(run_id)["errors"] == [("timeout", 1)]


# ---------------------------------------------------------------------------
# 舊版分片 JSON（沒有分片清單、revision、signature）
# ---------------------------------------------------------------------------


@pytest.fixture
def legacy_chunked(tmp_path):
    """手工建立只有 chunk_count／storage_chunk_size 的舊版分片批次。"""
    db_path = tmp_path / "legacy-chunked.db"
    directory = history_directory_for_db(db_path)
    run_id = "legacy_chunked"
    manifest = _legacy_header(run_id, started_at="2025-02-01 00:00:00", selected=7)
    manifest.update(
        {
            "storage_format": "chunked_json_v1",
            "storage_chunk_size": 3,
            "chunk_count": 3,
        }
    )
    _write_json(directory / f"{run_id}.json", manifest)
    layout = [
        [("ready", None), ("ready", None), ("no_change", None)],
        [("invalid", "bad_json"), ("ready", None), ("pending", None)],
        [("ready", None)],
    ]
    ordinal = 0
    for index, statuses in enumerate(layout):
        chunk_candidates = []
        for status, error in statuses:
            ordinal += 1
            chunk_candidates.append(
                _legacy_candidate(ordinal, status=status, error=error)
            )
        chunk: dict[str, Any] = {"candidates": chunk_candidates}
        if index:
            chunk.update({"run_id": run_id, "chunk_index": index})
        _write_json(directory / f"{run_id}.chunks" / f"{index:06d}.json", chunk)
    return SmartReviewHistoryStore(db_path), run_id


def test_legacy_chunked_json_without_checkpoints_is_readable(legacy_chunked):
    """舊版分片：分片範圍由 storage_chunk_size 推算；統計由分片重建。"""
    store, run_id = legacy_chunked
    run = store.get_run(run_id)
    assert run["status_counts"] == {
        "ready": 4,
        "no_change": 1,
        "invalid": 1,
        "pending": 1,
    }
    assert store.count_candidates(run_id) == 7
    assert store.count_candidates(run_id, status="ready") == 4
    assert store.breakdown(run_id)["errors"] == [("bad_json", 1)]
    # 跨分片分頁（偏移落在第二個分片中間）。
    total, rows = store.page_candidates(run_id, status="ready", offset=1, limit=2)
    assert (total, [row.ordinal for row in rows]) == (4, [2, 5])
    total, rows = store.page_candidates(run_id, offset=5, limit=10)
    assert (total, [row.ordinal for row in rows]) == (7, [6, 7])
    assert store.get_candidate(run_id, 7)["status"] == "ready"
    assert_summary_consistent(store, run_id, 7)


def test_legacy_chunked_json_is_upgraded_in_place_on_first_write(legacy_chunked):
    """第一次寫入會補上 checkpoint；其餘分片檔案不被改寫。"""
    store, run_id = legacy_chunked
    untouched = store.directory / f"{run_id}.chunks" / "000002.json"
    untouched_bytes = untouched.read_bytes()

    assert store.update_candidate(run_id, 2, status="rejected") is True

    manifest = json.loads(store.path_for(run_id).read_text(encoding="utf-8"))
    assert manifest["legacy_extra_field"] == {"keep": "me"}
    assert manifest["status_counts"] == {
        "ready": 3,
        "rejected": 1,
        "no_change": 1,
        "invalid": 1,
        "pending": 1,
    }
    assert set(manifest["chunk_revisions"]) >= {"0"}
    assert untouched.read_bytes() == untouched_bytes
    # 舊候選沒有 ai_status：人工改狀態後 AI 累計值沿用 manifest，不從狀態倒推。
    assert_summary_consistent(store, run_id, 7, check_processed=False)
    assert store.get_run(run_id)["processed_count"] == 6

    store.record_results(run_id, [make_result(6)])
    assert store.get_candidate(run_id, 6)["status"] == "ready"
    assert store.mark_pending_cancelled(run_id) == 0
    assert_summary_consistent(store, run_id, 7, check_processed=False)


# ---------------------------------------------------------------------------
# 同一目錄與資料庫混用三種格式
# ---------------------------------------------------------------------------


def _insert_legacy_sqlite_run(db_path: Path, run_id: str, started_at: str, snapshots):
    """直接寫入最舊的 SQLite 批次與候選（每筆都是 ready，可套用）。"""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO smart_review_run (id, provider, model, scope_version, "
            "requested_limit, batch_size, scope_json, started_at, finished_at, status, "
            "selected_count, processed_count, valid_count, invalid_count, "
            "request_error_count, metrics) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                "gemini",
                "sqlite-model",
                "1.21.1",
                len(snapshots),
                len(snapshots),
                "{}",
                started_at,
                started_at,
                "completed",
                len(snapshots),
                len(snapshots),
                len(snapshots),
                0,
                0,
                "{}",
            ),
        )
        for snapshot in snapshots:
            connection.execute(
                "INSERT INTO smart_review_candidate (run_id, ordinal, entry_id, kind, "
                "mc_version, mod_id, entry_key, en_us, current_zh_tw, effective_source, "
                "review_status, effective_revision, decision, suggested_zh_tw, "
                "confidence, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    snapshot.ordinal,
                    snapshot.entry_id,
                    snapshot.kind,
                    snapshot.mc_version,
                    snapshot.mod_id,
                    snapshot.key,
                    snapshot.en_us,
                    snapshot.current_zh_tw,
                    snapshot.effective_source,
                    snapshot.review_status,
                    snapshot.effective_revision,
                    "suggest_revision",
                    f"{snapshot.current_zh_tw}（修訂）",
                    0.9,
                    "ready",
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _renumber(snapshots, offset: int):
    """取一段快照並把 ordinal 重編為 1..n（每個批次各自從 1 開始）。"""
    part = snapshots[offset : offset + 2]
    return [dataclasses.replace(item, ordinal=i) for i, item in enumerate(part, 1)]


@pytest.fixture
def mixed(tmp_path):
    """同一資料庫旁並存四個批次：SQLite、舊版 JSON、目前單檔、目前分片。"""
    db_path = tmp_path / "mixed.db"
    snapshots = seed_translation_db(db_path, 8)
    sql_snaps, legacy_snaps, single_snaps, chunk_snaps = (
        _renumber(snapshots, offset) for offset in (0, 2, 4, 6)
    )

    # 舊版 SQLite 批次。
    _insert_legacy_sqlite_run(db_path, "sqlite_run", "2026-01-01 00:00:00", sql_snaps)

    store = SmartReviewHistoryStore(db_path)
    # 舊版單檔 JSON（欄位取自真實快照，才能通過套用時的 CAS）。
    legacy_payload = _legacy_header(
        "legacy_json", started_at="2026-02-01 00:00:00", selected=2
    )
    legacy_payload["candidates"] = [
        _legacy_candidate(
            item.ordinal,
            status="ready",
            entry_id=item.entry_id,
            kind=item.kind,
            mc_version=item.mc_version,
            mod_id=item.mod_id,
            effective_source=item.effective_source,
            review_status=item.review_status,
            effective_revision=item.effective_revision,
            entry_key=item.key,
            en_us=item.en_us,
            current_zh_tw=item.current_zh_tw,
            suggested_zh_tw=f"{item.current_zh_tw}（修訂）",
        )
        for item in legacy_snaps
    ]
    _write_json(store.directory / "legacy_json.json", legacy_payload)

    runs = {"sqlite": "sqlite_run", "legacy": "legacy_json"}
    for key, snaps, chunk, started in (
        ("single", single_snaps, 1000, "2026-03-01 00:00:00"),
        ("chunked", chunk_snaps, 1, "2026-04-01 00:00:00"),
    ):
        _store, run_id = create_store_run(db_path, 0, chunk_size=chunk, snapshots=snaps)
        _store.record_results(
            run_id,
            [
                make_result(i.ordinal, suggestion=f"{i.current_zh_tw}（修訂）")
                for i in snaps
            ],
        )
        _set_started_at(_store, run_id, started)
        runs[key] = run_id
    return db_path, runs


def test_runs_of_all_formats_are_listed_in_one_stable_order(mixed):
    """四種格式的批次依開始時間新到舊合併，分頁不重複、不遺漏。"""
    db_path, runs = mixed
    db = TranslationDB(db_path)
    try:
        expected = [runs["chunked"], runs["single"], runs["legacy"], runs["sqlite"]]
        assert [r["id"] for r in db.list_smart_review_runs(limit=20)] == expected
        paged = [
            r["id"]
            for offset in (0, 1, 2, 3)
            for r in db.list_smart_review_runs(limit=1, offset=offset)
        ]
        assert paged == expected
        two_by_two = [
            r["id"]
            for offset in (0, 2)
            for r in db.list_smart_review_runs(limit=2, offset=offset)
        ]
        assert two_by_two == expected
        assert all("_history_mtime_ns" not in r for r in db.list_smart_review_runs())

        for key, run_id in runs.items():
            run = db.get_smart_review_run(run_id)
            assert run is not None, key
            assert run["selected_count"] == 2
            assert run["id"] == run_id
    finally:
        db.close()


def test_list_smart_review_runs_parses_only_the_needed_json_prefix(
    tmp_path, monkeypatch
):
    """歷史清單分頁不因舊批次數量增加而完整解析所有 manifest。"""
    db_path = tmp_path / "bounded-history.db"
    seed_translation_db(db_path, 1)
    store = SmartReviewHistoryStore(db_path)
    store.directory.mkdir(parents=True, exist_ok=True)
    for index in range(120):
        payload = _legacy_header(
            f"json-{index:03d}",
            started_at=f"2026-01-{index // 24 + 1:02d} {index % 24:02d}:00:00",
            selected=0,
        )
        payload["candidates"] = []
        (store.directory / f"json-{index:03d}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    parsed = []
    original = HistoryQuery._summary_for_path

    def count_summaries(self, path, stamp, *, include_sort_metadata):
        parsed.append(path.name)
        return original(
            self,
            path,
            stamp,
            include_sort_metadata=include_sort_metadata,
        )

    monkeypatch.setattr(HistoryQuery, "_summary_for_path", count_summaries)
    db = TranslationDB(db_path)
    try:
        page = db.list_smart_review_runs(limit=2)
    finally:
        db.close()

    assert len(page) == 2
    assert len(parsed) == 3


def test_candidates_of_all_formats_read_and_count_the_same_way(mixed):
    """每種格式的候選分頁、計數與統計對同樣的資料給出同樣的結果。"""
    db_path, runs = mixed
    db = TranslationDB(db_path)
    try:
        for key, run_id in runs.items():
            rows = db.list_smart_review_candidates(run_id)
            assert [row.ordinal for row in rows] == [1, 2], key
            assert {row.status for row in rows} == {"ready"}, key
            assert all(
                row.suggested_zh_tw == f"{row.current_zh_tw}（修訂）" for row in rows
            ), key
            assert db.count_smart_review_candidates_for_run(run_id) == 2, key
            assert db.count_smart_review_candidates_for_run(run_id, status="ready") == 2
            assert (
                db.count_smart_review_candidates_for_run(run_id, status="applied") == 0
            )
            assert db.smart_review_run_breakdown(run_id)["status"] == {"ready": 2}, key
            total, page = db.load_smart_review_candidate_page(run_id, limit=1, offset=1)
            assert (total, [r.ordinal for r in page]) == (2, [2]), key
            # 超出範圍的偏移會被夾回最後一筆。
            total, page = db.load_smart_review_candidate_page(
                run_id, limit=1, offset=50
            )
            assert [r.ordinal for r in page] in ([2], []), key
    finally:
        db.close()


def test_apply_works_for_every_format_and_marks_follow_the_newest_json_run(mixed):
    """四種格式都能套用候選；條目標籤只跟隨最新的 JSON 批次。"""
    db_path, runs = mixed
    db = TranslationDB(db_path)
    try:
        for key, run_id in runs.items():
            assert db.apply_smart_review_candidate(run_id, 1) == "applied", key
            assert db.apply_smart_review_candidate(run_id, 1) == "not_ready", key
            rows = db.list_smart_review_candidates(run_id)
            assert [row.status for row in rows] == ["applied", "ready"], key
            assert db.smart_review_run_breakdown(run_id)["status"] == {
                "applied": 1,
                "ready": 1,
            }, key

        newest = db.list_smart_review_candidates(runs["chunked"])
        applied_entry = newest[0].entry_id
        ready_entry = newest[1].entry_id
        marks = db.smart_review_marks([applied_entry, ready_entry])
        assert marks[applied_entry].status == "applied"
        assert marks[applied_entry].run_id == runs["chunked"]
        assert ready_entry not in marks  # ready 不標記
        assert db.smart_review_entry_ids("applied") == (applied_entry,)
    finally:
        db.close()


def test_corrupt_neighbour_files_do_not_hide_valid_runs(mixed):
    """目錄內有損壞或格式不符的檔案時，其他批次的列表、分頁與標籤不受影響。"""
    db_path, runs = mixed
    store = SmartReviewHistoryStore(db_path)
    (store.directory / "broken.json").write_text("{not json", encoding="utf-8")
    (store.directory / "listy.json").write_text("[]", encoding="utf-8")
    (store.directory / "no_id.json").write_text(
        '{"started_at": "2099-01-01"}', encoding="utf-8"
    )

    listed = [run["id"] for run in store.all_runs()]
    assert listed == [runs["chunked"], runs["single"], runs["legacy"]]

    # 最新批次的分片損壞：標籤改用次新的完整批次；其餘批次仍可讀。
    chunk_file = store.directory / f"{runs['chunked']}.chunks" / "000000.json"
    chunk_file.write_text("{broken", encoding="utf-8")
    run_id, marks = store.latest_run_marks()
    assert run_id == runs["single"]
    assert len(marks) == 2
    assert store.page_candidates(runs["chunked"], limit=None) == (0, [])
    assert store.count_candidates(runs["single"]) == 2


# ---------------------------------------------------------------------------
# 單檔與分片格式的行為等價
# ---------------------------------------------------------------------------


def _scripted_operations(store: SmartReviewHistoryStore, run_id: str) -> None:
    """對批次執行一組會產生各種狀態的操作。"""
    results = []
    for ordinal in range(1, 18):
        if ordinal % 6 == 0:
            results.append(make_result(ordinal, validation_error="reason_too_short"))
        elif ordinal % 5 == 0:
            results.append(make_result(ordinal, decision="no_change"))
        elif ordinal % 7 == 0:
            results.append(make_result(ordinal, decision="needs_context"))
        else:
            results.append(make_result(ordinal))
    store.record_results(run_id, results)
    store.mark_request_error(run_id, [18, 19], error_code="timeout")
    store.update_candidate(run_id, 1, status="rejected")
    store.update_candidate(run_id, 2, status="stale", validation_error="snapshot_stale")
    store.reconcile_applied(
        run_id,
        {
            3: {"entry_id": 3, "batch": "b3", "applied_at": "2026-01-01 00:00:00"},
            9: {"entry_id": 9, "batch": "b9", "applied_at": "2026-01-01 00:00:00"},
        },
    )
    assert store.mark_pending_cancelled(run_id) == 4  # 20..23


def _observe(store: SmartReviewHistoryStore, run_id: str) -> dict[str, Any]:
    """蒐集公開讀取 API 的結果，用來比較兩種儲存格式。"""
    statuses = (None, "ready", "no_change", "needs_context", "invalid", "applied")
    statuses += ("request_failed", "cancelled", "rejected", "stale", "pending")
    pages = {}
    for status, offset, limit in itertools.product(
        statuses, (0, 1, 4, 5, 11, 22, 40), (None, 1, 3, 6)
    ):
        total, rows = store.page_candidates(
            run_id, status=status, offset=offset, limit=limit
        )
        pages[(status, offset, limit)] = (
            total,
            [
                (r.ordinal, r.status, r.validation_error, r.applied_batch, r.decision)
                for r in rows
            ],
        )
    run = store.get_run(run_id)
    return {
        "pages": pages,
        "breakdown": store.breakdown(run_id),
        "counts": {s: store.count_candidates(run_id, status=s) for s in statuses},
        "processed": [
            run[k]
            for k in (
                "processed_count",
                "valid_count",
                "invalid_count",
                "request_error_count",
            )
        ],
    }


def test_single_and_chunked_layouts_behave_identically(tmp_path):
    """同樣的操作序列下，單檔與分片格式對所有公開讀取 API 給出相同結果。"""
    total = 23
    observed = []
    for name, chunk in (("single", 1000), ("chunked", 5)):
        store, run_id = create_store_run(
            tmp_path / f"{name}.db", total, chunk_size=chunk
        )
        _scripted_operations(store, run_id)
        assert_summary_consistent(store, run_id, total)
        observed.append(_observe(store, run_id))
    status = observed[0]["breakdown"]["status"]
    assert status["applied"] == 2
    assert status["cancelled"] == 4
    assert status["request_failed"] == 2
    assert observed[0] == observed[1]
