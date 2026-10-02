"""匯入時檔案系統副作用的回歸測試。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_import_probe(source: str) -> subprocess.CompletedProcess[str]:
    """用乾淨的直譯器對目前的程式碼執行探測。"""
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
    """收集測試時可能會匯入 cache_manager，但匯入必須維持唯讀。"""
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
    """學名快取採惰性初始化，因此收集測試時不可建立使用者資料。"""
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
