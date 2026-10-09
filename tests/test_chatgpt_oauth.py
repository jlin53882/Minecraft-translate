"""Unit tests for ChatGPT OAuth credential and token flows."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from translation_tool.core import codex_oauth as oauth


class _Response:
    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300

    def json(self):
        return self.payload

    def raise_for_status(self):
        if not self.ok:
            raise oauth.requests.HTTPError(response=self)


class _Server:
    def __init__(self, address, handler):
        self.server_address = (address[0], 43210)
        self.handler = handler
        self.closed = False

    def serve_forever(self):
        return None

    def shutdown(self):
        return None

    def server_close(self):
        self.closed = True


@pytest.fixture
def no_credential_file_lock(monkeypatch):
    monkeypatch.setattr(oauth, "_credential_file_lock", lambda: nullcontext())


def _pending_login(server=None, **overrides):
    values = {
        "server": server or _Server(("127.0.0.1", 0), object),
        "state": "expected-state",
        "nonce": "expected-nonce",
        "verifier": "pkce-verifier",
        "redirect_uri": "http://127.0.0.1:43210/auth/callback",
        "requested_client_id": oauth._DYNAMIC_CLIENT_ID,
        "existing_record": {"ext_agent_host_id": "urn:uuid:host"},
        "metadata": {"token_endpoint": "https://auth.openai.com/oauth/token"},
    }
    values.update(overrides)
    return oauth.PendingChatGPTLogin(**values)


def test_credential_record_round_trips_and_rejects_unknown_format(monkeypatch):
    monkeypatch.setattr(oauth, "_dpapi_transform", lambda payload, *, protect: payload)
    record = {"access_token": "secret", "email": "user@example.test"}

    encoded = oauth._encode_record(record)

    assert encoded.startswith(oauth._DATA_MAGIC)
    assert oauth._decode_record(encoded) == record
    with pytest.raises(oauth.ChatGPTOAuthError, match="格式無法辨識"):
        oauth._decode_record(b"unknown-format")


@pytest.mark.parametrize(
    ("record", "connected", "session", "scope", "reauth"),
    [
        (
            {
                "client_id": "registered-client",
                "access_token": "access",
                "refresh_token": "refresh",
                "scopes": ["chatgpt.tokens.use.direct"],
            },
            True,
            True,
            True,
            False,
        ),
        (
            {"client_id": "registered-client", "subject": "account-1"},
            False,
            False,
            False,
            False,
        ),
        (
            {
                "client_id": "registered-client",
                "access_token": "",
                "refresh_token": "refresh",
                "scopes": ["openid"],
            },
            False,
            True,
            False,
            True,
        ),
    ],
)
def test_account_status_distinguishes_registration_session_and_direct_scope(
    monkeypatch, record, connected, session, scope, reauth
):
    monkeypatch.setattr(oauth, "_read_record_unlocked", lambda: dict(record))

    status = oauth.chatgpt_account_status()

    assert status["client_id_registered"] is bool(record.get("client_id"))
    assert status["connected"] is connected
    assert status["oauth_session_present"] is session
    assert status["direct_scope_granted"] is scope
    assert status["reauth_required"] is reauth


def test_credential_path_uses_platform_specific_application_data_roots(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

    monkeypatch.setattr(oauth.sys, "platform", "darwin")
    assert oauth._credential_path() == (
        tmp_path
        / "Library"
        / "Application Support"
        / "MinecraftTranslate"
        / "chatgpt-oauth.dat"
    )

    monkeypatch.setattr(oauth.sys, "platform", "linux")
    assert oauth._credential_path() == (
        tmp_path / "xdg-data" / "MinecraftTranslate" / "chatgpt-oauth.dat"
    )

    monkeypatch.setattr(oauth.sys, "platform", "win32")
    assert oauth._credential_path() == (
        tmp_path / "local-app-data" / "MinecraftTranslate" / "chatgpt-oauth.dat"
    )


def test_macos_legacy_credential_path_is_migrated_after_secure_write(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(oauth.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    target = (
        tmp_path
        / "Library"
        / "Application Support"
        / "MinecraftTranslate"
        / "chatgpt-oauth.dat"
    )
    legacy = tmp_path / ".local" / "share" / "MinecraftTranslate" / "chatgpt-oauth.dat"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(oauth._DATA_MAGIC + b"legacy-encoded-record")
    monkeypatch.setattr(oauth, "_credential_path", lambda: target)
    monkeypatch.setattr(oauth, "_legacy_macos_credential_path", lambda: legacy)
    monkeypatch.setattr(
        oauth,
        "_validate_legacy_macos_credential_file",
        lambda _path: None,
    )
    record = {"client_id": "registered-client", "access_token": "opaque-secret"}
    monkeypatch.setattr(oauth, "_decode_record", lambda raw: record if raw else {})
    writes = []

    def write_new_record(value):
        assert legacy.exists()
        writes.append(value)

    monkeypatch.setattr(oauth, "_write_record_unlocked", write_new_record)

    assert oauth._read_record_unlocked() == record
    assert writes == [record]
    assert not legacy.exists()


@pytest.mark.parametrize(
    ("mode", "private"), [(0o600, True), (0o640, False), (0o644, False)]
)
def test_macos_legacy_credential_file_requires_owner_only_permissions(
    monkeypatch, mode, private
):
    monkeypatch.setattr(oauth.sys, "platform", "darwin")
    legacy = SimpleNamespace(
        is_symlink=lambda: False,
        stat=lambda: SimpleNamespace(st_mode=mode),
    )

    if private:
        oauth._validate_legacy_macos_credential_file(legacy)
    else:
        with pytest.raises(oauth.ChatGPTOAuthError, match="權限不安全"):
            oauth._validate_legacy_macos_credential_file(legacy)


@pytest.mark.parametrize(
    ("expected_nonce", "valid"), [("nonce-expected", True), ("wrong-nonce", False)]
)
def test_id_token_requires_a_valid_signature_and_matching_nonce(
    monkeypatch, expected_nonce, valid
):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def encode_integer(value):
        byte_length = (value.bit_length() + 7) // 8
        return (
            oauth.base64.urlsafe_b64encode(value.to_bytes(byte_length, "big"))
            .rstrip(b"=")
            .decode("ascii")
        )

    jwk = {
        "kty": "RSA",
        "use": "sig",
        "kid": "signing-key",
        "alg": "RS256",
        "n": encode_integer(public_numbers.n),
        "e": encode_integer(public_numbers.e),
    }
    token = oauth.jwt.encode(
        {
            "iss": oauth._ISSUER,
            "aud": "registered-client",
            "exp": oauth.time.time() + 300,
            "sub": "account-1",
            "nonce": "nonce-expected",
            "email": "user@example.test",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "signing-key"},
    )
    monkeypatch.setattr(
        oauth.requests,
        "get",
        lambda *_args, **_kwargs: _Response({"keys": [jwk]}),
    )
    metadata = {
        "id_token_signing_alg_values_supported": ["RS256"],
        "jwks_uri": "https://auth.openai.com/.well-known/jwks.json",
    }

    if valid:
        claims = oauth._validate_id_token(
            token,
            client_id="registered-client",
            nonce=expected_nonce,
            metadata=metadata,
        )
        assert claims["sub"] == "account-1"
    else:
        with pytest.raises(oauth.ChatGPTOAuthError, match="nonce 不符"):
            oauth._validate_id_token(
                token,
                client_id="registered-client",
                nonce=expected_nonce,
                metadata=metadata,
            )


def test_begin_login_builds_pkce_authorization_url_and_stops_listener(
    monkeypatch,
):
    values = iter(("state-value", "nonce-value", "pkce-verifier"))
    server = _Server(("127.0.0.1", 0), object)
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda _size: next(values))
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: {
            "issuer": oauth._ISSUER,
            "authorization_endpoint": "https://auth.openai.com/authorize",
        },
    )
    monkeypatch.setattr(
        oauth,
        "_load_or_create_host_record",
        lambda: {"ext_agent_host_id": "urn:uuid:host"},
    )
    monkeypatch.setattr(oauth, "ThreadingHTTPServer", lambda address, handler: server)

    pending = oauth.begin_chatgpt_login()
    params = parse_qs(urlsplit(pending.authorization_url).query)

    assert params["client_id"] == [oauth._DYNAMIC_CLIENT_ID]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == [pending.redirect_uri]
    assert params["redirect_uri"][0].startswith("http://127.0.0.1:")
    assert params["state"] == ["state-value"]
    assert params["nonce"] == ["nonce-value"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [
        oauth.base64.urlsafe_b64encode(oauth.hashlib.sha256(b"pkce-verifier").digest())
        .rstrip(b"=")
        .decode("ascii")
    ]
    assert params["ext_agent_host_id"] == ["urn:uuid:host"]
    pending.cancel()
    assert server.closed is True


def test_begin_login_reuses_saved_registration_and_host_identity(monkeypatch):
    server = _Server(("127.0.0.1", 0), object)
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda size: f"value-{size}")
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: {
            "issuer": oauth._ISSUER,
            "authorization_endpoint": "https://auth.openai.com/authorize",
        },
    )
    monkeypatch.setattr(
        oauth,
        "_load_or_create_host_record",
        lambda: {
            "ext_agent_host_id": "urn:uuid:saved-host",
            "client_id": "issued-client-id",
            "subject": "account-1",
            "email": "user@example.test",
        },
    )
    monkeypatch.setattr(oauth, "ThreadingHTTPServer", lambda address, handler: server)

    pending = oauth.begin_chatgpt_login()
    params = parse_qs(urlsplit(pending.authorization_url).query)

    assert params["client_id"] == ["issued-client-id"]
    assert params["ext_agent_host_id"] == ["urn:uuid:saved-host"]
    assert params["login_hint"] == ["user@example.test"]
    assert "agent_name_hint" not in params
    pending.cancel()


def test_login_completion_exchanges_code_and_persists_rotating_credentials(
    monkeypatch, no_credential_file_lock
):
    pending = _pending_login()
    pending.receive_callback(
        {
            "state": "expected-state",
            "code": "single-use-code",
            "client_id": "registered-client",
        }
    )
    registration = []
    monkeypatch.setattr(
        pending, "_save_registration", lambda client_id: registration.append(client_id)
    )
    request = {}

    def post(url, **kwargs):
        request.update(url=url, **kwargs)
        return _Response(
            {
                "access_token": "access-next",
                "refresh_token": "refresh-next",
                "id_token": "validated-id-token",
                "scope": "openid chatgpt.tokens.use.direct",
                "expires_in": 3600,
            }
        )

    monkeypatch.setattr(oauth.requests, "post", post)
    monkeypatch.setattr(
        oauth,
        "_validate_id_token",
        lambda token, **kwargs: {"sub": "account-1", "email": "user@example.test"},
    )
    saved = []
    monkeypatch.setattr(oauth, "_write_record_unlocked", saved.append)
    monkeypatch.setattr(
        oauth,
        "chatgpt_account_status",
        lambda: {"connected": True, "email": "user@example.test"},
    )

    result = pending.complete()

    assert registration == ["registered-client"]
    assert request["url"] == "https://auth.openai.com/oauth/token"
    assert request["data"]["grant_type"] == "authorization_code"
    assert request["data"]["client_id"] == "registered-client"
    assert request["data"]["code"] == "single-use-code"
    assert request["data"]["code_verifier"] == "pkce-verifier"
    assert result["connected"] is True
    assert saved[-1]["ext_agent_host_id"] == "urn:uuid:host"
    assert saved[-1]["subject"] == "account-1"
    assert saved[-1]["refresh_token"] == "refresh-next"
    assert "chatgpt.tokens.use.direct" in saved[-1]["scopes"]


def test_login_requires_plan_scope_from_token_response_not_callback(
    monkeypatch, no_credential_file_lock
):
    pending = _pending_login()
    pending.receive_callback(
        {
            "state": "expected-state",
            "code": "single-use-code",
            "client_id": "registered-client",
            "scope": "openid chatgpt.tokens.use.direct",
        }
    )
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda *_args, **_kwargs: _Response(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "id_token": "id-token",
            }
        ),
    )
    monkeypatch.setattr(
        pending,
        "_save_registration",
        lambda _client_id: None,
    )
    monkeypatch.setattr(
        oauth,
        "_validate_id_token",
        lambda *_args, **_kwargs: pytest.fail(
            "scope validation must reject before storing or validating the identity"
        ),
    )
    monkeypatch.setattr(
        oauth,
        "_write_record_unlocked",
        lambda _record: pytest.fail("inadequately scoped credentials must not persist"),
    )

    with pytest.raises(oauth.ChatGPTOAuthError, match="尚未授權使用方案額度"):
        pending.complete()


def test_login_completion_rejects_state_mismatch_before_token_exchange(monkeypatch):
    pending = _pending_login()
    pending.receive_callback({"state": "attacker-state", "code": "code"})
    monkeypatch.setattr(
        oauth.requests, "post", lambda *args, **kwargs: pytest.fail("POST")
    )

    with pytest.raises(oauth.ChatGPTOAuthError, match="state 不符"):
        pending.complete()


def test_saved_registration_rejects_a_different_subject_on_relogin(
    monkeypatch, no_credential_file_lock
):
    pending = _pending_login(
        requested_client_id="registered-client",
        existing_record={
            "ext_agent_host_id": "urn:uuid:host",
            "client_id": "registered-client",
            "subject": "account-1",
        },
    )
    pending.receive_callback(
        {
            "state": "expected-state",
            "code": "single-use-code",
            "client_id": "registered-client",
        }
    )
    monkeypatch.setattr(pending, "_save_registration", lambda _client_id: None)
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda *_args, **_kwargs: _Response(
            {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "id_token": "validated-id-token",
                "scope": "openid chatgpt.tokens.use.direct",
            }
        ),
    )
    monkeypatch.setattr(
        oauth,
        "_validate_id_token",
        lambda *_args, **_kwargs: {"sub": "account-2", "email": "other@example.test"},
    )
    monkeypatch.setattr(
        oauth,
        "_write_record_unlocked",
        lambda _record: pytest.fail("different subject must not replace registration"),
    )

    with pytest.raises(oauth.ChatGPTOAuthError, match="不同"):
        pending.complete()


def test_access_token_refresh_rotates_refresh_token_and_scopes(
    monkeypatch, no_credential_file_lock
):
    stored = {
        "client_id": "registered-client",
        "access_token": "access-old",
        "refresh_token": "refresh-old",
        "expires_at": 0,
        "scopes": ["openid"],
    }
    monkeypatch.setattr(oauth, "_read_record_unlocked", lambda: dict(stored))
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: {"token_endpoint": "https://auth.openai.com/oauth/token"},
    )
    saved = []
    monkeypatch.setattr(oauth, "_write_record_unlocked", saved.append)
    request = {}

    def post(url, **kwargs):
        request.update(url=url, **kwargs)
        return _Response(
            {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_in": 900,
                "scope": "openid profile chatgpt.tokens.use.direct",
            }
        )

    monkeypatch.setattr(oauth.requests, "post", post)

    assert oauth.get_chatgpt_access_token() == "access-new"
    assert request["data"]["grant_type"] == "refresh_token"
    assert request["data"]["refresh_token"] == "refresh-old"
    assert saved[-1]["refresh_token"] == "refresh-new"
    assert saved[-1]["scopes"] == [
        "chatgpt.tokens.use.direct",
        "openid",
        "profile",
    ]


def _install_refresh_response(monkeypatch, stored, tokens):
    saved = []
    monkeypatch.setattr(oauth, "_read_record_unlocked", lambda: dict(stored))
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: {"token_endpoint": "https://auth.openai.com/oauth/token"},
    )
    monkeypatch.setattr(oauth, "_write_record_unlocked", saved.append)
    monkeypatch.setattr(
        oauth.requests, "post", lambda *_args, **_kwargs: _Response(tokens)
    )
    return saved


def test_refresh_inherits_trusted_scope_when_scope_is_omitted(
    monkeypatch, no_credential_file_lock
):
    stored = {
        "ext_agent_host_id": "urn:uuid:host",
        "client_id": "registered-client",
        "subject": "account-1",
        "access_token": "access-old",
        "refresh_token": "refresh-old",
        "expires_at": 0,
        "scopes": ["openid", "chatgpt.tokens.use.direct"],
    }
    saved = _install_refresh_response(
        monkeypatch,
        stored,
        {"access_token": "access-new", "refresh_token": "refresh-new"},
    )

    assert oauth.get_chatgpt_access_token() == "access-new"
    assert saved[-1]["refresh_token"] == "refresh-new"
    assert saved[-1]["scopes"] == ["chatgpt.tokens.use.direct", "openid"]


@pytest.mark.parametrize("scope", ["openid", ""])
def test_refresh_scope_downgrade_saves_rotation_and_requires_reauthorization(
    monkeypatch, no_credential_file_lock, scope
):
    stored = {
        "ext_agent_host_id": "urn:uuid:host",
        "client_id": "registered-client",
        "subject": "account-1",
        "email": "user@example.test",
        "access_token": "access-old",
        "refresh_token": "refresh-old",
        "expires_at": 0,
        "scopes": ["openid", "chatgpt.tokens.use.direct"],
    }
    saved = _install_refresh_response(
        monkeypatch,
        stored,
        {
            "access_token": "access-new",
            "refresh_token": "refresh-rotated",
            "scope": scope,
        },
    )

    with pytest.raises(oauth.ChatGPTOAuthError, match="重新登入"):
        oauth.get_chatgpt_access_token()

    assert saved[-1]["refresh_token"] == "refresh-rotated"
    assert saved[-1]["access_token"] == ""
    assert saved[-1]["scopes"] == scope.split()
    monkeypatch.setattr(oauth, "_read_record_unlocked", lambda: dict(saved[-1]))
    status = oauth.chatgpt_account_status()
    assert status["connected"] is False
    assert status["client_id_registered"] is True
    assert status["reauth_required"] is True
    assert status["email"] == "user@example.test"


def test_refresh_without_scope_or_trusted_prior_scope_fails_closed(
    monkeypatch, no_credential_file_lock
):
    stored = {
        "client_id": "registered-client",
        "access_token": "access-old",
        "refresh_token": "refresh-old",
        "expires_at": 0,
    }
    saved = _install_refresh_response(
        monkeypatch,
        stored,
        {"access_token": "access-new", "refresh_token": "refresh-new"},
    )

    with pytest.raises(oauth.ChatGPTOAuthError, match="重新登入"):
        oauth.get_chatgpt_access_token()

    assert saved[-1]["refresh_token"] == "refresh-new"
    assert saved[-1]["access_token"] == ""
    assert saved[-1]["scopes"] == []


def test_refresh_without_trusted_scope_does_not_return_cached_access_token(
    monkeypatch, no_credential_file_lock
):
    stored = {
        "client_id": "registered-client",
        "access_token": "access-old",
        "refresh_token": "refresh-old",
        "expires_at": oauth.time.time() + 3600,
    }
    saved = _install_refresh_response(
        monkeypatch,
        stored,
        {"access_token": "access-new", "refresh_token": "refresh-new"},
    )

    with pytest.raises(oauth.ChatGPTOAuthError, match="重新登入"):
        oauth.get_chatgpt_access_token()

    assert saved[-1]["access_token"] == ""
    assert saved[-1]["refresh_token"] == "refresh-new"


def test_temporary_refresh_network_failure_preserves_existing_credentials(
    monkeypatch, no_credential_file_lock
):
    stored = {
        "client_id": "registered-client",
        "access_token": "access-old",
        "refresh_token": "refresh-old",
        "expires_at": 0,
        "scopes": ["chatgpt.tokens.use.direct"],
    }
    monkeypatch.setattr(oauth, "_read_record_unlocked", lambda: dict(stored))
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: {"token_endpoint": "https://auth.openai.com/oauth/token"},
    )
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            oauth.requests.ConnectionError("offline")
        ),
    )
    saved = []
    monkeypatch.setattr(oauth, "_write_record_unlocked", saved.append)

    with pytest.raises(oauth.ChatGPTOAuthError, match="檢查網路"):
        oauth.get_chatgpt_access_token()

    assert saved == []
    assert stored["access_token"] == "access-old"
    assert stored["refresh_token"] == "refresh-old"


def test_list_models_keeps_only_named_visible_models(monkeypatch):
    monkeypatch.setattr(oauth, "get_chatgpt_access_token", lambda **kwargs: "access")
    monkeypatch.setattr(
        oauth.requests,
        "get",
        lambda *args, **kwargs: _Response(
            {
                "models": [
                    {"slug": "gpt-a", "display_name": "GPT A", "visibility": "list"},
                    {
                        "slug": "gpt-hidden",
                        "display_name": "Hidden",
                        "visibility": "private",
                    },
                    {"slug": "", "display_name": "Missing slug", "visibility": "list"},
                    {"slug": "gpt-no-name", "visibility": "list"},
                ]
            }
        ),
    )

    assert oauth.list_chatgpt_models() == [oauth.ChatGPTModel("gpt-a", "GPT A")]


def test_disconnect_clears_session_tokens_but_preserves_registration_mapping(
    monkeypatch, no_credential_file_lock
):
    monkeypatch.setattr(
        oauth,
        "_read_record_unlocked",
        lambda: {
            "ext_agent_host_id": "urn:uuid:host",
            "client_id": "registered-client",
            "subject": "account-1",
            "email": "user@example.test",
            "access_token": "access",
            "refresh_token": "refresh",
            "id_token": "id-token",
            "scopes": ["openid", "chatgpt.tokens.use.direct"],
            "expires_at": 12345,
        },
    )
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: {"revocation_endpoint": "https://auth.openai.com/revoke"},
    )
    calls = []
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs)) or _Response({}),
    )
    saved = []
    monkeypatch.setattr(oauth, "_write_record_unlocked", saved.append)

    assert oauth.disconnect_chatgpt_account() is True
    assert calls[0][1]["data"]["token"] == "refresh"
    assert saved == [
        {
            "ext_agent_host_id": "urn:uuid:host",
            "client_id": "registered-client",
            "subject": "account-1",
            "email": "user@example.test",
        }
    ]


def test_disconnect_revocation_failure_still_clears_tokens_and_keeps_registration(
    monkeypatch, no_credential_file_lock
):
    record = {
        "ext_agent_host_id": "urn:uuid:host",
        "client_id": "registered-client",
        "subject": "account-1",
        "email": "user@example.test",
        "access_token": "access",
        "refresh_token": "refresh",
        "id_token": "id-token",
        "scopes": ["openid", "chatgpt.tokens.use.direct"],
        "expires_at": 12345,
    }
    monkeypatch.setattr(oauth, "_read_record_unlocked", lambda: dict(record))
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: (_ for _ in ()).throw(oauth.requests.ConnectionError("offline")),
    )
    monkeypatch.setattr(oauth.time, "sleep", lambda _seconds: None)
    saved = []
    monkeypatch.setattr(oauth, "_write_record_unlocked", saved.append)

    assert oauth.disconnect_chatgpt_account() is False
    assert saved == [
        {
            "ext_agent_host_id": "urn:uuid:host",
            "client_id": "registered-client",
            "subject": "account-1",
            "email": "user@example.test",
        }
    ]
