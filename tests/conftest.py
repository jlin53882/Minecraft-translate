import importlib
import shutil
import sys
import tempfile
from pathlib import Path

import flet as ft
import pytest

# 移除 hermes-agent/tests 從 sys.path，避免它跟我們的 tests 套件命名衝突
# 這是 hermes-agent 自身 conftest 注入的，但對於我們專案的測試會造成問題
# （`from tests.conftest import mock_page` 會去 hermes-agent/tests 找 mock_page 而失敗）
_hermes_agent_tests = (
    Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent" / "tests"
)
if _hermes_agent_tests.exists():
    _hermes_agent_tests_resolved = _hermes_agent_tests.resolve()
    sys.path = [
        p for p in sys.path if Path(p).resolve() != _hermes_agent_tests_resolved
    ]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# -----------------------------------------------------------------------------
# Flet 0.85 Compatibility Helpers
# -----------------------------------------------------------------------------


def _border_all(width, color):
    """Flet 0.85+ compatible Border.all() helper.

    In flet >= 0.85, ft.Border no longer has the .all() class method.
    This replicates the behavior using Border + BorderSide.
    """
    import flet as ft

    return ft.Border(
        top=ft.BorderSide(width, color),
        right=ft.BorderSide(width, color),
        bottom=ft.BorderSide(width, color),
        left=ft.BorderSide(width, color),
    )


# Monkey-patch ft.Border.all for tests that expect the 0.28.3 API
ft.Border.all = staticmethod(_border_all)


# =============================================================================
# 共用 Fixtures
# =============================================================================


def pytest_configure(config):
    """Pytest 配置。"""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )


@pytest.fixture(autouse=True)
def _isolate_test_runtime_writes(tmp_path, monkeypatch, request):
    """將測試執行期的相對路徑與已知固定快取導向 tmp_path。

    Collection 發生在 fixture 之前，因此 production module 仍必須保持
    import-time 唯讀；本 fixture 只處理測試函式實際執行時的寫入。
    """
    monkeypatch.chdir(tmp_path)
    if request.node.path.name.startswith("test_icon_preview_"):
        module = importlib.import_module("app.views.icon_preview.icon_cache")
        entries_module = importlib.import_module("app.views.icon_preview.entries_cache")
        index_module = importlib.import_module("app.icon_index")
        icon_cache_root = tmp_path / "icon_cache"
        icon_cache = icon_cache_root / "jar_icons"
        index_cache = icon_cache_root / "icon_index" / "fixture.json"
        monkeypatch.setattr(entries_module, "_get_cache_dir", lambda: icon_cache_root)
        monkeypatch.setattr(module, "_get_icon_cache_dir", lambda: icon_cache)
        monkeypatch.setattr(index_module, "get_index_path", lambda _mods: index_cache)


@pytest.fixture(autouse=True)
def _no_default_batch_interval(monkeypatch):
    """預設的批次間隔（schema 預設 4 秒）不該讓測試真的睡覺。

    共用的模型配額時鐘會依 ``sleep_seconds_between_batches`` 節流每一次 provider 請求；
    沒明確設定間隔的測試會因此每個請求固定多等 4 秒。要驗證節流行為的測試
    一律明確傳入間隔，不依賴這個預設值。
    """
    from translation_tool.core import lm_translator_main

    monkeypatch.setattr(lm_translator_main, "_DEFAULT_BATCH_SLEEP", 0.0)


@pytest.fixture(autouse=True)
def _no_filter_options_settle_wait(monkeypatch):
    """篩選選項的第二階段正式會等條件穩定 0.25 秒才查；測試不該真的睡覺。

    要驗證「等待期間被取代」的測試自己把 ``SETTLE_SECONDS`` 設回正值。
    """
    from app.views.moddb import filter_options_loader

    monkeypatch.setattr(filter_options_loader, "SETTLE_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _patch_flet_page_property():
    """自動 patch Flet 的 BaseControl.page 屬性。

    Flet 的 BaseControl.page 是個 property，會從 self.parent 一路往上找到 Page 物件。
    在測試環境中，view 沒有真的被加到 page tree，所以這個 getter 會拋
    `RuntimeError: Control must be added to the page first`。

    這個 autouse fixture 會暫時把 BaseControl.page 換成一個會回傳 mock_page 的 property，
    這樣 view.page.update() / view.page.overlay.append() 等操作就不會失敗。

    這個 patch 在每個測試結束後會自動還原（yield 後的程式碼）。
    """
    from flet.controls.base_control import BaseControl

    # 暫存原本的 page property
    _original_page = BaseControl.page

    # 給每個 control instance 自己的 mock page 物件
    # 用 instance attribute 來儲存
    def _mock_page_getter(self):
        # 優先用 self._test_page（測試可手動設定）
        if hasattr(self, "_test_page"):
            return self._test_page
        # 否則用 self._page 屬性（很多地方會設定）
        if hasattr(self, "_page"):
            return self._page
        # 都沒有就回傳一個空的 mock
        return _EmptyPage()

    class _EmptyPage:
        """最簡單的 mock page，避免 view.page 拋 RuntimeError。"""

        def __init__(self):
            self.overlay = []
            self.updated = 0
            self._tasks = []

        def update(self, *args, **kwargs):
            self.updated += 1

        def open(self, dialog):
            self.overlay.append(dialog)
            dialog.open = True

        def close(self, dialog):
            dialog.open = False

        # 🐛 2026-07-12 user review: ExtractorView._show_extraction_summary 改用
        # Flet 0.85 內建 show_dialog API 後,連 _EmptyPage fallback 也需對應方法。
        def show_dialog(self, dialog):
            self.overlay.append(dialog)
            dialog.open = True

        def pop_dialog(self):
            if self.overlay:
                dlg = self.overlay[-1]
                dlg.open = False
                return dlg
            return None

        def run_task(self, coro, *args):
            self._tasks.append((coro, args))

    # patch BaseControl.page
    BaseControl.page = property(_mock_page_getter)

    yield

    # 還原
    BaseControl.page = _original_page


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


# View 測試用的 mock 工廠放在 tests/support/ui_mocks.py（只載入一次）；這裡重新匯出供既有的
# `from tests.conftest import mock_page` 使用。
from tests.support.ui_mocks import (  # noqa: E402,F401
    _make_filepicker,
    _make_page,
    mock_filepicker,
    mock_page,
)
