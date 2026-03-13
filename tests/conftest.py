import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# =============================================================================
# 共用 Fixtures
# =============================================================================

def pytest_configure(config):
    """Pytest 配置。"""
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")


# -----------------------------------------------------------------------------
# Temp Directory Fixtures
# -----------------------------------------------------------------------------

def temp_dir():
    """提供臨時目錄，測試結束後自動清理。
    
    Yields:
        Path: 臨時目錄路徑
    """
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


# -----------------------------------------------------------------------------
# Mock Config Fixtures
# -----------------------------------------------------------------------------

def mock_config():
    """提供測試用的 mock config。
    
    Returns:
        dict: Mock 設定字典
    """
    return {
        "test_mode": True,
        "mock_api": True,
        "verbose": False,
    }


def mock_empty_config():
    """提供空的 mock config（用於測試預設值）。
    
    Returns:
        dict: 空設定字典
    """
    return {}
