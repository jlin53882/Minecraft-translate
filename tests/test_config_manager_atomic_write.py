"""設定檔原子寫入的失敗注入測試。"""

from __future__ import annotations

import json
from pathlib import Path

from translation_tool.utils import config_manager


def _temp_files(path: Path) -> list[Path]:
    """回傳設定檔旁、由本次寫入建立的暫存檔。"""
    return list(path.parent.glob(f".{path.name}.*.tmp"))


def test_save_config_atomically_replaces_file_in_same_directory(tmp_path, monkeypatch):
    """發布後的設定檔必須完整，且替換來源是同目錄的暫存檔。"""
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
    """無法序列化的值不可截斷原本的設定檔。"""
    target = tmp_path / "config.json"
    original = '{"old": true}\n'
    target.write_text(original, encoding="utf-8")

    assert config_manager.save_config({"bad": object()}, target) is False
    assert target.read_text(encoding="utf-8") == original
    assert _temp_files(target) == []


def test_save_config_fsync_failure_preserves_existing_file(tmp_path, monkeypatch):
    """必須先完成落盤準備，才能替換舊檔。"""
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
    """Windows 檔案共用或防毒軟體造成的失敗，必須保留舊的 JSON 不變。"""
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
    """替換後的驗證錯誤必須能被觀察到，且不做不安全的回滾。"""
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
