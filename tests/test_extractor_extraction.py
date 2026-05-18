"""
Unit tests for jar_processor_extract yield format.
Tests that extract progress yields include current/total fields.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import shutil


class TestExtractionProgressYield:
    """Test extraction generator yields progress with current/total."""

    def test_extract_lang_generator_yields_with_current_and_total(self, tmp_path):
        """Test extract_lang_files_generator yields dicts with current and total."""
        from translation_tool.core.jar_processor import extract_lang_files_generator

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"

        jar1 = mods_dir / "mod1.jar"
        jar1.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        generator = extract_lang_files_generator(str(mods_dir), str(output_dir))
        updates = list(generator)

        has_current_total = any(
            'current' in u and 'total' in u for u in updates
        )
        assert has_current_total, f"Expected updates with current/total, got: {updates[-3:]}"


    def test_extract_book_generator_yields_with_current_and_total(self, tmp_path):
        """Test extract_book_files_generator yields dicts with current and total."""
        from translation_tool.core.jar_processor import extract_book_files_generator

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"

        jar1 = mods_dir / "mod1.jar"
        jar1.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        generator = extract_book_files_generator(str(mods_dir), str(output_dir))
        updates = list(generator)

        has_current_total = any(
            'current' in u and 'total' in u for u in updates
        )
        assert has_current_total, f"Expected updates with current/total, got: {updates[-3:]}"


    def test_run_extraction_process_impl_yields_progress_with_counts(self, tmp_path):
        """Test run_extraction_process_impl yields current/total alongside progress for each JAR processed."""
        from translation_tool.core.jar_processor_extract import run_extraction_process_impl
        import re

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"

        jar1 = mods_dir / "mod1.jar"
        jar1.write_bytes(b"PK\x05\x06" + b"\x00" * 20)
        jar2 = mods_dir / "mod2.jar"
        jar2.write_bytes(b"PK\x05\x06" + b"\x00" * 20)

        target_regex = re.compile(r".*\.json$")

        def mock_extract(jar, out_dir, regex, scan_results):
            return {'status': 'success', 'extracted': 1, 'skipped': 0}

        def mock_scan(jar_dir, patterns):
            return []

        generator = run_extraction_process_impl(
            str(mods_dir),
            str(output_dir),
            target_regex,
            "Test",
            find_jar_files_fn=lambda d: list(mods_dir.glob("*.jar")),
            extract_from_jar_fn=mock_extract,
        )

        updates = list(generator)

        progress_updates = [u for u in updates if 'progress' in u and 0 < u['progress'] < 1]
        assert len(progress_updates) >= 1

        for u in progress_updates:
            assert 'current' in u, f"Missing current in {u}"
            assert 'total' in u, f"Missing total in {u}"
            assert u['current'] <= u['total']


class TestPreviewStateInitialization:
    """Test PreviewState dataclass and initialization."""

    def test_preview_state_defaults(self):
        """Test PreviewState has correct default values."""
        from app.views.extractor.extractor_state import PreviewState

        state = PreviewState()
        assert state.progress == 0.0
        assert state.current == 0
        assert state.total == 0
        assert state.done is False
        assert state.result is None
        assert state.error is None


    def test_preview_state_can_set_total_before_start(self):
        """Test that total can be pre-set before do_preview starts."""
        from app.views.extractor.extractor_state import PreviewState

        state = PreviewState()
        state.total = 478
        state.current = 0

        assert state.total == 478
        assert state.current == 0
        assert state.done is False


    def test_preview_state_as_dict(self):
        """Test PreviewState.as_dict() returns correct structure."""
        from app.views.extractor.extractor_state import PreviewState

        state = PreviewState()
        state.progress = 0.5
        state.current = 100
        state.total = 200
        state.done = True
        state.result = {'files': 50}
        state.error = None

        d = state.as_dict()
        assert d['progress'] == 0.5
        assert d['current'] == 100
        assert d['total'] == 200
        assert d['done'] is True
        assert d['result'] == {'files': 50}
        assert d['error'] is None