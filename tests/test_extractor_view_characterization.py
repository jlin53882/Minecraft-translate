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
    def update(self):
        self.updated += 1
    def open(self, dialog):
        self.overlay.append(dialog)
        dialog.open = True
    def close(self, dialog):
        dialog.open = False


class _FilePicker:
    def __init__(self):
        self.on_result = None
    def get_directory_path(self):
        return None


def test_extractor_view_has_preview_and_extract_buttons(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(_Page(), _FilePicker())

    assert view.lang_button.text == '提取 Lang'
    assert view.book_button.text == '提取 Book'
    assert view.preview_lang_button.text == '預覽 Lang'
    assert view.preview_book_button.text == '預覽 Book'


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
