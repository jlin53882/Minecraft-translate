"""ChatGPT provider settings page behavior tests."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.views.config_view import ConfigView
from tests.conftest import mock_page
from translation_tool.utils.config_manager import DEFAULT_CONFIG


def _chatgpt_config_view(monkeypatch):
    config = deepcopy(DEFAULT_CONFIG)
    config["lm_translator"]["chatgpt_model"] = "gpt-alpha"
    monkeypatch.setattr("app.views.config_view.load_config_json", lambda: config)
    monkeypatch.setattr(
        "app.views.config_view.chatgpt_account_status",
        lambda: {"connected": False, "email": ""},
    )
    return ConfigView(mock_page())


def test_per_model_settings_survive_model_switches_and_are_collected(monkeypatch):
    view = _chatgpt_config_view(monkeypatch)
    view._hydrate_chatgpt_model_settings(
        {
            "gpt-alpha": {
                "max_input_token_budget": 12000,
                "reasoning_effort": "high",
            },
            "gpt-beta": {"max_input_token_budget": 24000, "reasoning_effort": "low"},
        }
    )
    view._sync_chatgpt_model_options(
        [
            SimpleNamespace(slug="gpt-alpha", display_name="GPT Alpha"),
            SimpleNamespace(slug="gpt-beta", display_name="GPT Beta"),
        ]
    )
    assert view.chatgpt_context_budget_control.value == "12000"
    assert view.chatgpt_reasoning_effort_control.value == "high"

    view.chatgpt_context_budget_control.value = "16000"
    view.chatgpt_reasoning_effort_control.value = "medium"
    view.chatgpt_model_control.value = "gpt-beta"
    view._on_chatgpt_model_selected()
    assert view.chatgpt_context_budget_control.value == "24000"
    assert view.chatgpt_reasoning_effort_control.value == "low"

    view.chatgpt_context_budget_control.value = "30000"
    view.chatgpt_reasoning_effort_control.value = "model_default"
    view.chatgpt_model_control.value = "gpt-alpha"
    view._on_chatgpt_model_selected()
    assert view.chatgpt_context_budget_control.value == "16000"
    assert view.chatgpt_reasoning_effort_control.value == "medium"
    assert view.collect_chatgpt_model_settings() == {
        "gpt-alpha": {
            "max_input_token_budget": 16000,
            "reasoning_effort": "medium",
        },
        "gpt-beta": {"max_input_token_budget": 30000},
    }


def test_reauth_required_status_does_not_enable_chatgpt_translation_controls(
    monkeypatch,
):
    view = _chatgpt_config_view(monkeypatch)
    monkeypatch.setattr(
        "app.views.config_view.chatgpt_account_status",
        lambda: {
            "connected": False,
            "client_id_registered": True,
            "oauth_session_present": True,
            "reauth_required": True,
            "email": "user@example.test",
        },
    )

    view._refresh_chatgpt_controls()

    assert "重新登入" in view.chatgpt_status_text.value
    assert view.chatgpt_login_button.disabled is False
    assert view.chatgpt_refresh_button.disabled is True
    assert view.chatgpt_disconnect_button.disabled is False
    assert view.chatgpt_model_control.disabled is True


def test_disconnected_registration_status_explains_client_id_reuse(monkeypatch):
    view = _chatgpt_config_view(monkeypatch)
    monkeypatch.setattr(
        "app.views.config_view.chatgpt_account_status",
        lambda: {
            "connected": False,
            "client_id_registered": True,
            "oauth_session_present": False,
            "reauth_required": False,
            "email": "user@example.test",
        },
    )

    view._refresh_chatgpt_controls()

    assert "沿用已註冊帳號" in view.chatgpt_status_text.value
    assert view.chatgpt_login_button.disabled is False
    assert view.chatgpt_disconnect_button.disabled is True


@pytest.mark.parametrize(
    ("budget", "effort", "message"),
    [("0", "low", "大於 0"), ("abc", "low", "正整數"), ("1000", "maximum", "思考強度")],
)
def test_per_model_settings_reject_invalid_values(monkeypatch, budget, effort, message):
    view = _chatgpt_config_view(monkeypatch)
    view.chatgpt_context_budget_control.value = budget
    view.chatgpt_reasoning_effort_control.value = effort

    with pytest.raises(ValueError, match=message):
        view.collect_chatgpt_model_settings()
