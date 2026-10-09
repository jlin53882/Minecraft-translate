from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.core import kubejs_translator_state


def test_commit_staging_run_copies_when_windows_blocks_directory_rename(
    tmp_path: Path, monkeypatch
) -> None:
    staging = tmp_path / ".pipeline" / "staging" / "abc"
    committed = tmp_path / ".pipeline" / "runs" / "abc"
    staging.mkdir(parents=True)
    (staging / "data.json").write_text('{"key": "value"}', encoding="utf-8")

    def blocked_rename(*_args, **_kwargs):
        raise PermissionError("simulated Windows directory lock")

    monkeypatch.setattr(kubejs_translator_state.os, "replace", blocked_rename)

    used_copy = kubejs_translator_state.commit_staging_run(staging, committed)

    assert used_copy is True
    assert (committed / "data.json").read_text(encoding="utf-8") == '{"key": "value"}'
    assert not staging.exists()


def test_failed_copy_fallback_leaves_staging_available(
    tmp_path: Path, monkeypatch
) -> None:
    staging = tmp_path / ".pipeline" / "staging" / "abc"
    committed = tmp_path / ".pipeline" / "runs" / "abc"
    staging.mkdir(parents=True)
    (staging / "data.json").write_text('{"key": "value"}', encoding="utf-8")

    def blocked_rename(*_args, **_kwargs):
        raise PermissionError("simulated Windows directory lock")

    def failed_copy(*_args, **_kwargs):
        raise OSError("simulated disk or permission failure")

    monkeypatch.setattr(kubejs_translator_state.os, "replace", blocked_rename)
    monkeypatch.setattr(kubejs_translator_state.shutil, "copytree", failed_copy)

    with pytest.raises(OSError, match="disk or permission"):
        kubejs_translator_state.commit_staging_run(staging, committed)

    assert (staging / "data.json").read_text(encoding="utf-8") == '{"key": "value"}'
    assert not committed.exists()


def test_sync_managed_tree_reports_and_preserves_legacy_unowned_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "snapshot"
    destination = tmp_path / "public"
    source.mkdir()
    destination.mkdir()
    old = destination / "old.json"
    old.write_text('{"manual":"keep"}', encoding="utf-8")
    details: list[dict[str, str]] = []

    owned, conflicts, removed = kubejs_translator_state.sync_managed_tree(
        source, destination, {}, conflict_details=details
    )

    assert owned == {}
    assert conflicts == ["old.json"]
    assert removed == 0
    assert old.read_text(encoding="utf-8") == '{"manual":"keep"}'
    assert details == [{"path": "old.json", "kind": "legacy_unowned_stale_file"}]


def test_sync_managed_tree_preserves_unowned_same_path_collision(
    tmp_path: Path,
) -> None:
    source = tmp_path / "snapshot"
    destination = tmp_path / "public"
    source.mkdir()
    destination.mkdir()
    (source / "current.json").write_text('{"new":"snapshot"}', encoding="utf-8")
    target = destination / "current.json"
    target.write_text('{"manual":"keep"}', encoding="utf-8")
    details: list[dict[str, str]] = []

    kubejs_translator_state.sync_managed_tree(
        source, destination, {}, conflict_details=details
    )

    assert target.read_text(encoding="utf-8") == '{"manual":"keep"}'
    assert details == [{"path": "current.json", "kind": "unowned_file_collision"}]


def test_sync_managed_tree_normalizes_json_formatting_without_conflict(
    tmp_path: Path,
) -> None:
    source = tmp_path / "snapshot"
    destination = tmp_path / "public"
    source.mkdir()
    destination.mkdir()
    source_file = source / "current.json"
    source_file.write_text('{"key":"same"}', encoding="utf-8")
    target = destination / "current.json"
    target.write_text('{\n  "key": "same"\n}', encoding="utf-8")
    old_hash = kubejs_translator_state.sha256_file(source_file)
    details: list[dict[str, str]] = []

    owned, conflicts, _ = kubejs_translator_state.sync_managed_tree(
        source, destination, {"current.json": old_hash}, conflict_details=details
    )

    assert target.read_bytes() == source_file.read_bytes()
    assert owned == {"current.json": old_hash}
    assert conflicts == []
    assert details == []


def test_corrupt_ownership_manifest_fails_safe(tmp_path: Path) -> None:
    manifest = tmp_path / ".kubejs-clean-manifest.json"
    manifest.write_text(json.dumps({"version": 1, "files": "bad"}), encoding="utf-8")

    with pytest.raises(TypeError, match="格式無效"):
        kubejs_translator_state.load_owned_files(manifest)


def test_atomic_manifest_write_retries_transient_windows_lock(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "current.json"
    target.write_text('{"old":true}', encoding="utf-8")
    real_replace = kubejs_translator_state.os.replace
    attempts = 0

    def transient_lock(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("simulated transient Windows file lock")
        return real_replace(source, destination)

    monkeypatch.setattr(kubejs_translator_state.os, "replace", transient_lock)

    kubejs_translator_state.atomic_write_bytes(target, b'{"new":true}')

    assert attempts == 3
    assert target.read_bytes() == b'{"new":true}'
