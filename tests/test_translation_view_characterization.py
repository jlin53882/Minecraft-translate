
from app.views import translation_view as tv


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self.loop = None
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

    def set_mock_path(self, path):
        self._mock_path = path


class _Session:
    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1


def test_translation_view_builds_three_tabs_and_shared_status_panel(monkeypatch):
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = _Page()
    picker = _FilePicker()

    view = tv.TranslationView(page, picker)

    # Flet 0.85.0: tabs.content is a Column containing [TabBar, TabBarView]
    # Structure: Column([TabBar(tabs=[3 tabs]), TabBarView(controls=[3 contents])])
    content = view.tabs.content
    assert hasattr(content, 'controls')  # Is a Column-like
    assert len(content.controls) == 2  # TabBar + TabBarView
    tab_bar = content.controls[0]
    assert hasattr(tab_bar, 'tabs')  # Is a TabBar
    assert len(tab_bar.tabs) == 3  # 3 tabs in TabBar
    assert view.status_chip.label.value == '尚未開始'
    assert view.progress.value == 0


def test_run_ftb_dry_run_calls_service_with_current_flags(monkeypatch):
    page = _Page()
    picker = _FilePicker()
    calls = {}

    monkeypatch.setattr(tv, 'TaskSession', _Session)
    monkeypatch.setattr(tv.threading, 'Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr(tv.TranslationView, '_start_ui_timer', lambda self: None)

    def fake_service(in_dir, session, **kwargs):
        calls['in_dir'] = in_dir
        calls['session'] = session
        calls.update(kwargs)

    monkeypatch.setattr(tv, 'run_ftb_translation_service', fake_service)

    view = tv.TranslationView(page, picker)
    view.ftb_in_dir.value = 'C:/Pack'
    view.ftb_out_dir.value = 'C:/Out'
    view.ftb_step_export.value = True
    view.ftb_step_clean.value = False
    view.ftb_step_translate.value = True
    view.ftb_step_inject.value = False
    view.ftb_write_new_cache.value = True

    view._run_ftb(dry_run=True)

    assert calls['in_dir'] == 'C:/Pack'
    assert calls['output_dir'] == 'C:/Out'
    assert calls['dry_run'] is True
    assert calls['step_clean'] is False
    assert calls['step_inject'] is False
    assert view.status_chip.label.value == '模擬執行'


def test_reset_md_inputs_restores_defaults_and_appends_log(monkeypatch):
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = _Page()
    picker = _FilePicker()
    view = tv.TranslationView(page, picker)

    view.md_in_dir.value = 'X'
    view.md_out_dir.value = 'Y'
    view.md_step_extract.value = False
    view.md_step_translate.value = False
    view.md_step_inject.value = False
    view.md_write_new_cache.value = False
    view.md_lang_mode.value = 'all'

    view._reset_md_inputs()

    assert view.md_in_dir.value == ''
    assert view.md_out_dir.value == ''
    assert view.md_step_extract.value is True
    assert view.md_step_translate.value is True
    assert view.md_step_inject.value is True
    assert view.md_write_new_cache.value is True
    assert view.md_lang_mode.value == 'non_cjk_only'
    assert view.log_view.controls[-1].value == '[UI] 已重置：Markdown 輸入已清空'
