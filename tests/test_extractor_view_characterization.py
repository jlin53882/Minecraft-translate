
import flet as ft
from app.views.extractor_view import ExtractorView


class _Session:
    def __init__(self, max_logs=2000):
        self._status = 'IDLE'
        self._progress = 0
        self._logs = []
        self._error = False
    def start(self):
        self._status = 'RUNNING'
    def snapshot(self):
        return {'status': self._status, 'progress': self._progress, 'logs': self._logs, 'error': self._error}


class _Page:
    def __init__(self):
        self.overlay = []
        self.dialog = None
        self.updated = 0
        self._tasks = []

    def update(self):
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


class _FilePicker:
    def __init__(self):
        self.on_result = None
        self._mock_path = None

    async def get_directory_path(self, dialog_title: str = None):
        return self._mock_path

    def set_mock_path(self, path):
        self._mock_path = path


def test_extractor_view_has_preview_and_extract_buttons(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    assert view.lang_button.content == '提取 Lang'
    assert view.book_button.content == '提取 Book'
    assert view.preview_lang_button.content == '預覽 Lang'
    assert view.preview_book_button.content == '預覽 Book'


def test_clear_output_path_appends_system_log(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())
    view.output_dir_textfield.value = 'C:/Out'

    view.clear_output_path()

    assert view.output_dir_textfield.value == ''
    assert view.log_view.controls[-1].value == '[系統] 已清除輸出路徑'


def test_update_stats_from_log_counts_success_warning_failure(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    view._update_stats_from_log('成功提取 3 個新檔案')
    view._update_stats_from_log('跳過已存在檔案')
    view._update_stats_from_log('[ERROR] boom')

    assert view._extraction_stats['success'] == 1
    assert view._extraction_stats['warnings'] == 1
    assert view._extraction_stats['failures'] == 1
    assert view._extraction_stats['total_files'] == 3


def test_extractor_view_mods_dir_textfield_exists(monkeypatch):
    """測試 mods_dir_textfield 存在且可設定"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    view.mods_dir_textfield.value = 'C:/Mods'
    assert view.mods_dir_textfield.value == 'C:/Mods'
    assert view.mods_dir_textfield.hint_text == './mods 或 %USERPROFILE%/Mods'


def test_extractor_view_status_text_and_progress_bar(monkeypatch):
    """測試 status_text 和 progress_bar 存在"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    assert view.status_text.value == '狀態：閒置'
    assert isinstance(view.progress_bar, ft.ProgressBar)
    assert view.progress_bar.visible is True
    assert view.progress_bar.value == 0


def test_extractor_view_output_dir_textfield_exists(monkeypatch):
    """測試 output_dir_textfield 存在"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    view.output_dir_textfield.value = 'C:/Out'
    assert view.output_dir_textfield.value == 'C:/Out'
    assert view.output_dir_textfield.hint_text == '（未指定將自動產生）'


def test_extractor_view_log_view_exists(monkeypatch):
    """測試 log_view 存在且為 ListView"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    assert isinstance(view.log_view, ft.ListView)
    assert view.log_view.auto_scroll is True


def test_extractor_view_all_buttons_have_on_click(monkeypatch):
    """測試所有按鈕都有 on_click 回調"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    assert view.lang_button.on_click is not None
    assert view.book_button.on_click is not None
    assert view.preview_lang_button.on_click is not None
    assert view.preview_book_button.on_click is not None


def test_extractor_view_update_stats_resets_counters(monkeypatch):
    """測試 _update_stats_from_log 的計數邏輯"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    view._extraction_stats = {"success": 0, "warnings": 0, "failures": 0, "total_files": 0}

    view._update_stats_from_log('成功提取 5 個新檔案')
    view._update_stats_from_log('跳過已存在檔案')
    view._update_stats_from_log('[WARN] 部分失敗')
    view._update_stats_from_log('[ERROR] 嚴重錯誤')

    assert view._extraction_stats['success'] == 1
    assert view._extraction_stats['warnings'] == 1
    assert view._extraction_stats['failures'] == 1
    assert view._extraction_stats['total_files'] == 5


def test_extractor_view_pick_directory_schedules_async_task(monkeypatch):
    """測試 pick_directory 正確排程 async task"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    page = _Page()
    picker = _FilePicker()
    picker.set_mock_path('/test/dir')
    view = ExtractorView(page, picker)

    target = ft.TextField()
    view.pick_directory(target)

    assert len(page._tasks) == 1
    page._run_all_tasks()

    assert target.value == '/test/dir'
    assert page.updated >= 1
