from app.views import merge_view


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
    def update(self):
        self.updated += 1


class _FilePicker:
    def __init__(self):
        self.on_result = None
    def pick_files(self, **kwargs):
        return None
    def get_directory_path(self, **kwargs):
        return None


class _Session:
    def __init__(self, max_logs=2000):
        self.logs = []
        self.started = 0
    def start(self):
        self.started += 1
    def add_log(self, text):
        self.logs.append(text)
    def snapshot(self):
        return {'status': 'DONE', 'progress': 1.0, 'logs': self.logs}


def test_merge_view_initializes_buttons_and_status(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(_Page(), _FilePicker())

    assert view.pick_zip_button.text == '新增 ZIP'
    assert view.start_button.text == '開始合併 ZIP'
    assert view.status_chip.label.value == '尚未開始'


def test_start_merge_without_inputs_shows_snack(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = _Page()
    view = merge_view.MergeView(page, _FilePicker())

    view.start_merge(None)

    assert page.overlay
    assert '請先選擇 ZIP 與輸出資料夾' in page.overlay[-1].content.value


def test_remove_zip_updates_selected_list(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(_Page(), _FilePicker())
    view.selected_zips = ['a.zip', 'b.zip']

    view._remove_zip('a.zip')

    assert view.selected_zips == ['b.zip']
