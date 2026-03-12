from app.views import lm_view


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
    def update(self):
        self.updated += 1


class _FilePicker:
    def __init__(self):
        self.on_result = None
    def get_directory_path(self):
        return None


class _Session:
    def __init__(self):
        self.started = 0
        self.logs = []
    def start(self):
        self.started += 1
    def add_log(self, text):
        self.logs.append(text)
    def snapshot(self):
        return {'status': 'DONE', 'progress': 1.0, 'logs': ['done']}


def test_lm_view_initializes_primary_controls(monkeypatch):
    monkeypatch.setattr(lm_view, 'TaskSession', _Session)
    view = lm_view.LMView(_Page(), _FilePicker())

    assert view.start_button.text == '開始翻譯'
    assert view.status_chip.label.value == '尚未開始'


def test_start_clicked_without_input_sets_error_status(monkeypatch):
    monkeypatch.setattr(lm_view, 'TaskSession', _Session)
    page = _Page()
    view = lm_view.LMView(page, _FilePicker())

    view.start_clicked(None)

    assert view.status_chip.label.value == '請先選擇輸入資料夾'


def test_start_clicked_launches_service_with_current_flags(monkeypatch):
    page = _Page()
    calls = {}
    monkeypatch.setattr(lm_view, 'TaskSession', _Session)
    monkeypatch.setattr(lm_view.threading, 'Thread', lambda target=None, args=(), daemon=None: type('T', (), {'start': lambda self: target(*args)})())
    monkeypatch.setattr(lm_view.LMView, 'start_ui_timer', lambda self: None)

    def fake_service(input_dir, output_dir, session, dry_run, export_lang, write_new_cache):
        calls.update({
            'input_dir': input_dir,
            'output_dir': output_dir,
            'session': session,
            'dry_run': dry_run,
            'export_lang': export_lang,
            'write_new_cache': write_new_cache,
        })

    monkeypatch.setattr(lm_view, 'run_lm_translation_service', fake_service)

    view = lm_view.LMView(page, _FilePicker())
    view.input_path.value = 'C:/Assets'
    view.output_path.value = 'C:/Out'
    view.dry_run_switch.value = True
    view.export_lang_checkbox.value = True
    view.write_new_cache_switch.value = True

    view.start_clicked(None)

    assert calls['input_dir'] == 'C:/Assets'
    assert calls['output_dir'] == 'C:/Out'
    assert calls['dry_run'] is True
    assert calls['export_lang'] is True
    assert calls['write_new_cache'] is True
