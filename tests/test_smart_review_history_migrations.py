"""智慧校對歷史：schema migration registry、status sidecar、一致性檢查與修復。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.smart_review_history_support import (
    create_store_run,
    make_result,
)
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)
from translation_tool.translation_db.smart_review_history_manifest import (
    ChunkManifestRepository,
)
from translation_tool.translation_db.smart_review_history_migrations import (
    MIGRATIONS,
    QUARANTINE_DIRECTORY_NAME,
    IssueCode,
    MigrationError,
    check_history_directory,
    latest_known_schema_version,
    migrate_payload,
    repair_history_directory,
    upgrade_v1_to_v2,
)

TOTAL = 10
CHUNK = 4


@pytest.fixture
def chunked(tmp_path):
    """三個分片的批次，已寫入幾筆結果；回傳 (store, run_id)。"""
    store, run_id = create_store_run(tmp_path / "review.db", TOTAL, chunk_size=CHUNK)
    store.record_results(
        run_id,
        [
            make_result(1),
            make_result(5, decision="no_change"),
            make_result(9, validation_error="bad_reason"),
        ],
    )
    return store, run_id


def _files(directory: Path) -> dict[str, bytes]:
    """歷史目錄內所有檔案的位元組（排除鎖檔），用來證明 dry-run 不改檔。"""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != ".smart_review.lock"
    }


def _codes(directory: Path) -> set[IssueCode]:
    return {issue.code for issue in check_history_directory(directory)}


def _sidecar(store: SmartReviewHistoryStore, run_id: str, index: int) -> Path:
    return store.repository.status_sidecar_path_for(run_id, index)


def _chunk(store: SmartReviewHistoryStore, run_id: str, index: int) -> Path:
    return store.repository.chunk_path_for(run_id, index)


# ---------------------------------------------------------------------------
# migration registry
# ---------------------------------------------------------------------------


def test_migrate_to_current_version_is_a_copy_without_changes():
    payload = {"schema_version": 1, "id": "run"}
    migrated = migrate_payload(payload)
    assert migrated == payload
    assert migrated is not payload


def test_missing_schema_version_is_treated_as_v1():
    migrated = migrate_payload({"id": "run"}, target=2)
    assert migrated["schema_version"] == 2
    assert migrated["projection"] == {"revision": 0, "state": "projected"}


def test_v1_to_v2_is_idempotent_and_keeps_existing_fields():
    payload = {
        "schema_version": 1,
        "id": "run",
        "projection": {"revision": 7, "state": "reconciled"},
    }
    once = migrate_payload(payload, target=2)
    twice = migrate_payload(once, target=2)
    assert once == twice
    assert once["projection"] == {"revision": 7, "state": "reconciled"}
    assert upgrade_v1_to_v2(dict(once)) == once
    assert payload["schema_version"] == 1, "輸入不可被改動"


def test_registry_rejects_unknown_targets_and_broken_steps(monkeypatch):
    assert latest_known_schema_version() == 2
    with pytest.raises(MigrationError):
        migrate_payload({"schema_version": 1}, target=3)
    with pytest.raises(MigrationError):
        migrate_payload({"schema_version": "x"})
    monkeypatch.setitem(MIGRATIONS, 1, lambda payload: payload)
    with pytest.raises(MigrationError):
        migrate_payload({"schema_version": 1}, target=2)


# ---------------------------------------------------------------------------
# status sidecar
# ---------------------------------------------------------------------------


def test_create_run_publishes_a_sidecar_per_chunk(tmp_path):
    store, run_id = create_store_run(tmp_path / "review.db", TOTAL, chunk_size=CHUNK)
    for index in range(3):
        payload = json.loads(_sidecar(store, run_id, index).read_text("utf-8"))
        assert payload["sidecar_version"] == 1
        assert payload["schema_version"] == 1
        manifest = store.repository.read_unlocked(run_id)
        assert payload["chunk_revision"] == manifest["chunk_revisions"][str(index)]
        assert {row[2] for row in payload["rows"]} == {"pending"}
    assert check_history_directory(store.directory) == []


def test_sidecar_follows_writes_and_serves_latest_marks(chunked, monkeypatch):
    store, run_id = chunked
    payload = json.loads(_sidecar(store, run_id, 2).read_text("utf-8"))
    assert [row[:3] for row in payload["rows"]] == [
        [9, payload["rows"][0][1], "invalid"],
        [10, payload["rows"][1][1], "pending"],
    ]
    assert payload["rows"][0][3] == "bad_reason"
    assert payload["projection_revision"] == 0

    SmartReviewHistoryStore.clear_caches()

    def no_chunk_reads(*_args, **_kwargs):
        raise AssertionError("sidecar 最新時不應讀取候選分片")

    monkeypatch.setattr(ChunkManifestRepository, "read_chunk_unlocked", no_chunk_reads)
    latest, marks = store.latest_run_marks()
    assert latest == run_id
    assert len(marks) == TOTAL
    assert sorted(status for status, _ in marks.values()).count("pending") == 7


def test_update_candidate_rewrites_only_that_chunks_sidecar(chunked):
    store, run_id = chunked
    before = {i: _sidecar(store, run_id, i).read_bytes() for i in range(3)}
    assert store.update_candidate(run_id, 6, status="rejected")
    after = {i: _sidecar(store, run_id, i).read_bytes() for i in range(3)}
    assert after[0] == before[0]
    assert after[1] != before[1]
    assert after[2] == before[2]
    assert json.loads(after[1])["projection_revision"] >= 0
    assert check_history_directory(store.directory) == []


def test_history_batch_publishes_each_affected_chunk_once(chunked, monkeypatch):
    """批量狀態更新只替換受影響分片，manifest 也只發布一次。"""
    store, run_id = chunked
    writes: list[str] = []
    original_write = ChunkManifestRepository._write_json_path_unlocked

    def counted_write(repository, path, payload):
        writes.append(path.name)
        return original_write(repository, path, payload)

    monkeypatch.setattr(
        ChunkManifestRepository, "_write_json_path_unlocked", counted_write
    )
    with store.batch(run_id) as batch:
        assert batch.update_candidate(1, status="rejected")
        assert batch.update_candidate(2, status="rejected")
        assert batch.update_candidate(6, status="rejected")

    assert writes.count(f"{run_id}.json") == 1
    assert writes.count("000000.json") == 1
    assert writes.count("000001.json") == 1
    assert writes.count("000000.status") == 1
    assert writes.count("000001.status") == 1
    assert store.breakdown(run_id)["status"] == {
        "rejected": 3,
        "pending": 5,
        "no_change": 1,
        "invalid": 1,
    }


def test_missing_stale_or_corrupt_sidecar_falls_back_to_chunks_without_writing(
    chunked,
):
    store, run_id = chunked
    SmartReviewHistoryStore.clear_caches()
    expected = store.latest_run_marks()

    old_bytes = _sidecar(store, run_id, 0).read_bytes()
    store.record_results(run_id, [make_result(2)])
    _sidecar(store, run_id, 0).write_bytes(old_bytes)  # 過期：revision 對不上
    _sidecar(store, run_id, 1).unlink()  # 遺失
    _sidecar(store, run_id, 2).write_text("{broken", encoding="utf-8")  # 損壞
    snapshot = _files(store.directory)

    SmartReviewHistoryStore.clear_caches()
    latest, marks = store.latest_run_marks()
    assert latest == expected[0]
    statuses = sorted(status for status, _ in marks.values())
    assert statuses.count("ready") == 2  # ordinal 1 與 2
    assert _files(store.directory) == snapshot, "讀取不可改寫檔案"


def test_chunk_changed_outside_the_store_is_not_hidden_by_sidecar(chunked):
    store, run_id = chunked
    _chunk(store, run_id, 0).write_text("{broken", encoding="utf-8")
    SmartReviewHistoryStore.clear_caches()
    assert store.latest_run_marks() == (None, {})


def test_legacy_chunked_run_without_sidecars_is_still_readable(chunked):
    store, run_id = chunked
    for index in range(3):
        _sidecar(store, run_id, index).unlink()
    SmartReviewHistoryStore.clear_caches()
    latest, marks = store.latest_run_marks()
    assert latest == run_id
    assert len(marks) == TOTAL
    assert store.count_candidates(run_id, status="ready") == 1
    store.record_results(run_id, [make_result(3)])
    assert _sidecar(store, run_id, 0).is_file(), "下次寫入該分片時補回 sidecar"


# ---------------------------------------------------------------------------
# 檢查器
# ---------------------------------------------------------------------------


def test_check_is_clean_for_chunked_single_and_empty_directories(chunked, tmp_path):
    store, _run_id = chunked
    create_store_run(tmp_path / "review.db", 3, chunk_size=CHUNK)  # 單檔批次
    assert check_history_directory(store.directory) == []
    assert check_history_directory(tmp_path / "does-not-exist") == []


def test_check_reports_missing_and_corrupt_chunks(chunked):
    store, run_id = chunked
    _chunk(store, run_id, 0).unlink()
    _chunk(store, run_id, 1).write_text("{broken", encoding="utf-8")
    issues = check_history_directory(store.directory)
    by_code = {issue.code: issue for issue in issues}
    assert by_code[IssueCode.CHUNK_MISSING].chunk_index == 0
    assert by_code[IssueCode.CHUNK_CORRUPT].chunk_index == 1
    assert by_code[IssueCode.CHUNK_MISSING].severity == "error"
    assert not by_code[IssueCode.CHUNK_MISSING].repairable
    assert all(issue.run_id == run_id for issue in issues)


def test_check_reports_chunk_count_and_summary_mismatch(chunked):
    store, run_id = chunked
    path = _chunk(store, run_id, 2)
    payload = json.loads(path.read_text("utf-8"))
    payload["candidates"].pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert IssueCode.CHUNK_COUNT_MISMATCH in _codes(store.directory)

    path.write_text(json.dumps({**payload, "candidates": "x"}), encoding="utf-8")
    assert IssueCode.CHUNK_CORRUPT in _codes(store.directory)


def test_check_reports_manifest_summary_that_disagrees_with_chunks(chunked):
    store, run_id = chunked
    manifest_path = store.path_for(run_id)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["status_counts"] = {"pending": TOTAL}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    issues = [
        issue
        for issue in check_history_directory(store.directory)
        if issue.code == IssueCode.SUMMARY_MISMATCH
    ]
    assert issues
    assert issues[0].repairable


def test_check_reports_corrupt_or_foreign_manifests(chunked):
    store, run_id = chunked
    (store.directory / "broken.json").write_text("{not json", encoding="utf-8")
    (store.directory / "listy.json").write_text("[]", encoding="utf-8")
    manifest_path = store.path_for(run_id)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["schema_version"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    issues = check_history_directory(store.directory)
    corrupt = {i.run_id for i in issues if i.code == IssueCode.MANIFEST_CORRUPT}
    assert corrupt == {"broken", "listy"}
    assert IssueCode.SCHEMA_UNSUPPORTED in {i.code for i in issues}


def test_check_reports_orphans_and_stale_temp_files(chunked):
    store, run_id = chunked
    chunk_directory = store.directory / f"{run_id}.chunks"
    (chunk_directory / "000009.json").write_text("{}", encoding="utf-8")
    (chunk_directory / ".000001.json.abc.tmp").write_text("x", encoding="utf-8")
    ghost = store.directory / "ghost.chunks"
    ghost.mkdir()
    (ghost / "000000.json").write_text("{}", encoding="utf-8")
    (store.directory / ".other.chunks.abc.tmp").mkdir()
    codes = [issue.code for issue in check_history_directory(store.directory)]
    assert codes.count(IssueCode.ORPHAN_CHUNK) == 1
    assert codes.count(IssueCode.ORPHAN_CHUNK_DIRECTORY) == 1
    assert codes.count(IssueCode.STALE_TEMP) == 2


def test_check_reports_every_sidecar_problem(chunked):
    store, run_id = chunked
    old_bytes = _sidecar(store, run_id, 0).read_bytes()
    store.record_results(run_id, [make_result(2)])
    _sidecar(store, run_id, 0).write_bytes(old_bytes)
    _sidecar(store, run_id, 1).unlink()
    _sidecar(store, run_id, 2).write_text("{broken", encoding="utf-8")
    codes = {i.code: i for i in check_history_directory(store.directory)}
    assert codes[IssueCode.SIDECAR_STALE].chunk_index == 0
    assert codes[IssueCode.SIDECAR_MISSING].chunk_index == 1
    assert codes[IssueCode.SIDECAR_CORRUPT].chunk_index == 2

    repair_history_directory(store.directory)
    path = _sidecar(store, run_id, 1)
    payload = json.loads(path.read_text("utf-8"))
    payload["rows"][0][2] = "applied"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert [i.code for i in check_history_directory(store.directory)] == [
        IssueCode.SIDECAR_MISMATCH
    ]


def test_check_single_file_summary_mismatch(tmp_path):
    store, run_id = create_store_run(tmp_path / "review.db", 3, chunk_size=CHUNK)
    path = store.path_for(run_id)
    payload = json.loads(path.read_text("utf-8"))
    payload["status_counts"] = {"ready": 3}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _codes(store.directory) == {IssueCode.SUMMARY_MISMATCH}
    assert repair_history_directory(store.directory).unresolved == []
    assert check_history_directory(store.directory) == []


# ---------------------------------------------------------------------------
# 修復
# ---------------------------------------------------------------------------


def _damage(store: SmartReviewHistoryStore, run_id: str) -> None:
    """製造只靠重建與隔離就能修好的各種問題。"""
    manifest_path = store.path_for(run_id)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["status_counts"] = {"pending": TOTAL}
    manifest["error_counts"] = {}
    manifest["chunk_signatures"] = {}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _sidecar(store, run_id, 1).unlink()
    _sidecar(store, run_id, 2).write_text("{broken", encoding="utf-8")
    (store.directory / f"{run_id}.chunks" / "000009.json").write_text(
        "{}", encoding="utf-8"
    )
    ghost = store.directory / "ghost.chunks"
    ghost.mkdir()
    (ghost / "000000.json").write_text("{}", encoding="utf-8")


def test_dry_run_reports_actions_but_changes_nothing(chunked):
    store, run_id = chunked
    _damage(store, run_id)
    before = _files(store.directory)
    report = repair_history_directory(store.directory, dry_run=True)
    assert report.dry_run
    assert {a.kind for a in report.actions} == {
        "quarantine",
        "rebuild_manifest_summary",
    }
    assert _files(store.directory) == before
    assert not (store.directory / QUARANTINE_DIRECTORY_NAME).exists()


def test_repair_rebuilds_derived_data_and_quarantines_orphans(chunked):
    store, run_id = chunked
    _damage(store, run_id)
    report = repair_history_directory(store.directory)
    assert report.unresolved == []
    assert check_history_directory(store.directory) == []

    quarantine = store.directory / (report.quarantine_directory or "missing")
    assert (quarantine / f"{run_id}.chunks" / "000009.json").is_file()
    assert (quarantine / "ghost.chunks" / "000000.json").is_file()
    assert not (store.directory / "ghost.chunks").exists()

    SmartReviewHistoryStore.clear_caches()
    assert store.breakdown(run_id)["status"] == {
        "ready": 1,
        "no_change": 1,
        "invalid": 1,
        "pending": 7,
    }
    assert store.get_run(run_id)["status_counts"]["pending"] == 7


def test_repair_is_idempotent_and_leaves_candidates_untouched(chunked):
    store, run_id = chunked
    candidates_before = [_chunk(store, run_id, i).read_bytes() for i in range(3)]
    _damage(store, run_id)
    repair_history_directory(store.directory)
    settled = _files(store.directory)
    again = repair_history_directory(store.directory)
    assert again.actions == []
    assert _files(store.directory) == settled
    assert [
        _chunk(store, run_id, i).read_bytes() for i in range(3)
    ] == candidates_before


def test_repair_reports_what_it_cannot_rebuild(chunked):
    store, run_id = chunked
    _chunk(store, run_id, 1).unlink()
    (store.directory / f"{run_id}.chunks" / "000009.json").write_text(
        "{}", encoding="utf-8"
    )
    report = repair_history_directory(store.directory)
    assert {i.code for i in report.unresolved} == {IssueCode.CHUNK_MISSING}
    assert [a.kind for a in report.actions] == ["quarantine"]
    assert not _chunk(store, run_id, 1).exists(), "不可憑空補出缺少的分片"
