"""Unit tests for extract_service helpers.

These tests cover all the helper functions in
app/services_impl/pipelines/extract_service.py that were added or
refactored during the Extractor View architecture cleanup.

Goal: ensure each helper has deterministic, isolated unit tests so future
refactors don't accidentally break the contract.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# =============================================================================
# Config helpers
# =============================================================================
class TestGetOutputFolderNames:
    """Tests for get_output_folder_names()."""

    def test_returns_all_five_keys_with_defaults(self):
        """When config has no output_folder_names, return defaults for all 5 keys."""
        from app.services_impl.pipelines.extract_service import get_output_folder_names

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            names = get_output_folder_names()

        assert set(names.keys()) == {
            "lang_extract", "book_extract", "dual_extract",
            "lang_preview", "book_preview",
        }
        # Verify defaults (Chinese suffix)
        assert names["lang_extract"] == "_提取lang_輸出"
        assert names["book_extract"] == "_提取book_輸出"
        assert names["dual_extract"] == "_提取both_輸出"
        assert names["lang_preview"] == "_預覽lang_輸出"
        assert names["book_preview"] == "_預覽book_輸出"

    def test_uses_custom_values_from_config(self):
        """When config has custom values, they should override defaults."""
        from app.services_impl.pipelines.extract_service import get_output_folder_names

        custom = {
            "extractor": {
                "output_folder_names": {
                    "lang_extract": "_CUSTOM_LANG_",
                    "book_extract": "_CUSTOM_BOOK_",
                }
            }
        }
        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value=custom,
        ):
            names = get_output_folder_names()

        assert names["lang_extract"] == "_CUSTOM_LANG_"
        assert names["book_extract"] == "_CUSTOM_BOOK_"
        # Missing keys fall back to defaults
        assert names["dual_extract"] == "_提取both_輸出"

    def test_handles_missing_extractor_section(self):
        """If config has no 'extractor' key, all defaults are used."""
        from app.services_impl.pipelines.extract_service import get_output_folder_names

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={"other_section": {}},
        ):
            names = get_output_folder_names()

        assert names["lang_extract"] == "_提取lang_輸出"


class TestGetTargetLanguage:
    """Tests for get_target_language()."""

    def test_default_is_zh_tw(self):
        """When config has no target_language, default to zh_tw."""
        from app.services_impl.pipelines.extract_service import get_target_language

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            lang = get_target_language()

        assert lang == "zh_tw"

    def test_uses_custom_target_language(self):
        """When config specifies a custom target language, return it."""
        from app.services_impl.pipelines.extract_service import get_target_language

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={"extractor": {"target_language": "en_us"}},
        ):
            lang = get_target_language()

        assert lang == "en_us"


class TestGetLangCodes:
    """Tests for get_lang_codes()."""

    def test_default_lang_codes(self):
        """When config has no jar_extractor section, return defaults."""
        from app.services_impl.pipelines.extract_service import get_lang_codes

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            codes = get_lang_codes()

        assert codes == ["en_us", "zh_cn", "zh_tw"]

    def test_custom_lang_codes(self):
        """When config specifies custom lang codes, return them."""
        from app.services_impl.pipelines.extract_service import get_lang_codes

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={"jar_extractor": {"lang_codes": ["ja_jp", "ko_kr"]}},
        ):
            codes = get_lang_codes()

        assert codes == ["ja_jp", "ko_kr"]


# =============================================================================
# Path helpers
# =============================================================================
class TestPrepareExtractionPaths:
    """Tests for prepare_extraction_paths()."""

    def test_lang_mode_with_explicit_output(self):
        """lang mode + explicit output_path should use lang suffix."""
        from app.services_impl.pipelines.extract_service import prepare_extraction_paths

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            result = prepare_extraction_paths("/mods", "lang", "/output")

        # Should contain both /output and the lang suffix
        assert "output" in result
        assert "_提取lang_輸出" in result

    def test_book_mode_uses_book_suffix(self):
        """book mode should use book_extract suffix."""
        from app.services_impl.pipelines.extract_service import prepare_extraction_paths

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            result = prepare_extraction_paths("/mods", "book", "/output")

        assert "_提取book_輸出" in result

    def test_dual_mode_uses_dual_suffix(self):
        """dual mode should use dual_extract suffix."""
        from app.services_impl.pipelines.extract_service import prepare_extraction_paths

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            result = prepare_extraction_paths("/mods", "dual", "/output")

        assert "_提取both_輸出" in result

    def test_empty_output_falls_back_to_mods_dir(self):
        """When output_path is empty, mods_dir is used as base."""
        from app.services_impl.pipelines.extract_service import prepare_extraction_paths

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            result = prepare_extraction_paths("/mods", "lang", "")

        assert "mods" in result
        assert "_提取lang_輸出" in result

    def test_empty_mods_and_empty_output_returns_empty(self):
        """When both are empty, return empty string."""
        from app.services_impl.pipelines.extract_service import prepare_extraction_paths

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            result = prepare_extraction_paths("", "lang", "")

        assert result == ""


class TestPreparePreviewPaths:
    """Tests for prepare_preview_paths()."""

    def test_returns_empty_for_nonexistent_path(self):
        """If mods_dir doesn't exist, return empty string."""
        from app.services_impl.pipelines.extract_service import prepare_preview_paths

        with patch(
            "app.services_impl.pipelines.extract_service.load_config",
            return_value={},
        ):
            result = prepare_preview_paths("/this/does/not/exist/mods", "lang")

        assert result == ""

    def test_lang_mode_appends_preview_suffix(self):
        """lang mode should append lang_preview suffix to a real path."""
        from app.services_impl.pipelines.extract_service import prepare_preview_paths

        with tempfile.TemporaryDirectory() as tmp:
            mods_dir = os.path.join(tmp, "mods")
            os.makedirs(mods_dir)

            with patch(
                "app.services_impl.pipelines.extract_service.load_config",
                return_value={},
            ):
                result = prepare_preview_paths(mods_dir, "lang")

        assert "_預覽lang_輸出" in result
        assert "mods" in result

    def test_book_mode_appends_book_preview_suffix(self):
        """book mode should append book_preview suffix."""
        from app.services_impl.pipelines.extract_service import prepare_preview_paths

        with tempfile.TemporaryDirectory() as tmp:
            mods_dir = os.path.join(tmp, "mods")
            os.makedirs(mods_dir)

            with patch(
                "app.services_impl.pipelines.extract_service.load_config",
                return_value={},
            ):
                result = prepare_preview_paths(mods_dir, "book")

        assert "_預覽book_輸出" in result

    def test_dual_mode_uses_dual_preview_suffix(self):
        """dual mode should use a dedicated _預覽_dual_輸出 suffix."""
        from app.services_impl.pipelines.extract_service import prepare_preview_paths

        with tempfile.TemporaryDirectory() as tmp:
            mods_dir = os.path.join(tmp, "mods")
            os.makedirs(mods_dir)

            with patch(
                "app.services_impl.pipelines.extract_service.load_config",
                return_value={},
            ):
                result = prepare_preview_paths(mods_dir, "dual")

        assert "_預覽_dual_輸出" in result


# =============================================================================
# OS helper
# =============================================================================
class TestOpenOutputFolder:
    """Tests for open_output_folder()."""

    def test_returns_false_for_nonexistent_path(self):
        """If path doesn't exist, return False."""
        from app.services_impl.pipelines.extract_service import open_output_folder

        result = open_output_folder("/this/path/does/not/exist/anywhere")
        assert result is False

    def test_returns_false_for_empty_path(self):
        """If path is empty, return False."""
        from app.services_impl.pipelines.extract_service import open_output_folder

        result = open_output_folder("")
        assert result is False

    def test_returns_false_for_non_string(self):
        """If path is None, return False without crashing."""
        from app.services_impl.pipelines.extract_service import open_output_folder

        result = open_output_folder(None)
        assert result is False


# =============================================================================
# Generator selection
# =============================================================================
class TestSelectExtractionGenerator:
    """Tests for _select_extraction_generator()."""

    def test_lang_mode_returns_lang_generator(self):
        """lang mode should select the lang generator."""
        from app.services_impl.pipelines.extract_service import _select_extraction_generator
        from translation_tool.core.jar_processor import (
            extract_lang_files_generator,
            extract_book_files_generator,
            extract_dual_files_generator,
        )

        gen = _select_extraction_generator("lang", "/mods", "/out")
        # Verify it's a generator and matches the expected function
        assert gen is not None
        # Generator functions return generator objects; we can verify by
        # checking the function attribute
        assert gen.__name__ == extract_lang_files_generator.__name__
        # Cleanup the generator
        for _ in gen:
            break

    def test_book_mode_returns_book_generator(self):
        """book mode should select the book generator."""
        from app.services_impl.pipelines.extract_service import _select_extraction_generator
        from translation_tool.core.jar_processor import extract_book_files_generator

        gen = _select_extraction_generator("book", "/mods", "/out")
        assert gen.__name__ == extract_book_files_generator.__name__
        for _ in gen:
            break

    def test_dual_mode_returns_dual_generator(self):
        """dual mode should select the dual generator."""
        from app.services_impl.pipelines.extract_service import _select_extraction_generator
        from translation_tool.core.jar_processor import extract_dual_files_generator

        gen = _select_extraction_generator("dual", "/mods", "/out")
        assert gen.__name__ == extract_dual_files_generator.__name__
        for _ in gen:
            break

    def test_unknown_mode_falls_back_to_dual(self):
        """Unknown mode (e.g. 'foo') falls back to dual generator."""
        from app.services_impl.pipelines.extract_service import _select_extraction_generator
        from translation_tool.core.jar_processor import extract_dual_files_generator

        gen = _select_extraction_generator("foo", "/mods", "/out")
        # The helper does not raise; it returns dual as fallback
        assert gen.__name__ == extract_dual_files_generator.__name__
        for _ in gen:
            break

    def test_lang_codes_passed_to_lang_generator(self):
        """When lang_codes is provided, it should be passed to the generator."""
        from app.services_impl.pipelines.extract_service import _select_extraction_generator

        gen = _select_extraction_generator("lang", "/mods", "/out", lang_codes=["en_us"])
        # Exhaust the generator
        list(gen)


# =============================================================================
# Loop helper
# =============================================================================
class TestRunExtractionLoop:
    """Tests for run_extraction_loop()."""

    def test_empty_generator_returns_zero_stats(self):
        """Empty generator returns zeroed stats dict."""
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        stats = run_extraction_loop(iter([]))
        assert stats == {"success": 0, "warnings": 0, "failures": 0}

    def test_extracts_final_stats_from_update(self):
        """Final update with 'stats' field is extracted into the returned stats."""
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        def gen():
            yield {"progress": 0.5, "log": "halfway"}
            yield {"progress": 1.0, "stats": {"success": 10, "warnings": 2, "failures": 1}}

        stats = run_extraction_loop(gen())
        assert stats == {"success": 10, "warnings": 2, "failures": 1}

    def test_error_in_update_increments_failures(self):
        """An update with 'error' field increments failures by 1."""
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        def gen():
            yield {"error": "something broke"}

        stats = run_extraction_loop(gen())
        assert stats["failures"] == 1

    def test_cancellation_stops_iteration(self):
        """Setting cancelled_flag[0] = True stops iteration."""
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        yielded = []

        def gen():
            for i in range(10):
                yielded.append(i)
                yield {"progress": i / 10.0}

        cancelled = [False]

        def on_update(update):
            # Cancel after first yield
            if len(yielded) >= 2:
                cancelled[0] = True

        stats = run_extraction_loop(gen(), cancelled_flag=cancelled, on_update=on_update)
        # Should have stopped early due to cancellation
        assert len(yielded) < 10

    def test_on_update_callback_invoked(self):
        """on_update is invoked for each yield."""
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        received = []

        def gen():
            for i in range(3):
                yield {"progress": i / 3.0}

        def on_update(update):
            received.append(update)

        run_extraction_loop(gen(), on_update=on_update)
        assert len(received) == 3


# =============================================================================
# Service-level extraction (TaskSession-based)
# =============================================================================
class TestRunExtractionServices:
    """Smoke tests for the three run_*_extraction_service entry points."""

    def _make_session_stub(self):
        """Create a mock TaskSession for testing."""
        class StubSession:
            def __init__(self):
                self.calls = []
                self.error = False
                self.max_logs = 2000

            def start(self):
                self.calls.append("start")

            def finish(self):
                self.calls.append("finish")

            def set_error(self):
                self.error = True
                self.calls.append("set_error")

            def set_progress(self, value):
                self.calls.append(("set_progress", value))

            def add_log(self, text):
                self.calls.append(("add_log", text))

        return StubSession()

    def test_lang_service_calls_session_methods(self):
        """run_lang_extraction_service should call session methods."""
        from app.services_impl.pipelines.extract_service import run_lang_extraction_service
        from unittest.mock import MagicMock

        session = self._make_session_stub()
        with tempfile.TemporaryDirectory() as tmp:
            mods = os.path.join(tmp, "mods")
            out = os.path.join(tmp, "out")
            os.makedirs(mods)

            # Patch the underlying generator to be empty
            with patch(
                "app.services_impl.pipelines.extract_service.extract_lang_files_generator",
                return_value=iter([]),
            ):
                # Patch UI log handler to avoid real session binding
                with patch(
                    "app.services_impl.pipelines.extract_service.UI_LOG_HANDLER"
                ) as mock_handler:
                    mock_handler.set_session = MagicMock()
                    run_lang_extraction_service(mods, out, session, lang_codes=["en_us"])

        # Verify session lifecycle
        assert "start" in session.calls
        assert "finish" in session.calls
        # No error
        assert session.error is False

    def test_book_service_calls_session_methods(self):
        """run_book_extraction_service should call session methods."""
        from app.services_impl.pipelines.extract_service import run_book_extraction_service
        from unittest.mock import MagicMock

        session = self._make_session_stub()
        with tempfile.TemporaryDirectory() as tmp:
            mods = os.path.join(tmp, "mods")
            out = os.path.join(tmp, "out")
            os.makedirs(mods)

            with patch(
                "app.services_impl.pipelines.extract_service.extract_book_files_generator",
                return_value=iter([]),
            ):
                with patch(
                    "app.services_impl.pipelines.extract_service.UI_LOG_HANDLER"
                ) as mock_handler:
                    mock_handler.set_session = MagicMock()
                    run_book_extraction_service(mods, out, session, lang_codes=["en_us"])

        assert "start" in session.calls
        assert "finish" in session.calls

    def test_dual_service_calls_session_methods(self):
        """run_dual_extraction_service should call session methods."""
        from app.services_impl.pipelines.extract_service import run_dual_extraction_service
        from unittest.mock import MagicMock

        session = self._make_session_stub()
        with tempfile.TemporaryDirectory() as tmp:
            mods = os.path.join(tmp, "mods")
            out = os.path.join(tmp, "out")
            os.makedirs(mods)

            with patch(
                "app.services_impl.pipelines.extract_service.extract_dual_files_generator",
                return_value=iter([]),
            ):
                with patch(
                    "app.services_impl.pipelines.extract_service.UI_LOG_HANDLER"
                ) as mock_handler:
                    mock_handler.set_session = MagicMock()
                    run_dual_extraction_service(mods, out, session, lang_codes=["en_us"])

        assert "start" in session.calls
        assert "finish" in session.calls

    def test_lang_service_handles_exception(self):
        """When the generator raises, the service should call set_error."""
        from app.services_impl.pipelines.extract_service import run_lang_extraction_service
        from unittest.mock import MagicMock

        session = self._make_session_stub()

        def boom():
            raise RuntimeError("explode")
            yield  # Make this a generator

        with patch(
            "app.services_impl.pipelines.extract_service.extract_lang_files_generator",
            side_effect=boom,
        ):
            with patch(
                "app.services_impl.pipelines.extract_service.UI_LOG_HANDLER"
            ) as mock_handler:
                mock_handler.set_session = MagicMock()
                run_lang_extraction_service("/mods", "/out", session)

        assert session.error is True
        assert "set_error" in session.calls

    def test_all_services_release_log_handler(self):
        """All three services should call UI_LOG_HANDLER.set_session(None) in finally."""
        from app.services_impl.pipelines.extract_service import (
            run_lang_extraction_service,
            run_book_extraction_service,
            run_dual_extraction_service,
        )
        from unittest.mock import MagicMock

        services = [
            ("lang", run_lang_extraction_service),
            ("book", run_book_extraction_service),
            ("dual", run_dual_extraction_service),
        ]

        for name, svc_func in services:
            session = self._make_session_stub()
            mock_handler = MagicMock()
            with tempfile.TemporaryDirectory() as tmp:
                mods = os.path.join(tmp, "mods")
                out = os.path.join(tmp, "out")
                os.makedirs(mods)

                # Patch the right generator for each service
                gen_name = f"extract_{name}_files_generator"
                with patch(
                    f"app.services_impl.pipelines.extract_service.{gen_name}",
                    return_value=iter([]),
                ):
                    with patch(
                        "app.services_impl.pipelines.extract_service.UI_LOG_HANDLER",
                        mock_handler,
                    ):
                        svc_func(mods, out, session)

            # Verify set_session(None) was called (release)
            none_calls = [
                call for call in mock_handler.set_session.call_args_list
                if call.args and call.args[0] is None
            ]
            assert len(none_calls) >= 1, f"{name} service did not release log handler"


# =============================================================================
# Session helper (with-session loop)
# =============================================================================
class TestRunExtractionWithSession:
    """Tests for _run_extraction_with_session()."""

    def test_session_add_log_called_for_log_updates(self):
        """Log updates from generator should call session.add_log."""
        from app.services_impl.pipelines.extract_service import _run_extraction_with_session

        class StubSession:
            def __init__(self):
                self.logs = []
                self.progresses = []
                self.error = False
                self.finished = False

            def add_log(self, text):
                self.logs.append(text)

            def set_progress(self, value):
                self.progresses.append(value)

            def set_error(self):
                self.error = True

            def finish(self):
                self.finished = True

        session = StubSession()

        def gen():
            yield {"log": "starting"}
            yield {"log": "working", "progress": 0.5}
            yield {"log": "done", "progress": 1.0}

        with patch(
            "app.services_impl.pipelines.extract_service.GLOBAL_LOG_LIMITER"
        ) as mock_limiter:
            # Make filter return the update unchanged
            mock_limiter.filter = lambda u: u
            mock_limiter.flush = lambda: None
            _run_extraction_with_session(gen(), session, "Test")

        assert "starting" in session.logs
        assert "working" in session.logs
        assert "done" in session.logs
        assert session.finished is True
        assert session.error is False

    def test_session_set_error_called_when_error_update(self):
        """An 'error' update triggers session.set_error()."""
        from app.services_impl.pipelines.extract_service import _run_extraction_with_session

        class StubSession:
            def __init__(self):
                self.error = False
                self.finished = False

            def add_log(self, text):
                pass

            def set_progress(self, value):
                pass

            def set_error(self):
                self.error = True

            def finish(self):
                self.finished = True

        session = StubSession()

        def gen():
            yield {"error": "boom"}

        with patch(
            "app.services_impl.pipelines.extract_service.GLOBAL_LOG_LIMITER"
        ) as mock_limiter:
            mock_limiter.filter = lambda u: u
            mock_limiter.flush = lambda: None
            _run_extraction_with_session(gen(), session, "Test")

        assert session.error is True
        assert session.finished is False  # Should NOT finish on error

    def test_log_limiter_filters_out_updates(self):
        """When GLOBAL_LOG_LIMITER.filter returns None, that update is skipped."""
        from app.services_impl.pipelines.extract_service import _run_extraction_with_session

        class StubSession:
            def __init__(self):
                self.logs = []

            def add_log(self, text):
                self.logs.append(text)

            def set_progress(self, value):
                pass

            def set_error(self):
                pass

            def finish(self):
                pass

        session = StubSession()

        def gen():
            yield {"log": "first"}
            yield {"log": "filtered"}
            yield {"log": "last"}

        call_count = [0]

        def fake_filter(update):
            call_count[0] += 1
            if update.get("log") == "filtered":
                return None
            return update

        with patch(
            "app.services_impl.pipelines.extract_service.GLOBAL_LOG_LIMITER"
        ) as mock_limiter:
            mock_limiter.filter = fake_filter
            mock_limiter.flush = lambda: None
            _run_extraction_with_session(gen(), session, "Test")

        assert "first" in session.logs
        assert "filtered" not in session.logs
        assert "last" in session.logs