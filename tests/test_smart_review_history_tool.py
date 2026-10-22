"""tools/smart_review_history_tool.py：check／repair／backup／restore／prune。"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.smart_review_history_support import create_store_run, make_result
from tools.smart_review_history_tool import main, plan_prune
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)
from translation_tool.translation_db.smart_review_history_migrations import (
    check_history_directory,
)

CHUNK = 4


@pytest.fixture
def history(tmp_path):
    """含一個分片批次與一個單檔批次的歷史目錄。"""
    db_path = tmp_path / "review.db"
    store, chunked_id = create_store_run(db_path, 10, chunk_size=CHUNK)
    store.record_results(chunked_id, [make_result(1), make_result(6)])
    _store, single_id = create_store_run(db_path, 3, chunk_size=CHUNK)
    for run_id in (chunked_id, single_id):
        store.finish_run(
            run_id,
            processed_count=0,
            valid_count=0,
            invalid_count=0,
            request_error_count=0,
            metrics=None,
            status="completed",
        )
    return store, chunked_id, single_id


def _run(capsys, store, *args: str) -> tuple[int, str]:
    code = main(["--dir", str(store.directory), *args])
    return code, capsys.readouterr().out


def _files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != ".smart_review.lock"
    }


def _set_started_at(store, run_id: str, when: datetime) -> None:
    path = store.path_for(run_id)
    payload = json.loads(path.read_text("utf-8"))
    payload["started_at"] = when.strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_check_exit_codes_and_json_output(history, capsys):
    store, chunked_id, _single_id = history
    code, out = _run(capsys, store, "check")
    assert code == 0
    assert "沒有發現問題" in out

    store.repository.chunk_path_for(chunked_id, 1).unlink()
    code, out = _run(capsys, store, "--json", "check")
    payload = json.loads(out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["summary"] == {"chunk_missing": 1}
    assert payload["issues"][0]["chunk_index"] == 1


def test_db_option_resolves_the_history_directory(history, capsys, tmp_path):
    store, _chunked_id, _single_id = history
    assert main(["--db", str(tmp_path / "review.db"), "check"]) == 0
    assert str(store.directory) in capsys.readouterr().out


def test_repair_dry_run_does_not_touch_files_and_repair_makes_check_pass(
    history, capsys
):
    store, chunked_id, _single_id = history
    store.repository.status_sidecar_path_for(chunked_id, 0).unlink()
    orphan = store.directory / f"{chunked_id}.chunks" / "000042.json"
    orphan.write_text("{}", encoding="utf-8")
    before = _files(store.directory)

    code, out = _run(capsys, store, "repair", "--dry-run")
    assert code == 0
    assert "dry-run" in out
    assert _files(store.directory) == before
    assert _run(capsys, store, "check")[0] == 1

    code, _out = _run(capsys, store, "repair")
    assert code == 0
    assert not orphan.exists()
    assert list((store.directory / "quarantine").rglob("000042.json"))
    assert _run(capsys, store, "check")[0] == 0


def test_repair_exit_code_is_nonzero_when_something_cannot_be_rebuilt(history, capsys):
    store, chunked_id, _single_id = history
    store.repository.chunk_path_for(chunked_id, 0).write_text("{x", encoding="utf-8")
    code, out = _run(capsys, store, "repair")
    assert code == 1
    assert "未解決" in out


def test_backup_restore_round_trip(history, capsys, tmp_path):
    store, chunked_id, single_id = history
    archive = tmp_path / "backups" / "history.zip"
    assert _run(capsys, store, "backup", "--output", str(archive))[0] == 0
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert f"{chunked_id}.json" in names
    assert f"{chunked_id}.chunks/000002.json" in names
    assert f"{chunked_id}.chunks/000002.status" in names
    assert not any(name.endswith(".lock") for name in names)
    candidates_before = {
        run: store.page_candidates(run, limit=None)[1]
        for run in (chunked_id, single_id)
    }

    # 破壞原目錄後還原
    store.repository.chunk_path_for(chunked_id, 0).write_text("{x", encoding="utf-8")
    (store.directory / f"{single_id}.json").unlink()
    code, out = _run(capsys, store, "restore", "--input", str(archive), "--force")
    assert code == 0
    assert "原本的目錄保留在" in out
    assert check_history_directory(store.directory) == []
    SmartReviewHistoryStore.clear_caches()
    for run, expected in candidates_before.items():
        assert store.page_candidates(run, limit=None)[1] == expected
    kept = [p for p in store.directory.parent.iterdir() if "pre-restore" in p.name]
    assert len(kept) == 1, "被取代的舊目錄必須保留"


def test_backup_refuses_existing_output_and_output_inside_history(
    history, capsys, tmp_path
):
    store, _chunked_id, _single_id = history
    existing = tmp_path / "exists.zip"
    existing.write_bytes(b"keep")
    assert _run(capsys, store, "backup", "--output", str(existing))[0] == 2
    assert existing.read_bytes() == b"keep"
    inside = store.directory / "inside.zip"
    assert _run(capsys, store, "backup", "--output", str(inside))[0] == 2
    assert not inside.exists()


def test_restore_requires_force_for_existing_data_and_rejects_unsafe_archives(
    history, capsys, tmp_path
):
    store, _chunked_id, _single_id = history
    archive = tmp_path / "ok.zip"
    assert _run(capsys, store, "backup", "--output", str(archive))[0] == 0
    before = _files(store.directory)
    assert _run(capsys, store, "restore", "--input", str(archive))[0] == 2
    assert _files(store.directory) == before

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("../escape.json", "{}")
    assert _run(capsys, store, "restore", "--input", str(evil), "--force")[0] == 2
    assert _files(store.directory) == before
    assert not (store.directory.parent / "escape.json").exists()


def test_restore_into_missing_directory(history, capsys, tmp_path):
    store, chunked_id, _single_id = history
    archive = tmp_path / "ok.zip"
    assert _run(capsys, store, "backup", "--output", str(archive))[0] == 0
    target = tmp_path / "fresh" / "other.smart_review"
    assert main(["--dir", str(target), "restore", "--input", str(archive)]) == 0
    capsys.readouterr()
    assert (target / f"{chunked_id}.json").is_file()
    assert check_history_directory(target) == []


def test_prune_is_dry_run_by_default_and_apply_deletes(history, capsys):
    store, chunked_id, single_id = history
    now = datetime.now(UTC)
    _set_started_at(store, chunked_id, now - timedelta(days=200))
    _set_started_at(store, single_id, now - timedelta(days=1))
    before = _files(store.directory)

    code, out = _run(
        capsys, store, "prune", "--retention-days", "90", "--keep-latest", "1"
    )
    assert code == 0
    assert chunked_id in out
    assert single_id not in out
    assert _files(store.directory) == before

    code, out = _run(
        capsys,
        store,
        "prune",
        "--retention-days",
        "90",
        "--keep-latest",
        "1",
        "--apply",
    )
    assert code == 0
    assert not store.path_for(chunked_id).exists()
    assert not (store.directory / f"{chunked_id}.chunks").exists()
    assert store.path_for(single_id).is_file()
    assert check_history_directory(store.directory) == []


def test_prune_keeps_latest_n_and_running_and_unparseable_runs(history):
    store, chunked_id, single_id = history
    now = datetime.now(UTC)
    old = now - timedelta(days=400)
    _set_started_at(store, chunked_id, old)
    _set_started_at(store, single_id, old + timedelta(days=1))

    both = plan_prune(store.directory, retention_days=30, keep_latest=0, now=now)
    assert {c.run_id for c in both} == {chunked_id, single_id}
    assert [
        c.run_id
        for c in plan_prune(store.directory, retention_days=30, keep_latest=1, now=now)
    ] == [chunked_id]
    assert plan_prune(store.directory, retention_days=500, keep_latest=0, now=now) == []

    path = store.path_for(chunked_id)
    payload = json.loads(path.read_text("utf-8"))
    payload["status"] = "running"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert [
        c.run_id
        for c in plan_prune(store.directory, retention_days=30, keep_latest=0, now=now)
    ] == [single_id]

    payload["status"] = "completed"
    payload["started_at"] = "not-a-date"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert chunked_id not in {
        c.run_id
        for c in plan_prune(store.directory, retention_days=30, keep_latest=0, now=now)
    }


def test_prune_rejects_negative_arguments(history, capsys):
    store, _chunked_id, _single_id = history
    assert _run(capsys, store, "prune", "--retention-days", "-1")[0] == 2
