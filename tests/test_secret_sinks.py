"""#125：所有會輸出文字的「出口」都不得帶出使用者的 API 金鑰（含非標準格式）。"""

from __future__ import annotations

import json
import logging

import pytest

from translation_tool.utils import redaction

CUSTOM_KEY = "my-private-key-0123456789"  # 不符合 AIza 格式：只靠「已知機密」登錄遮蔽
GOOGLE_KEY = "AIza" + "S" * 35
AQ_KEY = "AQ.XX"


@pytest.fixture(autouse=True)
def _clean_registry():
    redaction._known_secrets.clear()
    yield
    redaction._known_secrets.clear()


def test_registered_secret_is_masked_in_any_format():
    redaction.register_secrets([CUSTOM_KEY])
    out = redaction.redact_secrets(f"HTTP 403 for key {CUSTOM_KEY} on model")
    assert CUSTOM_KEY not in out and "[REDACTED]" in out


def test_google_format_masked_even_if_not_registered():
    for key in (GOOGLE_KEY, AQ_KEY):
        assert key not in redaction.redact_secrets(f"bad key {key}")
        assert key not in redaction.redact_text(f"bad key {key}")


def test_short_values_are_not_registered_and_normal_text_is_untouched():
    redaction.register_secrets(["abc", "", None, 123])
    assert redaction.known_secrets() == frozenset()
    text = "token: 6000, max_output_tokens=32768, 完成 100%"
    assert redaction.redact_secrets(text) == text  # 不誤傷一般訊息


def test_longer_secret_is_replaced_before_its_substring():
    redaction.register_secrets(["secret-key-AAAA", "secret-key-AAAA-extended"])
    out = redaction.redact_secrets("x secret-key-AAAA-extended y")
    assert "extended" not in out


def test_formatter_masks_message_and_traceback():
    redaction.register_secrets([CUSTOM_KEY])
    fmt = redaction.RedactingFormatter("%(message)s")
    try:
        raise RuntimeError(f"upstream said {CUSTOM_KEY} and {AQ_KEY}")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "t",
            logging.ERROR,
            __file__,
            1,
            "failed %s",
            (CUSTOM_KEY,),
            sys.exc_info(),
        )
    out = fmt.format(record)
    assert CUSTOM_KEY not in out and AQ_KEY not in out


def test_log_file_written_by_setup_logging_is_redacted(tmp_path):
    from translation_tool.utils import config_manager as cm

    redaction.register_secrets([CUSTOM_KEY])
    config = {"logging": {"log_level": "INFO", "log_dir": str(tmp_path / "logs")}}
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        cm.setup_logging(config)
        logging.getLogger("t").error(
            "leaked %s, %s, and %s", CUSTOM_KEY, GOOGLE_KEY, AQ_KEY
        )
        for h in root.handlers:
            h.flush()
        files = list((tmp_path / "logs").rglob("app.log"))
        assert files
        text = files[0].read_text(encoding="utf-8")
        assert "leaked" in text
        assert CUSTOM_KEY not in text and GOOGLE_KEY not in text and AQ_KEY not in text
    finally:
        for h in root.handlers[:]:
            root.removeHandler(h)
            h.close()
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)


def test_ui_log_handler_masks_before_reaching_the_session():
    from translation_tool.utils.ui_logging_handler import UISessionLogHandler

    redaction.register_secrets([CUSTOM_KEY])
    seen = []

    class Session:
        def add_log(self, text, level="info", source="ui"):
            seen.append(text)

    handler = UISessionLogHandler()
    handler.set_session(Session())
    handler.emit(
        logging.LogRecord(
            "t", logging.ERROR, __file__, 1, f"oops {CUSTOM_KEY}", (), None
        )
    )
    assert seen and CUSTOM_KEY not in seen[0]


def test_task_session_add_log_masks_direct_calls():
    from app.tasks.task_session import TaskSession

    redaction.register_secrets([CUSTOM_KEY])
    session = TaskSession()
    session.add_log(f"直接呼叫 add_log：{CUSTOM_KEY}")
    session.add_log(f"Bearer abc.def.ghi 與 {GOOGLE_KEY}")
    text = " | ".join(repr(e) for e in session.snapshot()["logs"])
    assert (
        CUSTOM_KEY not in text and GOOGLE_KEY not in text and "abc.def.ghi" not in text
    )


def test_snackbar_message_is_masked():
    import flet as ft

    from app.ui.snack import show_snack
    from tests.conftest import mock_page

    redaction.register_secrets([CUSTOM_KEY])
    snack = show_snack(mock_page(), f"❌ 發生錯誤：{CUSTOM_KEY}")
    assert isinstance(snack, ft.SnackBar)
    assert CUSTOM_KEY not in snack.content.value


def test_error_log_file_is_masked(tmp_path, monkeypatch):
    from translation_tool.utils import exceptions

    redaction.register_secrets([CUSTOM_KEY])
    monkeypatch.setattr(exceptions, "_resolve_error_log_dir", lambda: tmp_path)
    try:
        raise exceptions.TranslationError(
            f"api failed {CUSTOM_KEY}", {"key": CUSTOM_KEY}
        )
    except exceptions.TranslationError as exc:
        exceptions._log_error_to_file(exc, "probe")
    content = next(tmp_path.glob("errors_*.log")).read_text(encoding="utf-8")
    assert "api failed" in content and CUSTOM_KEY not in content


def test_load_config_registers_user_keys_but_not_placeholders(tmp_path):
    from translation_tool.utils import config_manager as cm

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "lm_translator": {
                    "keys": [GOOGLE_KEY, CUSTOM_KEY, "YOUR_GEMINI_API_KEY_1", "short"]
                }
            }
        ),
        encoding="utf-8",
    )
    cm.clear_config_cache()
    try:
        cm.load_config(cfg)
    finally:
        cm.clear_config_cache()
    known = redaction.known_secrets()
    assert GOOGLE_KEY in known and CUSTOM_KEY in known
    assert "YOUR_GEMINI_API_KEY_1" not in known and "short" not in known


# ---- 動態 token（OAuth／第三方格式）出口 -------------------------------------
# 全部是明顯無效的假字串，不是任何真實 credential。
FAKE_OPENAI = "sk-" + "A1b2C3d4" * 4
FAKE_ANTHROPIC = "sk-ant-api03-" + "Zz9Yy8Xx" * 4
FAKE_JWT = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJmYWtlIn0.c2lnbmF0dXJl"
FAKE_BASIC = "Basic dXNlcjpwYXNzd29yZC1mYWtl"
FAKE_REFRESH = "rt_fake_refresh_0123456789abcdef"
FAKE_ACCESS = "at_fake_access_0123456789abcdef"


def test_known_formats_are_masked_everywhere():
    for secret in (FAKE_OPENAI, FAKE_ANTHROPIC, FAKE_JWT):
        for fn in (redaction.redact_secrets, redaction.redact_text):
            out = fn(f"upstream said {secret} (HTTP 401)")
            assert secret not in out and "HTTP 401" in out
    out = redaction.redact_secrets(f"Authorization: {FAKE_BASIC}")
    assert "dXNlcjpw" not in out
    out = redaction.redact_secrets(f"POST body refresh_token={FAKE_REFRESH}&x=1")
    assert FAKE_REFRESH not in out and "x=1" in out
    out = redaction.redact_secrets(f'{{"refresh_token": "{FAKE_REFRESH}"}}')
    assert FAKE_REFRESH not in out


def test_false_positive_controls():
    samples = [
        "sk-learn 與 sk-1 是一般文字",
        "Basic configuration 與 Basic authentication 說明",
        "task-id: abc, risk-assessment, eyJ 開頭但太短",
        "refresh_token 欄位已存在；refresh_token=短",
        "model gpt-4o-mini 完成 100 個 key，token: 6000",
        "an.example.dotted.name 與 file.name.json",
    ]
    for text in samples:
        assert redaction.redact_secrets(text) == text


def test_exception_traceback_masks_dynamic_token():
    fmt = redaction.RedactingFormatter("%(message)s")
    try:
        raise RuntimeError(f"401 for {FAKE_OPENAI} / {FAKE_JWT}")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "call failed", (), sys.exc_info()
        )
    out = fmt.format(record)
    assert FAKE_OPENAI not in out and FAKE_JWT not in out
    assert "call failed" in out and "RuntimeError" in out


def test_snackbar_masks_format_tokens():
    from app.ui.snack import show_snack
    from tests.conftest import mock_page

    snack = show_snack(mock_page(), f"失敗：{FAKE_ANTHROPIC}")
    assert FAKE_ANTHROPIC not in snack.content.value


def test_masking_works_across_threads():
    import threading

    redaction.register_secrets([CUSTOM_KEY])
    results = []

    def worker():
        results.append(redaction.redact_secrets(f"t {CUSTOM_KEY} {FAKE_OPENAI}"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8
    assert all(CUSTOM_KEY not in r and FAKE_OPENAI not in r for r in results)


# ---- OAuth token 取得／更新後登錄 ------------------------------------------------


def test_oauth_error_description_is_masked():
    from translation_tool.core import codex_oauth

    class Resp:
        status_code = 400
        ok = False

        def json(self):
            return {"error": "bad", "error_description": f"echo {FAKE_OPENAI}"}

    err = codex_oauth._safe_oauth_error(Resp(), "ChatGPT token 更新")
    assert FAKE_OPENAI not in str(err) and "ChatGPT token 更新失敗" in str(err)


def test_oauth_exchange_error_masks_the_one_time_authorization_code():
    from translation_tool.core import codex_oauth

    code = "one-time-authorization-code-123456789"

    class Resp:
        status_code = 400
        ok = False

        def json(self):
            return {
                "error": "invalid_grant",
                "error_description": f"invalid code={code}",
            }

    err = codex_oauth._safe_oauth_error(
        Resp(), "ChatGPT 登入授權交換", extra_secrets=(code,)
    )
    assert code not in str(err)


def test_oauth_refresh_registers_new_tokens(monkeypatch):
    from contextlib import nullcontext

    from translation_tool.core import codex_oauth

    record = {
        "client_id": "cid",
        "access_token": FAKE_ACCESS,
        "refresh_token": FAKE_REFRESH,
        "expires_at": 0,
        "scopes": ["chatgpt.tokens.use.direct"],
    }
    new_access = "at_rotated_access_ABCDEFGHIJKL"
    new_refresh = "rt_rotated_refresh_ABCDEFGHIJKL"

    class Resp:
        ok = True
        status_code = 200

        def json(self):
            return {
                "access_token": new_access,
                "refresh_token": new_refresh,
                "expires_in": 3600,
            }

    monkeypatch.setattr(codex_oauth, "_read_record_unlocked", lambda: dict(record))
    monkeypatch.setattr(codex_oauth, "_write_record_unlocked", lambda r, **k: None)
    monkeypatch.setattr(codex_oauth, "_discover", dict)
    monkeypatch.setattr(codex_oauth, "_auth_endpoint", lambda m, n: "https://x.invalid")
    monkeypatch.setattr(codex_oauth.requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(codex_oauth, "_credential_file_lock", nullcontext)

    token = codex_oauth.get_chatgpt_access_token(force_refresh=True)
    assert token == new_access
    known = redaction.known_secrets()
    assert {FAKE_ACCESS, FAKE_REFRESH, new_access, new_refresh} <= known
    out = redaction.redact_secrets(f"x {new_access} y {new_refresh} z {FAKE_REFRESH}")
    assert new_access not in out and new_refresh not in out and FAKE_REFRESH not in out
