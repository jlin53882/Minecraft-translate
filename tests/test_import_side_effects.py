"""Regression tests for import-time filesystem side effects."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_import_probe(source: str) -> subprocess.CompletedProcess[str]:
    """Run a clean interpreter against the current checkout."""
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_importing_cache_manager_does_not_initialize_or_create_cache_root():
    """Collection may import cache_manager, but importing must remain read-only."""
    result = _run_import_probe(
        """
from pathlib import Path
root = Path.cwd()
cache_root = root / "快取資料"
before = cache_root.exists()
from translation_tool.utils import cache_manager
assert cache_manager.is_cache_initialized() is False
assert cache_root.exists() is before
"""
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_importing_species_cache_does_not_initialize_or_create_database_root():
    """Species cache initialization is lazy so collection cannot create user data."""
    result = _run_import_probe(
        """
from pathlib import Path
root = Path.cwd()
cache_root = root / "學名資料庫"
before = cache_root.exists()
from translation_tool.utils import species_cache
assert species_cache._initialized is False
assert cache_root.exists() is before
"""
    )
    assert result.returncode == 0, result.stdout + result.stderr
