"""本機 Codex CLI 登入讀取的針對性測試。"""

import base64
import json
import time

import pytest

from translation_tool.core import codex_cli_auth as auth


def _jwt(exp, account="acct-claim"):
    def enc(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    claims = {
        "exp": exp,
        "https://api.openai.com/auth": {"chatgpt_account_id": account},
        "https://api.openai.com/profile": {"email": "Me@Example.com"},
    }
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def _write(tmp_path, monkeypatch, record):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(json.dumps(record), encoding="utf-8")


def test_loads_valid_chatgpt_login(tmp_path, monkeypatch):
    token = _jwt(time.time() + 3600)
    _write(
        tmp_path,
        monkeypatch,
        {"auth_mode": "chatgpt", "tokens": {"access_token": token, "account_id": "a1"}},
    )
    creds = auth.load_codex_cli_credentials()
    assert creds.access_token == token
    assert creds.account_id == "a1"
    assert creds.email == "me@example.com"


def test_account_id_falls_back_to_token_claim(tmp_path, monkeypatch):
    _write(
        tmp_path,
        monkeypatch,
        {"auth_mode": "chatgpt", "tokens": {"access_token": _jwt(time.time() + 3600)}},
    )
    assert auth.load_codex_cli_credentials().account_id == "acct-claim"


@pytest.mark.parametrize(
    "record",
    [
        {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"},
        {"auth_mode": "chatgpt", "tokens": {}},
        {"auth_mode": "chatgpt", "tokens": {"access_token": "not-a-jwt"}},
    ],
)
def test_rejects_unusable_logins(tmp_path, monkeypatch, record):
    _write(tmp_path, monkeypatch, record)
    with pytest.raises(auth.CodexCLIAuthError):
        auth.load_codex_cli_credentials()


def test_rejects_expired_token(tmp_path, monkeypatch):
    _write(
        tmp_path,
        monkeypatch,
        {"auth_mode": "chatgpt", "tokens": {"access_token": _jwt(time.time() - 5)}},
    )
    with pytest.raises(auth.CodexCLIAuthError, match="過期"):
        auth.load_codex_cli_credentials()


def test_missing_file_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    with pytest.raises(auth.CodexCLIAuthError):
        auth.load_codex_cli_credentials()
