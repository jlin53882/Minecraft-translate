"""設定預設值與錯誤日誌秘密遮罩的回歸測試。"""

from __future__ import annotations

import logging

from translation_tool.utils import config_manager


def test_schema_default_wins_over_example_value(monkeypatch, tmp_path):
    """schema 預設值優先，example 仍可補充 schema 未定義的欄位。"""
    monkeypatch.setattr(
        config_manager,
        "DEFAULT_CONFIG",
        {"section": {"value": "schema"}},
    )
    monkeypatch.setattr(
        config_manager,
        "load_config_example",
        lambda: {"section": {"value": "example"}, "extra": 1},
    )
    monkeypatch.setattr(
        config_manager,
        "read_config_file",
        lambda _path: (config_manager.CONFIG_MISSING, None),
    )
    monkeypatch.setattr(config_manager, "validate_config_values", lambda _config: None)

    config, cacheable = config_manager._load_config_uncached(
        tmp_path / "missing-config.json"
    )

    assert cacheable is True
    assert config == {"section": {"value": "schema"}, "extra": 1}


def test_config_listener_traceback_masks_secret(caplog):
    """設定訂閱者 traceback 不得把明確秘密欄位寫入應用程式日誌。"""
    from app import config_store

    secret = "custom-listener-secret-12345"

    def failing_listener():
        raise RuntimeError(f"api_key={secret}")

    unsubscribe = config_store.subscribe(failing_listener)
    try:
        with caplog.at_level(logging.ERROR, logger=config_store.logger.name):
            config_store.notify_saved()
    finally:
        unsubscribe()

    assert secret not in caplog.text
    assert "[REDACTED]" in caplog.text
