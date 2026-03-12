import flet as ft

from app.views import translation_view as tv


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self.loop = None

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

    def start(self):
        self.started += 1


def test_translation_view_builds_three_tabs_and_shared_status_panel(monkeypatch):
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = _Page()
    picker = _FilePicker()

    view = tv.TranslationView(page, picker)

    assert len(view.tabs.tabs) == 3
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
