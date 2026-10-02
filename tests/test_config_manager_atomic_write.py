"""Failure-injection coverage for atomic config persistence."""

from __future__ import annotations

import json
from pathlib import Path

from translation_tool.utils import config_manager


def _temp_files(path: Path) -> list[Path]:
    """Return task-owned temporary files next to the config target."""
    return list(path.parent.glob(f".{path.name}.*.tmp"))


def test_save_config_atomically_replaces_file_in_same_directory(tmp_path, monkeypatch):
    """The published config is complete and the replacement source is a sibling."""
    target = tmp_path / "config.json"
    target.write_text('{"old": true}', encoding="utf-8")
    seen: list[tuple[Path, Path]] = []
    real_replace = config_manager.os.replace

    def replace(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        seen.append((source_path, destination_path))
        assert source_path.parent == target.parent
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(config_manager.os, "replace", replace)

    assert config_manager.save_config({"new": [1, 2, 3]}, target) is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": [1, 2, 3]}
    assert seen and seen[0][1] == target
    assert _temp_files(target) == []


def test_save_config_serialization_failure_preserves_existing_file(tmp_path):
    """A non-serializable value must not truncate the previous config."""
    target = tmp_path / "config.json"
    original = '{"old": true}\n'
    target.write_text(original, encoding="utf-8")

    assert config_manager.save_config({"bad": object()}, target) is False
    assert target.read_text(encoding="utf-8") == original
    assert _temp_files(target) == []


def test_save_config_fsync_failure_preserves_existing_file(tmp_path, monkeypatch):
    """Durability preparation must finish before the old file is replaced."""
    target = tmp_path / "config.json"
    original = '{"old": true}'
    target.write_text(original, encoding="utf-8")

    def fail_fsync(_fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(config_manager.os, "fsync", fail_fsync)

    assert config_manager.save_config({"new": True}, target) is False
    assert target.read_text(encoding="utf-8") == original
    assert _temp_files(target) == []


def test_save_config_replace_failure_preserves_existing_file(tmp_path, monkeypatch):
    """Windows sharing or antivirus failures must leave the old JSON intact."""
    target = tmp_path / "config.json"
    original = '{"old": true}'
    target.write_text(original, encoding="utf-8")

    def fail_replace(_source, _destination):
        raise PermissionError("simulated sharing violation")

    monkeypatch.setattr(config_manager.os, "replace", fail_replace)

    assert config_manager.save_config({"new": True}, target) is False
    assert target.read_text(encoding="utf-8") == original
    assert _temp_files(target) == []


def test_save_config_readback_failure_reports_failure_after_commit(
    tmp_path, monkeypatch
):
    """A post-replace verification error is observable without unsafe rollback."""
    target = tmp_path / "config.json"
    target.write_text('{"old": true}', encoding="utf-8")

    real_load = config_manager.json.load
    calls = 0

    def fail_readback(stream):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_load(stream)
        raise OSError("simulated readback failure")

    monkeypatch.setattr(config_manager.json, "load", fail_readback)

    assert config_manager.save_config({"new": True}, target) is False
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert _temp_files(target) == []
