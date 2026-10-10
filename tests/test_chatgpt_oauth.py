"""Unit tests for ChatGPT OAuth credential and token flows."""

import errno
import multiprocessing
import sys
import time
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


def _refresh_in_child_process(
    credential_path, start_event, ready_event, result_queue, calls
):
    """Run a competing token refresh from a real child process."""
    worker_oauth = oauth
    worker_oauth._credential_path = lambda: Path(credential_path)
    worker_oauth._dpapi_transform = lambda payload, *, protect: payload
    worker_oauth._discover = lambda: {
        "token_endpoint": "https://auth.openai.com/oauth/token"
    }

    def post(*_args, **_kwargs):
        with calls.get_lock():
            calls.value += 1
        time.sleep(0.35)
        return _Response(
            {
                "access_token": "access-rotated",
                "refresh_token": "refresh-rotated",
                "scope": "openid chatgpt.tokens.use.direct",
                "expires_in": 3600,
            }
        )

    worker_oauth.requests.post = post
    ready_event.set()
    if not start_event.wait(10):
        result_queue.put((False, "start timeout"))
        return
    try:
        result_queue.put((True, worker_oauth.get_chatgpt_access_token()))
    except Exception as exc:  # noqa: BLE001 - report child failures to parent
        result_queue.put((False, repr(exc)))


@pytest.fixture
def no_credential_file_lock(monkeypatch):
    monkeypatch.setattr(oauth, "_credential_file_lock", lambda: nullcontext())
    monkeypatch.setattr(oauth, "_dpapi_transform", lambda payload, *, protect: payload)


@pytest.fixture(autouse=True)
def isolate_oauth_credential_file(monkeypatch, tmp_path):
    native_path = oauth._credential_path
    credential_path = tmp_path / "chatgpt-oauth.dat"
    monkeypatch.setattr(oauth, "_credential_path", lambda: credential_path)
    monkeypatch.setattr(oauth, "_legacy_macos_credential_path", lambda: None)
    return credential_path, native_path


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


def test_windows_file_lock_retries_contention_and_unlocks_only_after_acquire(
    monkeypatch, tmp_path
):
    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def __init__(self, fail_count):
            self.fail_count = fail_count
            self.lock_calls = 0
            self.unlock_calls = 0

        def locking(self, _fd, mode, _count):
            if mode == self.LK_UNLCK:
                self.unlock_calls += 1
                return
            self.lock_calls += 1
            if self.fail_count is None or self.lock_calls <= self.fail_count:
                raise OSError(errno.EACCES, "locked")

    lock_module = FakeMsvcrt(fail_count=2)
    monkeypatch.setattr(oauth.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", lock_module)
    monkeypatch.setattr(oauth, "_credential_path", lambda: tmp_path / "oauth.dat")
    monkeypatch.setattr(oauth, "_CREDENTIAL_LOCK_TIMEOUT_SEC", 0.1)
    monkeypatch.setattr(oauth, "_CREDENTIAL_LOCK_RETRY_SEC", 0.001)

    with oauth._credential_file_lock():
        assert lock_module.lock_calls == 3
    assert lock_module.unlock_calls == 1

    blocked = FakeMsvcrt(fail_count=None)
    monkeypatch.setitem(sys.modules, "msvcrt", blocked)
    monkeypatch.setattr(oauth, "_CREDENTIAL_LOCK_TIMEOUT_SEC", 0.01)
    with (
        pytest.raises(oauth.ChatGPTOAuthError, match="等待逾時"),
        oauth._credential_file_lock(),
    ):
        pytest.fail("a blocked lock must not enter its critical section")
    assert blocked.unlock_calls == 0


def test_two_processes_share_one_serialized_refresh(
    monkeypatch, isolate_oauth_credential_file
):
    credential_path, _native_path = isolate_oauth_credential_file
    monkeypatch.setattr(oauth, "_dpapi_transform", lambda payload, *, protect: payload)
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: {"token_endpoint": "https://auth.openai.com/oauth/token"},
    )
    oauth._write_store_unlocked(
        {
            "schema_version": oauth._CREDENTIAL_STORE_VERSION,
            "ext_agent_host_id": "urn:uuid:host",
            "active_profile_id": "profile-a",
            "profiles": {
                "profile-a": {
                    "client_id": "client-a",
                    "subject": "account-a",
                    "access_token": "access-expired",
                    "refresh_token": "refresh-original",
                    "expires_at": 0,
                    "scopes": ["openid", "chatgpt.tokens.use.direct"],
                }
            },
        }
    )
    context = multiprocessing.get_context("spawn")
    calls = context.Value("i", 0)
    start_event = context.Event()
    ready_event = context.Event()
    result_queue = context.Queue()
    worker = context.Process(
        target=_refresh_in_child_process,
        args=(
            str(credential_path),
            start_event,
            ready_event,
            result_queue,
            calls,
        ),
    )
    worker.start()
    try:
        assert ready_event.wait(10), "child process did not initialize"

        def post(*_args, **_kwargs):
            with calls.get_lock():
                calls.value += 1
            time.sleep(0.35)
            return _Response(
                {
                    "access_token": "access-rotated",
                    "refresh_token": "refresh-rotated",
                    "scope": "openid chatgpt.tokens.use.direct",
                    "expires_in": 3600,
                }
            )

        monkeypatch.setattr(oauth.requests, "post", post)
        start_event.set()
        parent_token = oauth.get_chatgpt_access_token()
        child_ok, child_token = result_queue.get(timeout=10)
        worker.join(timeout=10)

        assert worker.exitcode == 0
        assert child_ok is True, child_token
        assert parent_token == child_token == "access-rotated"
        assert calls.value == 1
        assert oauth._read_record_unlocked()["refresh_token"] == "refresh-rotated"
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        result_queue.close()


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
            False,
        ),
    ],
)
def test_account_status_distinguishes_registration_session_and_direct_scope(
    monkeypatch, record, connected, session, scope, reauth
):
    monkeypatch.setattr(
        oauth, "_read_store_unlocked", lambda: oauth._normalize_store(record)
    )

    status = oauth.chatgpt_account_status()

    assert status["client_id_registered"] is bool(record.get("client_id"))
    assert status["connected"] is connected
    assert status["oauth_session_present"] is session
    assert status["direct_scope_granted"] is scope
    assert status["reauth_required"] is reauth


def test_account_profiles_keep_separate_credentials_and_can_be_switched(
    no_credential_file_lock,
):
    store = {
        "schema_version": oauth._CREDENTIAL_STORE_VERSION,
        "ext_agent_host_id": "urn:uuid:host",
        "active_profile_id": "profile-a",
        "profiles": {
            "profile-a": {
                "client_id": "client-a",
                "subject": "subject-a",
                "email": "a@example.test",
                "access_token": "access-a",
                "refresh_token": "refresh-a",
                "scopes": ["chatgpt.tokens.use.direct"],
            },
            "profile-b": {
                "client_id": "client-b",
                "subject": "subject-b",
                "email": "b@example.test",
                "access_token": "access-b",
                "refresh_token": "refresh-b",
                "scopes": ["chatgpt.tokens.use.direct"],
            },
        },
    }
    oauth._write_store_unlocked(store)

    status = oauth.set_active_chatgpt_account("profile-b")

    assert status["active_profile_id"] == "profile-b"
    assert status["email"] == "b@example.test"
    assert status["connected"] is True
    persisted = oauth._read_store_unlocked()
    assert persisted["profiles"]["profile-a"]["refresh_token"] == "refresh-a"
    assert persisted["profiles"]["profile-b"]["refresh_token"] == "refresh-b"


def test_profile_pinned_refresh_updates_only_that_account_without_switching_active(
    monkeypatch, no_credential_file_lock
):
    oauth._write_store_unlocked(
        {
            "schema_version": oauth._CREDENTIAL_STORE_VERSION,
            "ext_agent_host_id": "urn:uuid:host",
            "active_profile_id": "profile-b",
            "profiles": {
                "profile-a": {
                    "client_id": "client-a",
                    "subject": "subject-a",
                    "access_token": "access-a-expired",
                    "refresh_token": "refresh-a",
                    "expires_at": 0,
                    "scopes": ["openid", "chatgpt.tokens.use.direct"],
                },
                "profile-b": {
                    "client_id": "client-b",
                    "subject": "subject-b",
                    "access_token": "access-b",
                    "refresh_token": "refresh-b",
                    "expires_at": 4_000_000_000,
                    "scopes": ["openid", "chatgpt.tokens.use.direct"],
                },
            },
        }
    )
    monkeypatch.setattr(
        oauth,
        "_discover",
        lambda: {"token_endpoint": "https://auth.openai.com/oauth/token"},
    )
    request = {}
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda url, **kwargs: (
            request.update(url=url, **kwargs)
            or _Response(
                {
                    "access_token": "access-a-rotated",
                    "refresh_token": "refresh-a-rotated",
                    "scope": "openid chatgpt.tokens.use.direct",
                    "expires_in": 3600,
                }
            )
        ),
    )

    token = oauth.get_chatgpt_access_token(profile_id="profile-a")

    assert token == "access-a-rotated"
    assert request["data"]["client_id"] == "client-a"
    assert request["data"]["refresh_token"] == "refresh-a"
    persisted = oauth._read_store_unlocked()
    assert persisted["active_profile_id"] == "profile-b"
    assert persisted["profiles"]["profile-a"]["refresh_token"] == "refresh-a-rotated"
    assert persisted["profiles"]["profile-b"]["refresh_token"] == "refresh-b"


def test_new_account_registration_does_not_replace_existing_profile(
    no_credential_file_lock,
):
    oauth._write_store_unlocked(
        {
            "schema_version": oauth._CREDENTIAL_STORE_VERSION,
            "ext_agent_host_id": "urn:uuid:host",
            "active_profile_id": "profile-a",
            "profiles": {
                "profile-a": {
                    "client_id": "client-a",
                    "subject": "subject-a",
                    "email": "a@example.test",
                    "refresh_token": "refresh-a",
                }
            },
        }
    )
    pending = _pending_login(
        existing_record={"ext_agent_host_id": "urn:uuid:host"},
        profile_id="profile-b",
    )

    pending._save_registration("client-b")

    store = oauth._read_store_unlocked()
    assert store["active_profile_id"] == "profile-a"
    assert store["profiles"]["profile-a"]["client_id"] == "client-a"
    assert store["profiles"]["profile-a"]["refresh_token"] == "refresh-a"
    assert store["profiles"]["profile-b"] == {"client_id": "client-b"}


def test_credential_path_uses_platform_specific_application_data_roots(
    monkeypatch, tmp_path, isolate_oauth_credential_file
):
    _isolated, native_path = isolate_oauth_credential_file
    monkeypatch.setattr(oauth, "_credential_path", native_path)
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

    monkeypatch.setattr(oauth, "_write_store_unlocked", write_new_record)

    migrated = oauth._read_record_unlocked()
    assert migrated["client_id"] == record["client_id"]
    assert migrated["access_token"] == record["access_token"]
    assert len(writes) == 1
    assert writes[0]["schema_version"] == oauth._CREDENTIAL_STORE_VERSION
    assert len(writes[0]["profiles"]) == 1
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
    assert "prompt" not in params
    pending.cancel()


def test_add_account_registers_separately_and_reconsent_is_explicit(monkeypatch):
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
            "_profile_id": "profile-a",
            "client_id": "client-a",
            "subject": "account-a",
            "email": "a@example.test",
            "id_token": "id-token-a",
        },
    )
    monkeypatch.setattr(oauth, "ThreadingHTTPServer", lambda address, handler: server)

    added = oauth.begin_chatgpt_login(add_account=True)
    add_params = parse_qs(urlsplit(added.authorization_url).query)
    assert add_params["client_id"] == [oauth._DYNAMIC_CLIENT_ID]
    assert "prompt" not in add_params
    assert added.profile_id != "profile-a"
    added.cancel()

    reconsent = oauth.begin_chatgpt_login(request_plan_usage=True)
    consent_params = parse_qs(urlsplit(reconsent.authorization_url).query)
    assert consent_params["client_id"] == ["client-a"]
    assert consent_params["prompt"] == ["consent"]
    assert consent_params["id_token_hint"] == ["id-token-a"]
    reconsent.cancel()


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


def test_login_without_plan_scope_preserves_identity_but_disables_usage(
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
        lambda *_args, **_kwargs: {
            "sub": "account-no-consent",
            "email": "no-consent@example.test",
        },
    )

    result = pending.complete()

    assert result["identity_connected"] is True
    assert result["connected"] is False
    assert result["plan_usage_authorized"] is False
    assert result["email"] == "no-consent@example.test"
    record = oauth._read_record_unlocked()
    assert record["subject"] == "account-no-consent"
    assert record["refresh_token"] == "refresh"
    assert record["scopes"] == []


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
        "scopes": ["openid", "chatgpt.tokens.use.direct"],
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
    monkeypatch.setattr(
        oauth,
        "_read_store_unlocked",
        lambda: oauth._normalize_store(saved[-1]),
    )
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

    with pytest.raises(oauth.ChatGPTOAuthError, match="尚未授權方案使用"):
        oauth.get_chatgpt_access_token()

    assert saved == []


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

    with pytest.raises(oauth.ChatGPTOAuthError, match="尚未授權方案使用"):
        oauth.get_chatgpt_access_token()

    assert saved == []


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
