"""View 測試用的 mock page／filepicker 工廠。

放在 ``tests/support`` 而不是 ``conftest.py``：conftest 會被 pytest 以 ``conftest`` 載入，
又被 ``from tests.conftest import ...`` 以另一個模組名稱再載入一次；助手只應有一份。
``tests/conftest.py`` 仍重新匯出這些名稱，既有的 import 不必改。
"""


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

        # 🐛 2026-07-12 user review: ExtractorView._show_extraction_summary 改用
        # Flet 0.85 內建 show_dialog API 後,mock 也需要對應方法才能驗證。
        def show_dialog(self, dialog):
            # 模擬 Flet 0.85 把 dialog 加入 _dialogs.controls。
            # 我們用簡化的 overlay append 模擬(mock 不需還原真正 stack 機制)。
            self.overlay.append(dialog)
            dialog.open = True

        def pop_dialog(self):
            # 模擬 Flet 0.85 從 stack topmost pop,沒東西 return None。
            if self.overlay:
                dlg = self.overlay[-1]
                dlg.open = False
                return dlg
            return None

        def run_task(self, coro, *args):
            self._tasks.append((coro, args))

        def _run_all_tasks(self):
            # 執行中又排入的任務也會在同一次跑完；已執行的任務不會被重播
            while self._tasks:
                coro, args = self._tasks.pop(0)
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

        async def get_directory_path(self, dialog_title: str | None = None):
            return self._mock_path

        async def pick_files(
            self,
            dialog_title: str | None = None,
            allowed_extensions: list | None = None,
            **kwargs,
        ):
            return (
                [type("obj", (object,), {"path": self._mock_path})()]
                if self._mock_path
                else []
            )

        async def save_file(
            self,
            dialog_title: str | None = None,
            allowed_extensions: list | None = None,
            file_name: str | None = None,
        ):
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
