import sys
import tempfile
import shutil
from pathlib import Path

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
import flet as ft
ft.Border.all = staticmethod(_border_all)


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


# -----------------------------------------------------------------------------
# View Test Fixtures — 工廠模式
# -----------------------------------------------------------------------------

def _make_page(**overrides):
    """工廠函數：每次回傳全新的 _Page instance。

    用法:
        page = _make_page()                          # 預設值
        page = _make_page(loop=my_loop)              # 自訂單一欄位
        page = _make_page(overlay=[], updated=0)     # 自訂多欄位

    包含所有 view tests 用到的所有欄位:
        - overlay: list, page overlay 控件
        - updated: int, update() 呼叫次數
        - loop: any, asyncio event loop（某些測試需要）
        - dialog: any, Flet dialog 物件（某些測試需要）
        - _tasks: list, 待執行的 coroutine 任務
    """

    class _Loop:
        def call_soon_threadsafe(self, func, *args, **kwargs):
            func(*args, **kwargs)

    class _Page:
        def __init__(self):
            self.overlay = []
            self.updated = 0
            self.loop = _Loop()
            self.dialog = None
            self._tasks = []
            for k, v in overrides.items():
                setattr(self, k, v)

        def update(self, *args, **kwargs):
            self.updated += 1

        def open(self, dialog):
            self.overlay.append(dialog)
            dialog.open = True

        def close(self, dialog):
            dialog.open = False

        def run_task(self, coro, *args):
            self._tasks.append((coro, args))

        def _run_all_tasks(self):
            for coro, args in self._tasks:
                result = coro(*args)
                if result is not None:
                    try:
                        result.send(None)
                    except StopIteration:
                        pass

    return _Page()


def _make_filepicker(**overrides):
    """工廠函數：每次回傳全新的 _FilePicker instance。

    用法:
        fp = _make_filepicker()                                    # 預設值
        fp = _make_filepicker(_mock_path='/test')                 # 自訂路徑
        fp = _make_filepicker(_mock_path='/test', _mock_files=[]) # 多欄位

    包含所有 view tests 用到的所有方法:
        - get_directory_path(): async, 回傳 _mock_path
        - pick_files(): async, 回傳 _mock_files
        - save_file(): async, 回傳 _mock_path
        - on_upload(callback): 設定 callback
        - set_mock_path(path): 設定 _mock_path
        - reset(): 重置所有狀態
    """
    class _FilePicker:
        def __init__(self):
            self._callback = None
            self.on_result = None
            self._mock_path = None
            self._mock_files = []
            for k, v in overrides.items():
                setattr(self, k, v)

        def on_upload(self, callback):
            self._callback = callback

        async def get_directory_path(self, dialog_title: str = None):
            return self._mock_path

        async def pick_files(self, dialog_title: str = None, allowed_extensions: list = None):
            return [type('obj', (object,), {'path': self._mock_path})()] if self._mock_path else []

        async def save_file(self, dialog_title: str = None, allowed_extensions: list = None, file_name: str = None):
            return self._mock_path

        def set_mock_path(self, path):
            self._mock_path = path

        def reset(self):
            self._mock_path = None
            self._mock_files = []

    return _FilePicker()


def mock_page():
    """Fixture: 回傳全新的 _Page instance。

    每次 call 都回傳新 instance，無共享狀態。
    包含所有 view tests 用到的所有欄位:
        - overlay: list, page overlay 控件
        - updated: int, update() 呼叫次數
        - loop: any, asyncio event loop（某些測試需要）
        - dialog: any, Flet dialog 物件（某些測試需要）
        - _tasks: list, 待執行的 coroutine 任務
    """
    return _make_page()


def mock_filepicker():
    """Fixture: 回傳全新的 _FilePicker instance。

    每次 call 都回傳新 instance，無共享狀態。
    包含所有 view tests 用到的所有方法:
        - get_directory_path(): async, 回傳 _mock_path
        - pick_files(): async, 回傳 _mock_files
        - save_file(): async, 回傳 _mock_path
        - on_upload(callback): 設定 callback
        - set_mock_path(path): 設定 _mock_path
        - reset(): 重置所有狀態
    """
    return _make_filepicker()