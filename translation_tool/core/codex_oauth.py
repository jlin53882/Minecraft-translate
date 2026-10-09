"""Official Sign in with ChatGPT flow for local/open-source clients.

OAuth credentials are kept separately from ``config.json``. On Windows the
credential record is protected with the current user's DPAPI; other platforms
use a private application-data file with owner-only permissions.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import requests

from translation_tool.utils.ui_mirror import run_in_context

_ISSUER = "https://auth.openai.com"
_DISCOVERY_URL = f"{_ISSUER}/.well-known/openid-configuration"
_DYNAMIC_CLIENT_ID = "dynamic_agent_client"
_RESOURCE = "https://api.openai.com/v1"
_SCOPES = (
    "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
)
_APP_NAME = "Minecraft Translate"
_CALLBACK_PATH = "/auth/callback"
_LOGIN_TIMEOUT_SEC = 300
_REFRESH_SKEW_SEC = 120
_DATA_MAGIC = b"MTCHATGPT1\0"
_credential_mutex = threading.RLock()


class ChatGPTOAuthError(RuntimeError):
    """A safe, user-facing OAuth or credential-store failure."""


@dataclass(frozen=True)
class ChatGPTModel:
    slug: str
    display_name: str


def _credential_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "MinecraftTranslate" / "chatgpt-oauth.dat"


def _legacy_macos_credential_path() -> Path | None:
    """Return the credential path used before macOS got its native app-data path."""
    if sys.platform != "darwin":
        return None
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    legacy = root / "MinecraftTranslate" / "chatgpt-oauth.dat"
    return None if legacy == _credential_path() else legacy


def _validate_legacy_macos_credential_file(path: Path) -> None:
    """Refuse legacy credential migration unless the source file is private."""
    try:
        if path.is_symlink():
            raise ChatGPTOAuthError("舊版 ChatGPT 登入資料是符號連結，拒絕遷移。")
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ChatGPTOAuthError("無法安全檢查舊版 ChatGPT 登入資料。") from exc
    if mode & 0o077:
        raise ChatGPTOAuthError(
            "舊版 ChatGPT 登入資料的檔案權限不安全；請先將權限設為僅限目前使用者讀取。"
        )


def _dpapi_transform(payload: bytes, *, protect: bool) -> bytes:
    """Encrypt/decrypt bytes with Windows DPAPI scoped to the current user."""
    if os.name != "nt":
        raise ChatGPTOAuthError("目前平台不支援 Windows DPAPI。")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_ulong),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    source_buffer = ctypes.create_string_buffer(payload)
    source = DataBlob(
        len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    result = DataBlob()
    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer,
        ctypes.c_wchar_p,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        blob_pointer,
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer,
        ctypes.c_void_p,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            "Minecraft Translate ChatGPT credentials",
            None,
            None,
            None,
            0x1,  # CRYPTPROTECT_UI_FORBIDDEN
            ctypes.byref(result),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            0x1,  # CRYPTPROTECT_UI_FORBIDDEN
            ctypes.byref(result),
        )
    if not ok:
        raise ChatGPTOAuthError("無法解密 ChatGPT 登入資料，請重新登入。")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _decode_record(raw: bytes) -> dict[str, Any]:
    if not raw.startswith(_DATA_MAGIC):
        raise ChatGPTOAuthError("ChatGPT 登入資料格式無法辨識，請重新登入。")
    payload = raw[len(_DATA_MAGIC) :]
    if os.name == "nt":
        payload = _dpapi_transform(payload, protect=False)
    try:
        record = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChatGPTOAuthError("ChatGPT 登入資料無法讀取，請重新登入。") from exc
    if not isinstance(record, dict):
        raise ChatGPTOAuthError("ChatGPT 登入資料格式無效，請重新登入。")
    return record


def _encode_record(record: dict[str, Any]) -> bytes:
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if os.name == "nt":
        payload = _dpapi_transform(payload, protect=True)
    return _DATA_MAGIC + payload


@contextmanager
def _credential_file_lock():
    path = _credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _read_record_unlocked() -> dict[str, Any]:
    path = _credential_path()
    if not path.exists():
        legacy_path = _legacy_macos_credential_path()
        if legacy_path is None or not (
            legacy_path.exists() or legacy_path.is_symlink()
        ):
            return {}
        _validate_legacy_macos_credential_file(legacy_path)
        try:
            record = _decode_record(legacy_path.read_bytes())
        except OSError as exc:
            raise ChatGPTOAuthError("無法讀取舊版 ChatGPT 登入資料。") from exc
        # The normal writer uses an atomic replacement and owner-only mode on
        # POSIX. Keep the old file until that protected write has succeeded.
        _write_record_unlocked(record)
        try:
            legacy_path.unlink()
        except OSError as exc:
            raise ChatGPTOAuthError(
                "已遷移 ChatGPT 登入資料，但無法清除舊位置的副本。"
            ) from exc
        return record
    try:
        return _decode_record(path.read_bytes())
    except OSError as exc:
        raise ChatGPTOAuthError("無法讀取 ChatGPT 登入資料。") from exc


def _write_record_unlocked(record: dict[str, Any]) -> None:
    path = _credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _encode_record(record)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            if os.name != "nt":
                os.chmod(temporary_path, 0o600)
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ChatGPTOAuthError("無法安全儲存 ChatGPT 登入資料。") from exc


def _load_or_create_host_record() -> dict[str, Any]:
    with _credential_mutex, _credential_file_lock():
        record = _read_record_unlocked()
        if not record.get("ext_agent_host_id"):
            record["ext_agent_host_id"] = f"urn:uuid:{uuid.uuid4()}"
            _write_record_unlocked(record)
        return record


def _record_scopes(record: dict[str, Any]) -> set[str]:
    """Normalize the trusted scopes stored from a token endpoint response."""
    scopes = record.get("scopes")
    if isinstance(scopes, str):
        return set(scopes.split())
    if isinstance(scopes, (list, tuple, set)):
        return {item for item in scopes if isinstance(item, str) and item}
    return set()


def chatgpt_account_status() -> dict[str, Any]:
    # Atomic replace keeps this non-secret status read consistent without waiting
    # behind a token refresh that may hold the cross-process lock during HTTP I/O.
    record = _read_record_unlocked()
    oauth_session_present = bool(
        record.get("refresh_token") and record.get("client_id")
    )
    access_token_present = bool(record.get("access_token"))
    direct_scope_granted = "chatgpt.tokens.use.direct" in _record_scopes(record)
    reauth_required = bool(record.get("reauth_required")) or (
        oauth_session_present and (not direct_scope_granted or not access_token_present)
    )
    connected = (
        oauth_session_present
        and access_token_present
        and direct_scope_granted
        and not reauth_required
    )
    return {
        "connected": connected,
        "email": str(record.get("email") or ""),
        "client_id_registered": bool(record.get("client_id")),
        "oauth_session_present": oauth_session_present,
        "direct_scope_granted": direct_scope_granted,
        "reauth_required": reauth_required,
        "expires_at": record.get("expires_at"),
    }


def _discover() -> dict[str, Any]:
    try:
        response = requests.get(_DISCOVERY_URL, timeout=(10, 20))
        response.raise_for_status()
        metadata = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ChatGPTOAuthError("無法連線到 OpenAI 登入服務，請稍後重試。") from exc
    if not isinstance(metadata, dict) or metadata.get("issuer") != _ISSUER:
        raise ChatGPTOAuthError("OpenAI 登入服務回傳的 issuer 不符合預期。")
    return metadata


def _auth_endpoint(metadata: dict[str, Any], name: str) -> str:
    value = metadata.get(name)
    parts = urlsplit(value) if isinstance(value, str) else None
    if not parts or parts.scheme != "https" or parts.hostname != "auth.openai.com":
        raise ChatGPTOAuthError(f"OpenAI 登入服務缺少安全的 {name}。")
    return value


def _safe_oauth_error(response: requests.Response, action: str) -> ChatGPTOAuthError:
    code = ""
    description = ""
    try:
        body = response.json()
        code = str(body.get("error") or "") if isinstance(body, dict) else ""
        description = (
            str(body.get("error_description") or "") if isinstance(body, dict) else ""
        )
    except ValueError:
        pass
    if code == "invalid_grant":
        message = "登入授權已過期或已使用，請重新開始 ChatGPT 登入。"
    elif code in {"invalid_client", "unauthorized_client"}:
        message = "OpenAI 未接受此登入用戶端，請稍後再試或更新應用程式。"
    elif description:
        message = f"{action}失敗：{description[:240]}"
    else:
        message = f"{action}失敗（HTTP {response.status_code}）。"
    return ChatGPTOAuthError(message)


def _validate_id_token(
    id_token: str, *, client_id: str, nonce: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise ChatGPTOAuthError("OpenAI 回傳的 ID token 格式無效。") from exc
    algorithm = header.get("alg")
    supported = metadata.get("id_token_signing_alg_values_supported") or []
    if (
        not isinstance(algorithm, str)
        or algorithm == "none"
        or algorithm not in supported
    ):
        raise ChatGPTOAuthError("OpenAI ID token 使用不支援的簽章演算法。")
    key_id = header.get("kid")
    if not isinstance(key_id, str):
        raise ChatGPTOAuthError("OpenAI ID token 缺少簽章金鑰識別碼。")
    jwks_uri = metadata.get("jwks_uri")
    parts = urlsplit(jwks_uri) if isinstance(jwks_uri, str) else None
    if not parts or parts.scheme != "https" or parts.hostname != "auth.openai.com":
        raise ChatGPTOAuthError("OpenAI 登入服務缺少安全的 JWKS 網址。")
    try:
        response = requests.get(jwks_uri, timeout=(10, 20))
        response.raise_for_status()
        jwks = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ChatGPTOAuthError("無法驗證 OpenAI ID token 簽章。") from exc
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    jwk = next(
        (
            key
            for key in keys or []
            if isinstance(key, dict) and key.get("kid") == key_id
        ),
        None,
    )
    if not jwk:
        raise ChatGPTOAuthError("找不到驗證 OpenAI ID token 的簽章金鑰。")
    try:
        public_key = jwt.PyJWK.from_dict(jwk, algorithm=algorithm).key
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=[algorithm],
            audience=client_id,
            issuer=_ISSUER,
            options={"require": ["iss", "aud", "exp", "sub", "nonce"]},
        )
    except jwt.PyJWTError as exc:
        raise ChatGPTOAuthError("OpenAI ID token 驗證失敗，請重新登入。") from exc
    if not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise ChatGPTOAuthError("OpenAI ID token nonce 不符，請重新登入。")
    if not claims.get("sub"):
        raise ChatGPTOAuthError("OpenAI ID token 缺少帳號識別碼。")
    return claims


class PendingChatGPTLogin:
    """One PKCE login attempt and its loopback callback listener."""

    def __init__(
        self,
        *,
        server: ThreadingHTTPServer,
        state: str,
        nonce: str,
        verifier: str,
        redirect_uri: str,
        requested_client_id: str,
        existing_record: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        self.server = server
        self.state = state
        self.nonce = nonce
        self.verifier = verifier
        self.redirect_uri = redirect_uri
        self.requested_client_id = requested_client_id
        self.existing_record = existing_record
        self.metadata = metadata
        self.callback_event = threading.Event()
        self.cancelled_event = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._server_closed = False
        self.callback: dict[str, str] | None = None
        self.server_thread = threading.Thread(
            target=run_in_context(server.serve_forever),
            name="chatgpt-oauth-loopback",
            daemon=True,
        )

    def receive_callback(self, callback: dict[str, str]) -> None:
        if self.callback_event.is_set():
            return
        self.callback = callback
        self.callback_event.set()

    def cancel(self) -> None:
        self.cancelled_event.set()
        self.callback_event.set()
        self.stop_server()

    def stop_server(self) -> None:
        """Shut down the callback listener once, safe from its handler thread."""
        with self._shutdown_lock:
            if self.server_thread.is_alive():
                self.server.shutdown()
                self.server_thread.join(timeout=2)
            if not self._server_closed:
                self.server.server_close()
                self._server_closed = True

    def complete(self, timeout: float = _LOGIN_TIMEOUT_SEC) -> dict[str, Any]:
        if not self.callback_event.wait(timeout):
            self.cancel()
            raise ChatGPTOAuthError("登入等待逾時，請重新點選「使用 ChatGPT 登入」。")
        try:
            if self.cancelled_event.is_set():
                raise ChatGPTOAuthError("你已取消 ChatGPT 登入。")
            callback = self.callback or {}
            if not secrets.compare_digest(callback.get("state", ""), self.state):
                raise ChatGPTOAuthError("登入回呼 state 不符，已取消此次登入。")
            if callback.get("error"):
                if callback["error"] == "access_denied":
                    raise ChatGPTOAuthError("你已取消 ChatGPT 登入。")
                raise ChatGPTOAuthError("OpenAI 未完成登入授權，請重試。")
            code = callback.get("code", "")
            if not code:
                raise ChatGPTOAuthError("OpenAI 登入回呼缺少授權碼。")

            callback_client_id = callback.get("client_id", "")
            if self.requested_client_id == _DYNAMIC_CLIENT_ID:
                client_id = callback_client_id
                if not client_id or client_id == _DYNAMIC_CLIENT_ID:
                    raise ChatGPTOAuthError("OpenAI 沒有完成用戶端註冊，請重新登入。")
            else:
                client_id = self.requested_client_id
                if callback_client_id and callback_client_id != client_id:
                    raise ChatGPTOAuthError(
                        "OpenAI 登入回傳了不同的用戶端，已拒絕此次登入。"
                    )

            self._save_registration(client_id)
            token_endpoint = _auth_endpoint(self.metadata, "token_endpoint")
            try:
                response = requests.post(
                    token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "client_id": client_id,
                        "code": code,
                        "code_verifier": self.verifier,
                        "redirect_uri": self.redirect_uri,
                        "resource": _RESOURCE,
                    },
                    headers={"Accept": "application/json"},
                    timeout=(10, 30),
                )
            except requests.RequestException as exc:
                raise ChatGPTOAuthError(
                    "無法交換 ChatGPT 登入授權，請重新登入。"
                ) from exc
            if not response.ok:
                raise _safe_oauth_error(response, "ChatGPT 登入授權交換")
            try:
                tokens = response.json()
            except ValueError as exc:
                raise ChatGPTOAuthError("OpenAI token endpoint 回傳無效資料。") from exc
            if not isinstance(tokens, dict):
                raise ChatGPTOAuthError("OpenAI token endpoint 回傳無效資料。")
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            id_token = tokens.get("id_token")
            # Only the validated token response proves which permissions OpenAI
            # granted. The authorization callback may echo requested scopes.
            granted_scopes = str(tokens.get("scope") or "")
            if not all(
                isinstance(value, str) and value
                for value in (access_token, refresh_token, id_token)
            ):
                raise ChatGPTOAuthError("OpenAI token response 缺少必要的登入憑證。")
            scopes = set(granted_scopes.split())
            if "chatgpt.tokens.use.direct" not in scopes:
                raise ChatGPTOAuthError(
                    "此 ChatGPT 帳號尚未授權使用方案額度；請重新登入並同意方案使用權限。"
                )

            claims = _validate_id_token(
                id_token,
                client_id=client_id,
                nonce=self.nonce,
                metadata=self.metadata,
            )
            previous_subject = self.existing_record.get("subject")
            if (
                previous_subject
                and client_id == self.existing_record.get("client_id")
                and previous_subject != claims.get("sub")
            ):
                raise ChatGPTOAuthError("登入帳號與已選取的 ChatGPT 帳號不同。")

            try:
                expires_in = max(60, int(tokens.get("expires_in", 3600)))
            except (TypeError, ValueError):
                expires_in = 3600
            record = dict(self.existing_record)
            record.update(
                {
                    "ext_agent_host_id": self.existing_record["ext_agent_host_id"],
                    "client_id": client_id,
                    "subject": str(claims["sub"]),
                    "email": str(claims.get("email") or ""),
                    "id_token": id_token,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_type": "Bearer",
                    "expires_at": time.time() + expires_in,
                    "scopes": sorted(scopes),
                    "reauth_required": False,
                    "saved_at": int(time.time()),
                }
            )
            with _credential_mutex, _credential_file_lock():
                _write_record_unlocked(record)
            return chatgpt_account_status()
        finally:
            self.cancel()

    def _save_registration(self, client_id: str) -> None:
        with _credential_mutex, _credential_file_lock():
            current = _read_record_unlocked()
            if current.get("client_id") not in (None, "", client_id):
                raise ChatGPTOAuthError(
                    "已選取另一個 ChatGPT 用戶端，請先中斷連結再登入。"
                )
            current["ext_agent_host_id"] = self.existing_record["ext_agent_host_id"]
            current["client_id"] = client_id
            _write_record_unlocked(current)


def begin_chatgpt_login() -> PendingChatGPTLogin:
    """Start the official dynamic-registration OAuth + PKCE flow."""
    metadata = _discover()
    record = _load_or_create_host_record()
    registered_client_id = str(record.get("client_id") or "")
    requested_client_id = registered_client_id or _DYNAMIC_CLIENT_ID
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    code_challenge = challenge.rstrip(b"=").decode("ascii")

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path != _CALLBACK_PATH:
                self.send_error(404)
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            callback = {
                name: values[0]
                for name, values in query.items()
                if values and name in {"code", "state", "client_id", "scope", "error"}
            }
            pending.receive_callback(callback)
            body = (
                "<!doctype html><html><meta charset='utf-8'><title>Minecraft Translate</title>"
                "<body><p>登入結果已送回 Minecraft Translate。可以關閉此分頁。</p></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(
                target=run_in_context(pending.stop_server), daemon=True
            ).start()

        def log_message(self, _format: str, *args: Any) -> None:
            return  # Never log callback URLs; they contain single-use OAuth codes.

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    except OSError as exc:
        raise ChatGPTOAuthError(
            "無法啟動本機登入回呼，請確認 loopback 網路可用。"
        ) from exc
    server.daemon_threads = True
    host, port = server.server_address
    redirect_uri = f"http://{host}:{port}{_CALLBACK_PATH}"
    pending = PendingChatGPTLogin(
        server=server,
        state=state,
        nonce=nonce,
        verifier=verifier,
        redirect_uri=redirect_uri,
        requested_client_id=requested_client_id,
        existing_record=record,
        metadata=metadata,
    )
    params: dict[str, str] = {
        "client_id": requested_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": _SCOPES,
        "resource": _RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "ext_agent_host_id": str(record["ext_agent_host_id"]),
    }
    if requested_client_id == _DYNAMIC_CLIENT_ID:
        params["agent_name_hint"] = _APP_NAME
    elif record.get("id_token"):
        params["id_token_hint"] = str(record["id_token"])
    if record.get("email"):
        params["login_hint"] = str(record["email"])
    authorization_endpoint = _auth_endpoint(metadata, "authorization_endpoint")
    pending.authorization_url = f"{authorization_endpoint}?{urlencode(params)}"
    pending.server_thread.start()
    return pending


def get_chatgpt_access_token(*, force_refresh: bool = False) -> str:
    """Return a usable access token, serializing rotating-token refreshes."""
    with _credential_mutex, _credential_file_lock():
        record = _read_record_unlocked()
        access_token = str(record.get("access_token") or "")
        refresh_token = str(record.get("refresh_token") or "")
        client_id = str(record.get("client_id") or "")
        if not access_token or not refresh_token or not client_id:
            if record.get("reauth_required") or client_id:
                raise ChatGPTOAuthError(
                    "ChatGPT 登入授權需要更新，請到 API 設定重新登入。"
                )
            raise ChatGPTOAuthError("尚未連結 ChatGPT 帳號；請先在設定頁登入。")
        try:
            expires_at = float(record.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        trusted_scopes = _record_scopes(record)
        has_direct_scope = "chatgpt.tokens.use.direct" in trusted_scopes
        if (
            not force_refresh
            and has_direct_scope
            and not record.get("reauth_required")
            and expires_at > time.time() + _REFRESH_SKEW_SEC
        ):
            return access_token

        metadata = _discover()
        token_endpoint = _auth_endpoint(metadata, "token_endpoint")
        try:
            response = requests.post(
                token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "resource": _RESOURCE,
                },
                headers={"Accept": "application/json"},
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise ChatGPTOAuthError(
                "無法更新 ChatGPT 登入狀態，請檢查網路後重試。"
            ) from exc
        if not response.ok:
            if response.status_code in {400, 401, 403}:
                raise ChatGPTOAuthError("ChatGPT 登入已失效，請重新登入。")
            raise _safe_oauth_error(response, "ChatGPT token 更新")
        try:
            tokens = response.json()
        except ValueError as exc:
            raise ChatGPTOAuthError("OpenAI token endpoint 回傳無效資料。") from exc
        if not isinstance(tokens, dict):
            raise ChatGPTOAuthError("OpenAI token endpoint 回傳無效資料。")
        new_access = tokens.get("access_token")
        new_refresh = tokens.get("refresh_token")
        if not isinstance(new_access, str) or not new_access:
            raise ChatGPTOAuthError("OpenAI token 更新缺少 access token，請重新登入。")
        if not isinstance(new_refresh, str) or not new_refresh:
            raise ChatGPTOAuthError(
                "OpenAI token 更新缺少 replacement refresh token，請重新登入。"
            )
        try:
            expires_in = max(60, int(tokens.get("expires_in", 3600)))
        except (TypeError, ValueError):
            expires_in = 3600
        # A missing scope field inherits the trusted grant under OAuth refresh
        # semantics. An explicit empty/non-string scope is a real downgrade.
        if "scope" in tokens:
            response_scopes = tokens.get("scope")
            granted_scopes = (
                set(response_scopes.split())
                if isinstance(response_scopes, str)
                else set()
            )
        else:
            granted_scopes = trusted_scopes
        direct_scope_granted = "chatgpt.tokens.use.direct" in granted_scopes
        record.update(
            {
                # Persist the replacement refresh token before reporting a
                # scope downgrade; the previous rotating token may be invalid.
                "access_token": new_access if direct_scope_granted else "",
                "refresh_token": new_refresh,
                "expires_at": time.time() + expires_in if direct_scope_granted else 0,
                "scopes": sorted(granted_scopes),
                "reauth_required": not direct_scope_granted,
                "saved_at": int(time.time()),
            }
        )
        _write_record_unlocked(record)
        if not direct_scope_granted:
            raise ChatGPTOAuthError(
                "ChatGPT 方案授權範圍已變更或無法確認；請到 API 設定重新登入後再翻譯。"
            )
        return new_access


def list_chatgpt_models() -> list[ChatGPTModel]:
    """Fetch the signed-in account's current displayable model catalog."""
    token = get_chatgpt_access_token()
    for attempt in range(2):
        try:
            response = requests.get(
                f"{_RESOURCE}/models",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise ChatGPTOAuthError("無法讀取 ChatGPT 帳號可用模型。") from exc
        if response.status_code == 401 and attempt == 0:
            token = get_chatgpt_access_token(force_refresh=True)
            continue
        if not response.ok:
            raise ChatGPTOAuthError(
                f"讀取 ChatGPT 模型失敗（HTTP {response.status_code}）。"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ChatGPTOAuthError("ChatGPT 模型清單格式無效。") from exc
        models = payload.get("models") if isinstance(payload, dict) else None
        return [
            ChatGPTModel(str(item["slug"]), str(item["display_name"]))
            for item in models or []
            if isinstance(item, dict)
            and item.get("visibility") == "list"
            and item.get("slug")
            and item.get("display_name")
        ]
    raise ChatGPTOAuthError("ChatGPT 登入已失效，請重新登入。")


def disconnect_chatgpt_account() -> bool:
    """Revoke the renewable session when possible, then clear local tokens."""
    confirmed = True
    with _credential_mutex, _credential_file_lock():
        record = _read_record_unlocked()
        refresh_token = str(record.get("refresh_token") or "")
        client_id = str(record.get("client_id") or "")
        if refresh_token and client_id:
            for attempt in range(3):
                try:
                    metadata = _discover()
                    endpoint = _auth_endpoint(metadata, "revocation_endpoint")
                    response = requests.post(
                        endpoint,
                        data={
                            "token": refresh_token,
                            "token_type_hint": "refresh_token",
                            "client_id": client_id,
                        },
                        headers={"Accept": "application/json"},
                        timeout=(10, 10),
                    )
                    confirmed = response.status_code == 200
                    retryable = (
                        response.status_code == 429 or response.status_code >= 500
                    )
                except (ChatGPTOAuthError, requests.RequestException):
                    confirmed = False
                    retryable = True
                if confirmed or not retryable or attempt == 2:
                    break
                time.sleep(0.5 * (2**attempt))
        retained = {
            key: record[key]
            for key in ("ext_agent_host_id", "client_id", "subject", "email")
            if record.get(key)
        }
        _write_record_unlocked(retained)
    return confirmed
