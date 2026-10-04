"""#125：金鑰格式錯誤的訊息與日誌不得包含完整原始金鑰。"""

from __future__ import annotations

import pytest

from translation_tool.core import lm_config_rules
from translation_tool.core.lm_config_rules import (
    validate_api_keys_from_ui,
)

# 刻意構造「格式不對但長得像真金鑰」的字串：使用者可能貼錯欄位或貼到殘缺金鑰。
BAD_PREFIX = "XYZ_SECRET_TOKEN_1234567890_ABCDEFGHIJKLMNOP"
SHORT_KEY = "AIzaShortSecret123"
BAD_CHARS = "AIzaSy!bad chars ***" + "A" * 30


@pytest.mark.parametrize("bad", [BAD_PREFIX, SHORT_KEY, BAD_CHARS])
def test_ui_validation_error_does_not_leak_key(bad):
    with pytest.raises(RuntimeError) as exc:
        validate_api_keys_from_ui([bad])
    assert bad not in str(exc.value)


@pytest.mark.parametrize("bad", [BAD_PREFIX, SHORT_KEY, BAD_CHARS])
def test_runtime_validation_error_and_log_do_not_leak_key(bad, monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(lm_config_rules, "log_error", logged.append)
    monkeypatch.setattr(lm_config_rules, "_get_all_keys", lambda: [bad])
    with pytest.raises(RuntimeError) as exc:
        lm_config_rules.validate_api_keys()
    assert bad not in str(exc.value)
    assert all(bad not in m for m in logged)


def test_save_failure_traceback_is_redacted(monkeypatch, caplog):
    """儲存設定失敗時記錄的 traceback 不得帶出原始金鑰。"""
    import logging

    from app.views.config import config_actions

    secret = "AIzaSy" + "Q" * 33
    monkeypatch.setattr(config_actions, "show_snack", lambda *a, **k: None)

    class View:
        page = object()

        def __getattr__(self, name):
            raise ValueError(f"boom {secret}")

    with caplog.at_level(logging.ERROR, logger=config_actions.logger.name):
        ok = config_actions.save_config_from_view(
            View(),
            load_config_json_fn=dict,
            save_config_json_fn=lambda c: None,
            validate_api_keys_from_ui_fn=lambda k: None,
        )
    assert ok is False
    assert secret not in caplog.text
