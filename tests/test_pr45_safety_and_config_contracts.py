from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from app import config_store
from app.services_impl import config_service
from translation_tool.core import lm_api_client
from translation_tool.core import lm_translator_shared_loop as loop_mod
from translation_tool.core.lm_config_schema import model_output_token_cap
from translation_tool.utils import cache_manager
from translation_tool.utils.cache_search_facade import CacheSearchFacade
from translation_tool.utils.config_manager import get_models_config
from translation_tool.utils.redaction import redact_mapping, redact_text


def test_redaction_masks_keys_and_secret_fields():
    secret = "AIza" + "x" * 30
    text = f"key={secret} authorization: Bearer {secret}"
    assert secret not in redact_text(text)
    assert (
        redact_mapping({"api_key": secret, "message": text})["api_key"] == "[REDACTED]"
    )


def test_http_error_message_does_not_expose_api_key(monkeypatch):
    secret = "AIza" + "s" * 30
    response = Mock(ok=False, status_code=403, text=f'{{"api_key":"{secret}"}}')
    monkeypatch.setattr(lm_api_client, "load_config", lambda: {"lm_translator": {}})
    monkeypatch.setattr(lm_api_client, "_post_with_retry", lambda *_a, **_k: response)

    with pytest.raises(requests.HTTPError) as exc_info:
        lm_api_client.call_gemini_requests(
            model_name="m",
            system_prompt="p",
            payload={},
            api_key=secret,
            temperature=0.2,
        )

    assert secret not in str(exc_info.value)
    assert "403" in str(exc_info.value)


def test_malformed_response_message_is_redacted(monkeypatch):
    secret = "AIza" + "m" * 30
    response = Mock(ok=True)
    response.json.return_value = {"api_key": secret, "unexpected": "shape"}
    monkeypatch.setattr(lm_api_client, "load_config", lambda: {"lm_translator": {}})
    monkeypatch.setattr(lm_api_client, "_post_with_retry", lambda *_a, **_k: response)

    with pytest.raises(RuntimeError) as exc_info:
        lm_api_client.call_gemini_requests(
            model_name="m",
            system_prompt="p",
            payload={},
            api_key=secret,
            temperature=0.2,
        )
    assert secret not in str(exc_info.value)


def test_model_config_preserves_optional_output_cap():
    cfg = {
        "lm_translator": {"models": {"a": {"enabled": True, "max_output_tokens": 123}}}
    }
    assert get_models_config(cfg)["a"] == {"enabled": True, "max_output_tokens": 123}
    assert model_output_token_cap(cfg["lm_translator"], "a") == 123
    assert model_output_token_cap(cfg["lm_translator"], "missing") is None


def test_changed_path_subscriber_is_compatible_with_legacy_subscriber(
    tmp_path, monkeypatch
):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_service, "CONFIG_PATH", str(path))
    legacy_calls: list[int] = []
    path_calls: list[frozenset[str]] = []
    unsubscribe_legacy = config_store.subscribe(lambda: legacy_calls.append(1))
    unsubscribe_paths = config_store.subscribe_paths(path_calls.append)
    try:
        assert config_store.set_value("ui.theme_mode", "light") is True
    finally:
        unsubscribe_legacy()
        unsubscribe_paths()
    assert legacy_calls == [1]
    assert path_calls == [frozenset({"ui.theme_mode"})]


def test_search_rebuild_failure_is_reported():
    facade = CacheSearchFacade(lambda: None, Mock())
    orchestrator = Mock()
    orchestrator.rebuild_search_index.side_effect = OSError("index unavailable")
    facade._orchestrator = orchestrator
    assert facade.rebuild_search_index(["lang"], {"lang": {}}) is False


def test_active_cache_root_does_not_follow_unsaved_runtime_config(
    monkeypatch, tmp_path
):
    state = cache_manager.cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    state.active_cache_root = old_root
    monkeypatch.setattr(cache_manager, "_configured_cache_root", lambda: new_root)
    assert cache_manager._get_cache_root() == old_root
    cache_manager.cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)


def test_api_schema_round_trip_keeps_old_models_shape():
    old = {"lm_translator": {"models": {"a": {"enabled": True}}}}
    assert get_models_config(old) == {"a": {"enabled": True}}


def test_shared_loop_does_not_report_success_when_cache_write_is_rejected(monkeypatch):
    item = {
        "path": "a",
        "text": "甲",
        "source_text": "A",
        "cache_type": "lang",
    }
    monkeypatch.setattr(loop_mod, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(loop_mod, "add_to_cache", lambda *_a, **_k: False)
    monkeypatch.setattr(loop_mod, "save_translation_cache", lambda *_a, **_k: True)
    monkeypatch.setattr(loop_mod, "load_config", lambda: {"lm_translator": {}})
    monkeypatch.setattr(loop_mod, "is_cancelled", lambda: False)

    result = loop_mod.translate_items_with_cache_loop(
        [item],
        translate_batch_smart=lambda batch, _total: (
            [{**batch[0], "text": "乙"}],
            "AUTO",
        ),
        cache_rules={},
    )

    assert result.status == "FAILED"
    assert result.last_error and "快取寫入被拒絕" in result.last_error
