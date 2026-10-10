"""ChatGPT provider settings page behavior tests."""

import asyncio
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
        "app.views.config.chatgpt_oauth_panel.chatgpt_account_status",
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


def test_config_reload_preserves_valid_model_catalog_and_selection(monkeypatch):
    from app.views.config import chatgpt_oauth_panel as panel_module

    config = deepcopy(DEFAULT_CONFIG)
    config["lm_translator"]["provider"] = "chatgpt"
    config["lm_translator"]["chatgpt_model"] = "gpt-alpha"
    config["lm_translator"]["chatgpt_model_settings"] = {
        "gpt-alpha": {
            "max_input_token_budget": 264000,
            "reasoning_effort": "high",
        }
    }
    monkeypatch.setattr("app.views.config_view.load_config_json", lambda: config)
    monkeypatch.setattr(
        panel_module,
        "chatgpt_account_status",
        lambda: {"connected": False, "email": ""},
    )
    view = ConfigView(mock_page())
    models = [SimpleNamespace(slug="gpt-alpha", display_name="GPT Alpha")]
    view._sync_chatgpt_model_options(models, profile_id="profile-a")

    view.load_config()

    assert view.chatgpt_model_control.value == "gpt-alpha"
    assert [option.key for option in view.chatgpt_model_control.options] == [
        "gpt-alpha"
    ]
    assert view._chatgpt_model_catalog_valid is True
    assert view.chatgpt_model_settings_panel.visible is True
    assert view.chatgpt_context_budget_control.value == "264000"
    assert view.chatgpt_reasoning_effort_control.value == "high"


def test_config_reload_invalidates_model_catalog_when_selected_model_is_missing(
    monkeypatch,
):
    from app.views.config import chatgpt_oauth_panel as panel_module

    config = deepcopy(DEFAULT_CONFIG)
    config["lm_translator"]["provider"] = "chatgpt"
    config["lm_translator"]["chatgpt_model"] = "gpt-removed"
    monkeypatch.setattr("app.views.config_view.load_config_json", lambda: config)
    monkeypatch.setattr(
        panel_module,
        "chatgpt_account_status",
        lambda: {"connected": False, "email": ""},
    )
    view = ConfigView(mock_page())
    view._sync_chatgpt_model_options(
        [SimpleNamespace(slug="gpt-alpha", display_name="GPT Alpha")],
        profile_id="profile-a",
    )

    view.load_config()

    assert view.chatgpt_model_control.value == "gpt-removed"
    assert [option.key for option in view.chatgpt_model_control.options] == [""]
    assert view._chatgpt_model_catalog_valid is False


def test_reauth_required_status_does_not_enable_chatgpt_translation_controls(
    monkeypatch,
):
    view = _chatgpt_config_view(monkeypatch)
    monkeypatch.setattr(
        "app.views.config.chatgpt_oauth_panel.chatgpt_account_status",
        lambda: {
            "connected": False,
            "client_id_registered": True,
            "oauth_session_present": True,
            "reauth_required": True,
            "email": "user@example.test",
        },
    )

    view.chatgpt_oauth_panel.refresh_controls()

    panel = view.chatgpt_oauth_panel
    assert "重新同意" in panel.status_text.value
    assert panel.login_button.disabled is False
    assert panel.authorize_usage_button.visible is True
    assert panel.authorize_usage_button.disabled is False
    assert panel.refresh_button.disabled is True
    assert panel.disconnect_button.disabled is False
    assert view.chatgpt_model_control.disabled is True


def test_disconnected_registration_status_explains_client_id_reuse(monkeypatch):
    view = _chatgpt_config_view(monkeypatch)
    monkeypatch.setattr(
        "app.views.config.chatgpt_oauth_panel.chatgpt_account_status",
        lambda: {
            "connected": False,
            "client_id_registered": True,
            "oauth_session_present": False,
            "reauth_required": False,
            "email": "user@example.test",
        },
    )

    view.chatgpt_oauth_panel.refresh_controls()

    panel = view.chatgpt_oauth_panel
    assert "沿用已註冊帳號" in panel.status_text.value
    assert panel.login_button.disabled is False
    assert panel.disconnect_button.disabled is True


def test_identity_without_plan_scope_shows_explicit_reconsent_action(monkeypatch):
    view = _chatgpt_config_view(monkeypatch)
    monkeypatch.setattr(
        "app.views.config.chatgpt_oauth_panel.chatgpt_account_status",
        lambda: {
            "connected": False,
            "identity_connected": True,
            "client_id_registered": True,
            "oauth_session_present": True,
            "plan_usage_authorized": False,
            "reauth_required": False,
            "email": "user@example.test",
            "accounts": [],
            "active_profile_id": "profile-a",
        },
    )

    view.chatgpt_oauth_panel.refresh_controls()

    panel = view.chatgpt_oauth_panel
    assert "身分" in panel.status_text.value
    assert "尚未授權" in panel.status_text.value
    assert panel.authorize_usage_button.visible is True
    assert panel.refresh_button.disabled is True
    assert view.chatgpt_model_control.disabled is True


def test_multiple_saved_accounts_are_available_in_account_selector(monkeypatch):
    view = _chatgpt_config_view(monkeypatch)
    monkeypatch.setattr(
        "app.views.config.chatgpt_oauth_panel.chatgpt_account_status",
        lambda: {
            "connected": True,
            "identity_connected": True,
            "client_id_registered": True,
            "oauth_session_present": True,
            "plan_usage_authorized": True,
            "reauth_required": False,
            "email": "b@example.test",
            "active_profile_id": "profile-b",
            "accounts": [
                {"profile_id": "profile-a", "email": "a@example.test"},
                {"profile_id": "profile-b", "email": "b@example.test"},
            ],
        },
    )

    view.chatgpt_oauth_panel.refresh_controls()

    selector = view.chatgpt_oauth_panel.account_selector
    assert selector.visible is True
    assert selector.disabled is False
    assert selector.value == "profile-b"
    assert [option.key for option in selector.options] == [
        "profile-a",
        "profile-b",
    ]


def test_account_switch_invalidates_stale_models_and_reload_binds_new_profile(
    monkeypatch,
):
    from app.views.config import chatgpt_oauth_panel as panel_module

    active = {"profile_id": "profile-a"}
    view = _chatgpt_config_view(monkeypatch)
    view._sync_chatgpt_model_options(
        [SimpleNamespace(slug="gpt-alpha", display_name="GPT Alpha")],
        profile_id="profile-a",
    )

    def account_status():
        return {
            "connected": True,
            "client_id_registered": True,
            "oauth_session_present": True,
            "plan_usage_authorized": True,
            "active_profile_id": active["profile_id"],
            "accounts": [
                {"profile_id": "profile-a", "email": "a@example.test"},
                {"profile_id": "profile-b", "email": "b@example.test"},
            ],
        }

    monkeypatch.setattr(panel_module, "chatgpt_account_status", account_status)
    monkeypatch.setattr(
        panel_module,
        "set_active_chatgpt_account",
        lambda profile_id: active.update(profile_id=profile_id) or account_status(),
    )
    reads = []

    def list_models(*, profile_id=None):
        reads.append(profile_id)
        if profile_id == "profile-b":
            raise RuntimeError("HTTP 403")
        return [SimpleNamespace(slug="gpt-alpha", display_name="GPT Alpha")]

    monkeypatch.setattr(panel_module, "list_chatgpt_models", list_models)
    panel = view.chatgpt_oauth_panel
    panel.refresh_controls()
    panel.account_selector.value = "profile-b"

    asyncio.run(panel._on_account_selected())

    assert active["profile_id"] == "profile-b"
    assert reads == ["profile-b"]
    assert view.chatgpt_model_control.value == "gpt-alpha"
    assert all(
        option.key != "gpt-alpha" for option in view.chatgpt_model_control.options
    )
    assert view.chatgpt_model_control.disabled is True
    assert view._chatgpt_model_catalog_valid is False
    assert view._chatgpt_model_catalog_profile_id == "profile-a"

    monkeypatch.setattr(
        panel_module,
        "list_chatgpt_models",
        lambda *, profile_id=None: [
            SimpleNamespace(slug="gpt-beta", display_name="GPT Beta")
        ],
    )
    asyncio.run(panel._on_refresh())

    assert view.chatgpt_model_control.value == "gpt-beta"
    assert [option.key for option in view.chatgpt_model_control.options] == ["gpt-beta"]
    assert view.chatgpt_model_control.disabled is False
    assert view._chatgpt_model_catalog_valid is True
    assert view._chatgpt_model_catalog_profile_id == "profile-b"


def test_rapid_account_switch_discards_late_model_response(monkeypatch):
    from app.views.config import chatgpt_oauth_panel as panel_module

    active = {"profile_id": "profile-a"}
    view = _chatgpt_config_view(monkeypatch)
    view._sync_chatgpt_model_options(
        [SimpleNamespace(slug="gpt-alpha", display_name="GPT Alpha")],
        profile_id="profile-a",
    )

    def account_status():
        return {
            "connected": True,
            "client_id_registered": True,
            "oauth_session_present": True,
            "plan_usage_authorized": True,
            "active_profile_id": active["profile_id"],
            "accounts": [
                {"profile_id": "profile-a", "email": "a@example.test"},
                {"profile_id": "profile-b", "email": "b@example.test"},
            ],
        }

    monkeypatch.setattr(panel_module, "chatgpt_account_status", account_status)

    def set_active(profile_id):
        active["profile_id"] = profile_id
        return account_status()

    monkeypatch.setattr(panel_module, "set_active_chatgpt_account", set_active)
    a_started = None
    release_a = None
    loaded = []

    async def fake_to_thread(func, *args, **kwargs):
        if func is set_active:
            return func(*args, **kwargs)
        if func is panel_module.list_chatgpt_models:
            profile_id = kwargs.get("profile_id")
            if profile_id == "profile-a":
                a_started.set()
                await release_a.wait()
                return [SimpleNamespace(slug="gpt-alpha", display_name="GPT Alpha")]
            return [SimpleNamespace(slug="gpt-beta", display_name="GPT Beta")]
        return func(*args, **kwargs)

    def capture_loaded(models, profile_id):
        loaded.append((profile_id, [model.slug for model in models]))
        view._sync_chatgpt_model_options(models, profile_id)

    panel = panel_module.ChatGPTOAuthPanel(
        view.page,
        view.chatgpt_model_control,
        capture_loaded,
        view._invalidate_chatgpt_model_options,
    )
    monkeypatch.setattr(panel_module.asyncio, "to_thread", fake_to_thread)
    panel.refresh_controls()

    async def run_switches():
        nonlocal a_started, release_a
        a_started = asyncio.Event()
        release_a = asyncio.Event()
        panel.account_selector.value = "profile-a"
        task_a = asyncio.create_task(panel._on_account_selected())
        await a_started.wait()
        panel.account_selector.value = "profile-b"
        task_b = asyncio.create_task(panel._on_account_selected())
        release_a.set()
        await asyncio.gather(task_a, task_b)

    asyncio.run(run_switches())

    assert active["profile_id"] == "profile-b"
    assert loaded == [("profile-b", ["gpt-beta"])]
    assert [option.key for option in view.chatgpt_model_control.options] == ["gpt-beta"]


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
