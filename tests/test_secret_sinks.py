"""#125：所有會輸出文字的「出口」都不得帶出使用者的 API 金鑰（含非標準格式）。"""

from __future__ import annotations

import json
import logging

import pytest

from translation_tool.utils import redaction

CUSTOM_KEY = "my-private-key-0123456789"  # 不符合 AIza 格式：只靠「已知機密」登錄遮蔽
GOOGLE_KEY = "AIza" + "S" * 35


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
    assert GOOGLE_KEY not in redaction.redact_secrets(f"bad key {GOOGLE_KEY}")


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
        raise RuntimeError(f"upstream said {CUSTOM_KEY}")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "failed %s", (CUSTOM_KEY,), sys.exc_info()
        )
    out = fmt.format(record)
    assert CUSTOM_KEY not in out


def test_log_file_written_by_setup_logging_is_redacted(tmp_path):
    from translation_tool.utils import config_manager as cm

    redaction.register_secrets([CUSTOM_KEY])
    config = {"logging": {"log_level": "INFO", "log_dir": str(tmp_path / "logs")}}
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        cm.setup_logging(config)
        logging.getLogger("t").error("leaked %s and %s", CUSTOM_KEY, GOOGLE_KEY)
        for h in root.handlers:
            h.flush()
        files = list((tmp_path / "logs").rglob("app.log"))
        assert files
        text = files[0].read_text(encoding="utf-8")
        assert "leaked" in text
        assert CUSTOM_KEY not in text and GOOGLE_KEY not in text
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
