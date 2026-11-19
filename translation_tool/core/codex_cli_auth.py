"""讀取本機 Codex CLI 的 ChatGPT 登入，供訂閱分享額度用盡時備援使用。

只「讀取」`$CODEX_HOME/auth.json`（預設 `~/.codex/auth.json`）裡的 access token 與
account id，不負責更新：refresh token 是單次輪替的，由本程式更新會讓 Codex CLI
自己的登入失效，所以 token 過期時只提示使用者重新執行 Codex CLI。
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from translation_tool.utils.redaction import register_secrets

# access token 剩餘時間低於此秒數就視為過期，避免請求途中失效。
_EXPIRY_SKEW_SEC = 120


class CodexCLIAuthError(RuntimeError):
    """本機 Codex CLI 登入不存在或無法使用。"""


@dataclass(frozen=True)
class CodexCLICredentials:
    access_token: str
    account_id: str


def codex_auth_path() -> Path:
    home = os.environ.get("CODEX_HOME", "").strip()
    base = Path(home).expanduser() if home else Path.home() / ".codex"
    return base / "auth.json"


def _jwt_claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def load_codex_cli_credentials() -> CodexCLICredentials:
    """回傳可用的 Codex CLI 登入；不可用時拋出 CodexCLIAuthError。"""
    path = codex_auth_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CodexCLIAuthError(
            "找不到 Codex CLI 登入（~/.codex/auth.json）。"
        ) from exc
    except (OSError, ValueError) as exc:
        raise CodexCLIAuthError("Codex CLI 登入檔無法讀取。") from exc
    if not isinstance(data, dict) or data.get("auth_mode") != "chatgpt":
        raise CodexCLIAuthError("Codex CLI 目前不是使用 ChatGPT 帳號登入。")
    tokens = data.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    access_token = str(tokens.get("access_token") or "")
    if not access_token:
        raise CodexCLIAuthError("Codex CLI 登入檔沒有 access token。")
    register_secrets([access_token])
    claims = _jwt_claims(access_token)
    try:
        expires_at = float(claims.get("exp") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at <= time.time() + _EXPIRY_SKEW_SEC:
        raise CodexCLIAuthError(
            "Codex CLI 的登入已過期；請先在終端機執行一次 codex 讓它更新登入。"
        )
    auth_claims = claims.get("https://api.openai.com/auth")
    claim_account = (
        auth_claims.get("chatgpt_account_id") if isinstance(auth_claims, dict) else ""
    )
    account_id = str(tokens.get("account_id") or claim_account or "")
    return CodexCLICredentials(access_token=access_token, account_id=account_id)
