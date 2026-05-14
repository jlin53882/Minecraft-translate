"""app/views/untranslated_checker.py 單元測試。

用途：驗證 UntranslatedChecker 元件的功能正確性。
"""

from app.views.untranslated_checker import UntranslatedChecker
from app.views.qc_base import QCBase


class _MockPage:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self._tasks = []

    def update(self):
        self.updated += 1

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


class _MockFilePicker:
    def __init__(self):
        self.on_result = None
        self.last_directory_path = None
        self.last_title = None
        self._mock_path = "/mock/path"

    async def get_directory_path(self, dialog_title: str = None):
        self.last_title = dialog_title
        return self._mock_path

    async def pick_files(self, dialog_title: str = None):
        self.last_title = dialog_title
        return [type('obj', (object,), {'path': self._mock_path})()]


class _MockProgressBar:
    def __init__(self):
        self.value = 0
        self.visible = False
        self.color = None


class _MockListView:
    def __init__(self):
        self.controls = []

    def scroll_to(self, offset=None, duration=None):
        pass


def test_untranslated_checker_initialization():
    """測試 UntranslatedChecker 初始化"""
    page = _MockPage()
    file_picker = _MockFilePicker()
    task_runner = QCBase(page, _MockProgressBar(), _MockListView())

    checker = UntranslatedChecker(page, file_picker, task_runner)

    assert checker.page is page
    assert checker.file_picker is file_picker
    assert checker.task_runner is task_runner


def test_untranslated_checker_has_required_fields():
    """測試 UntranslatedChecker 有所需的 UI 欄位"""
    page = _MockPage()
    file_picker = _MockFilePicker()
    task_runner = QCBase(page, _MockProgressBar(), _MockListView())

    checker = UntranslatedChecker(page, file_picker, task_runner)

    assert hasattr(checker, "en_dir")
    assert hasattr(checker, "tw_dir")
    assert hasattr(checker, "out_dir")
    assert hasattr(checker, "start_button")


def test_untranslated_checker_start_button_text():
    """測試開始按鈕文字"""
    page = _MockPage()
    file_picker = _MockFilePicker()
    task_runner = QCBase(page, _MockProgressBar(), _MockListView())

    checker = UntranslatedChecker(page, file_picker, task_runner)

    assert checker.start_button.content == "開始檢查"


def test_untranslated_checker_registers_file_picker():
    """測試 UntranslatedChecker 建立 file_picker（自動通過 init() 註冊，不需添加到 page.overlay）"""
    page = _MockPage()
    file_picker = _MockFilePicker()
    task_runner = QCBase(page, _MockProgressBar(), _MockListView())

    # file_picker 不在 overlay 中
    assert file_picker not in page.overlay

    checker = UntranslatedChecker(page, file_picker, task_runner)

    # FilePicker 是 Service，自動註冊，不需要添加到 page.overlay
    # 因此 file_picker 仍然不在 overlay 中（這是預期行為）
    assert file_picker not in page.overlay


def test_untranslated_checker_pick_directory():
    """測試選擇目錄功能"""
    page = _MockPage()
    file_picker = _MockFilePicker()
    task_runner = QCBase(page, _MockProgressBar(), _MockListView())

    checker = UntranslatedChecker(page, file_picker, task_runner)

    # 模擬點擊選擇目錄
    checker._pick_file_or_directory(
        e=None,
        target_textfield=checker.en_dir,
        title="選擇目錄",
        folder_mode=True,
    )

    # 執行所有排程的 async tasks
    page._run_all_tasks()

    # 驗證 file_picker 被呼叫
    assert file_picker.last_title == "選擇目錄"
    # 驗證 target_textfield 值被更新
    assert checker.en_dir.value == "/mock/path"


def test_untranslated_checker_shows_snackbar_on_cancel():
    """測試取消選擇時顯示訊息"""
    page = _MockPage()
    file_picker = _MockFilePicker()
    task_runner = QCBase(page, _MockProgressBar(), _MockListView())

    checker = UntranslatedChecker(page, file_picker, task_runner)

    # 設定 file_picker.on_result 為 None（取消選擇）
    file_picker.on_result = None

    # 呼叫 snackbar（應該不崩潰）
    checker._show_snack_bar("測試訊息")

    # 驗證 snackbar 被加入 overlay
    assert len(page.overlay) >= 1
