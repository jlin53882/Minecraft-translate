"""app/views/qc_view_characterization.py 測試。

用途：驗證 QCView 與新拆分公司件的功能正確性。
維護注意：PR1 拆分後更新測試以匹配新結構。
"""

from app.views.qc_view import QCView
from app.views.untranslated_checker import UntranslatedChecker
from app.views.qc_base import QCBase


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self.dialog = None
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


class _FilePicker:
    def __init__(self):
        self.on_result = None
        self._mock_path = None

    async def get_directory_path(self, dialog_title: str = None):
        return self._mock_path

    async def pick_files(self, dialog_title: str = None):
        return [type('obj', (object,), {'path': self._mock_path})()] if self._mock_path else []

    def set_mock_path(self, path):
        self._mock_path = path


class _ProgressBar:
    def __init__(self):
        self.value = 0
        self.visible = False
        self.color = None


class _ListView:
    def __init__(self):
        self.controls = []
        self.expand = True

    def scroll_to(self, offset=None, duration=None):
        pass


def test_qc_view_initializes_three_cards_and_shared_log_area():
    """測試 QCView 初始化三個卡片與共用日誌區域"""
    view = QCView(_Page(), _FilePicker())

    # 檢查新拆分的 UntranslatedChecker 元件
    assert isinstance(view.untranslated_checker, UntranslatedChecker)
    assert view.untranslated_checker.start_button.content == "開始檢查"

    # 檢查 JSON 比較按鈕
    assert view.compare_start_button.content == "啟動：JSON 資料夾差異比對"

    # 檢查 TSV 比較按鈕
    assert view.compare_tsv_start_button.content == "啟動：TSV 單檔案差異比對"

    # 檢查共用日誌
    assert view.progress_bar.visible is False
    assert view.log_view is not None

    # 檢查 task_runner (QCBase 實例)
    assert isinstance(view.task_runner, QCBase)


def test_set_controls_disabled_toggles_json_and_tsv_inputs():
    """測試 set_controls_disabled 只影響 JSON 和 TSV 輸入（不含 UntranslatedChecker）"""
    view = QCView(_Page(), _FilePicker())

    view.set_controls_disabled(True)

    # JSON 比較控制項應被禁用
    assert view.cn_dir_textfield.disabled is True
    assert view.compare_start_button.disabled is True

    # TSV 比較控制項應被禁用
    assert view.tsv_out_file_textfield.disabled is True

    view.set_controls_disabled(False)

    # 恢復為未禁用
    assert view.cn_dir_textfield.disabled is False
    assert view.compare_start_button.disabled is False
    assert view.tsv_out_file_textfield.disabled is False


def test_start_task_untranslated_requires_all_paths():
    """測試未翻譯檢查需要填寫所有路徑"""
    page = _Page()
    view = QCView(page, _FilePicker())

    # 嘗試啟動任務但路徑為空
    view.start_task("untranslated")

    # 檢查是否有錯誤訊息
    assert page.overlay
    assert "錯誤：請填寫所有" in page.overlay[-1].content.value


def test_task_runner_task_worker_exists_and_callable():
    """測試 task_runner.task_worker 方法存在且可呼叫"""
    page = _Page()
    view = QCView(page, _FilePicker())

    # 確認 task_runner.task_worker 方法存在
    assert hasattr(view.task_runner, "task_worker")
    assert callable(view.task_runner.task_worker)

    # 確認可以傳入參數呼叫（不實際執行緒，僅驗證簽名）
    # 使用一個簡單的 mock service
    def dummy_service(*args):
        return iter([])

    # 這應該不會引發錯誤（即使生成器是空的）
    try:
        view.task_runner.task_worker(dummy_service, tuple())
    except Exception as e:
        # 不應有語法或簽名錯誤
        raise AssertionError(f"task_worker 呼叫失敗: {e}")


def test_untranslated_checker_uses_task_runner():
    """測試 UntranslatedChecker 使用傳入的 task_runner"""
    page = _Page()
    file_picker = _FilePicker()
    view = QCView(page, file_picker)

    # 確認 UntranslatedChecker 使用 view.task_runner
    assert view.untranslated_checker.task_runner is view.task_runner
