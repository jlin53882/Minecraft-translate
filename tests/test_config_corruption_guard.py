"""設定檔損毀保護：既存 config.json 無法解析時，任何整份寫入都不得覆蓋它。"""

from __future__ import annotations

import json
import logging

import pytest

from app import config_store
from app.services_impl import config_service
from translation_tool.utils import config_manager

CORRUPT = b'{"lm_translator": {"keys": ["sk-recoverable-key"]}, "broken": '


@pytest.fixture
def corrupt_cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_bytes(CORRUPT)
    monkeypatch.setattr(config_service, "CONFIG_PATH", str(path))
    config_manager.clear_config_cache()
    yield path
    config_manager.clear_config_cache()


def test_status_reports_each_file_state(tmp_path):
    path = tmp_path / "config.json"
    assert config_manager.config_file_status(path) == config_manager.CONFIG_MISSING
    path.write_text("{}", encoding="utf-8")
    assert config_manager.config_file_status(path) == config_manager.CONFIG_OK
    path.write_bytes(CORRUPT)
    assert config_manager.config_file_status(path) == config_manager.CONFIG_CORRUPT
    path.write_text("[1, 2]", encoding="utf-8")
    assert config_manager.config_file_status(path) == config_manager.CONFIG_CORRUPT
    path.write_bytes(b"\xff\xfe\x00bad")
    assert config_manager.config_file_status(path) == config_manager.CONFIG_CORRUPT


def test_save_config_refuses_to_overwrite_corrupt_file(corrupt_cfg):
    assert config_manager.save_config({"new": True}, corrupt_cfg) is False
    assert corrupt_cfg.read_bytes() == CORRUPT
    assert list(corrupt_cfg.parent.glob(".config.json.*.tmp")) == []


def test_save_config_first_time_creation_still_succeeds(tmp_path):
    path = tmp_path / "config.json"
    assert config_manager.save_config({"new": True}, path) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}


def test_save_config_revalidates_right_before_replace(tmp_path, monkeypatch):
    """讀取與寫入之間檔案被改壞：不可靠事前檢查，替換前要再驗證一次。"""
    path = tmp_path / "config.json"
    path.write_text('{"ok": true}', encoding="utf-8")
    real_fsync = config_manager.os.fsync

    def fsync_then_corrupt(fd):
        real_fsync(fd)
        path.write_bytes(CORRUPT)

    monkeypatch.setattr(config_manager.os, "fsync", fsync_then_corrupt)

    assert config_manager.save_config({"new": True}, path) is False
    assert path.read_bytes() == CORRUPT


def test_both_whole_file_writers_refuse(corrupt_cfg):
    assert config_service.save_config_json({"lm_translator": {"keys": []}}) is False
    assert corrupt_cfg.read_bytes() == CORRUPT
    assert config_store.save({"lm_translator": {"keys": []}}) is False
    assert corrupt_cfg.read_bytes() == CORRUPT
    assert config_store.set_value("ui.theme_mode", "light") is False
    assert corrupt_cfg.read_bytes() == CORRUPT


def test_background_whole_file_writers_refuse(corrupt_cfg, monkeypatch):
    from translation_tool.translation_db import settings as db_settings

    monkeypatch.setattr(config_manager, "CONFIG_PATH", corrupt_cfg)
    assert db_settings.forget_priority_lines({"x"}) is False
    assert corrupt_cfg.read_bytes() == CORRUPT


def test_corrupt_file_is_parsed_and_logged_once_then_recovers(corrupt_cfg, caplog):
    with caplog.at_level(logging.ERROR, logger=config_manager.log.name):
        first = config_manager.load_config(corrupt_cfg)
        config_manager.load_config(corrupt_cfg)
        config_manager.load_config(corrupt_cfg)
    errors = [r for r in caplog.records if "無法讀取" in r.getMessage()]
    assert len(errors) == 1
    assert first["lm_translator"]["keys"] != ["sk-recoverable-key"]

    corrupt_cfg.write_text(
        json.dumps({"lm_translator": {"keys": ["sk-fixed"]}}), encoding="utf-8"
    )
    assert config_manager.load_config(corrupt_cfg)["lm_translator"]["keys"] == [
        "sk-fixed"
    ]
    assert config_manager.config_file_unsafe_to_overwrite(corrupt_cfg) is False
    assert config_service.save_config_json({"lm_translator": {"keys": []}}) is True


def test_ui_save_keeps_values_and_explains_corruption(corrupt_cfg, monkeypatch):
    from app.views.config import config_actions

    shown: list[str] = []
    monkeypatch.setattr(
        config_actions, "show_snack", lambda _p, msg, *a: shown.append(msg)
    )

    class View:
        page = object()

    ok = config_actions._write_config_with_feedback(
        View(), {"x": 1}, config_service.save_config_json
    )
    assert ok is False
    assert corrupt_cfg.read_bytes() == CORRUPT
    assert shown and "格式損毀" in shown[0] and "不會覆寫" in shown[0]
