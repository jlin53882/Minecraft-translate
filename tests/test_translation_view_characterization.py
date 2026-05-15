
from app.views import translation_view as tv
from tests.conftest import mock_page, mock_filepicker


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
    page = mock_page()
    picker = mock_filepicker()

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
    page = mock_page()
    picker = mock_filepicker()
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
    page = mock_page()
    picker = mock_filepicker()
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


def test_reset_kjs_inputs_restores_defaults_and_appends_log(monkeypatch):
    """驗證 KubeJS 輸入重置行為"""
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    view.kjs_in_dir.value = 'X'
    view.kjs_out_dir.value = 'Y'
    view.kjs_step_extract.value = False
    view.kjs_step_translate.value = False
    view.kjs_step_inject.value = False
    view.kjs_write_new_cache.value = False

    view._reset_kjs_inputs()

    assert view.kjs_in_dir.value == ''
    assert view.kjs_out_dir.value == ''
    assert view.kjs_step_extract.value is True
    assert view.kjs_step_translate.value is True
    assert view.kjs_step_inject.value is True
    assert view.kjs_write_new_cache.value is True


def test_run_kjs_calls_run_kjs_service(monkeypatch):
    """驗證 _run_kjs 正確呼叫 run_kjs"""
    page = mock_page()
    picker = mock_filepicker()
    calls = {}

    monkeypatch.setattr(tv, 'TaskSession', _Session)
    monkeypatch.setattr(tv.threading, 'Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr(tv.TranslationView, '_start_ui_timer', lambda self: None)

    def fake_service(in_dir, session, **kwargs):
        calls['in_dir'] = in_dir
        calls.update(kwargs)

    monkeypatch.setattr(tv, 'run_kubejs_tooltip_service', fake_service)

    view = tv.TranslationView(page, picker)
    view.kjs_in_dir.value = 'C:/KJS/In'
    view.kjs_out_dir.value = 'C:/KJS/Out'
    view.kjs_step_extract.value = True
    view.kjs_step_translate.value = False
    view.kjs_step_inject.value = True
    view.kjs_write_new_cache.value = True

    view._run_kjs(dry_run=False)

    assert calls['input_dir'] == 'C:/KJS/In'
    assert calls['output_dir'] == 'C:/KJS/Out'
    assert calls['dry_run'] is False
    assert calls['step_translate'] is False
    assert view.status_chip.label.value == '執行中'


def test_run_md_calls_run_md_service(monkeypatch):
    """驗證 _run_md 正確呼叫 run_md"""
    page = mock_page()
    picker = mock_filepicker()
    calls = {}

    monkeypatch.setattr(tv, 'TaskSession', _Session)
    monkeypatch.setattr(tv.threading, 'Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr(tv.TranslationView, '_start_ui_timer', lambda self: None)

    def fake_service(in_dir, session, **kwargs):
        calls['in_dir'] = in_dir
        calls.update(kwargs)

    monkeypatch.setattr(tv, 'run_md_translation_service', fake_service)

    view = tv.TranslationView(page, picker)
    view.md_in_dir.value = 'C:/MD/In'
    view.md_out_dir.value = 'C:/MD/Out'
    view.md_step_extract.value = True
    view.md_step_translate.value = True
    view.md_step_inject.value = False
    view.md_write_new_cache.value = True

    view._run_md(dry_run=True)

    assert calls['input_dir'] == 'C:/MD/In'
    assert calls['output_dir'] == 'C:/MD/Out'
    assert calls['dry_run'] is True
    assert calls['step_inject'] is False


def test_run_ftb_calls_run_ftb_service(monkeypatch):
    """驗證 _run_ftb 正確呼叫 run_ftb"""
    page = mock_page()
    picker = mock_filepicker()
    calls = {}

    monkeypatch.setattr(tv, 'TaskSession', _Session)
    monkeypatch.setattr(tv.threading, 'Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr(tv.TranslationView, '_start_ui_timer', lambda self: None)

    def fake_service(in_dir, session, **kwargs):
        calls['in_dir'] = in_dir
        calls.update(kwargs)

    monkeypatch.setattr(tv, 'run_ftb_translation_service', fake_service)

    view = tv.TranslationView(page, picker)
    view.ftb_in_dir.value = 'C:/FTB/In'
    view.ftb_out_dir.value = 'C:/FTB/Out'
    view.ftb_step_export.value = True
    view.ftb_step_clean.value = True
    view.ftb_step_translate.value = True
    view.ftb_step_inject.value = True
    view.ftb_write_new_cache.value = True

    view._run_ftb(dry_run=False)

    assert calls['in_dir'] == 'C:/FTB/In'
    assert calls['output_dir'] == 'C:/FTB/Out'
    assert calls['dry_run'] is False


def test_pick_directory_into_without_page_update_when_no_result(monkeypatch):
    """驗證 _pick_directory_into 在取消選擇時不更新"""
    page = mock_page()
    picker = mock_filepicker()
    picker._mock_path = None
    view = tv.TranslationView(page, picker)

    target = tv.ft.TextField(value="original")
    view._pick_directory_into(target)
    page._run_all_tasks()

    assert target.value == "original"
    assert page.updated == 0
    assert view.log_view.controls[-1].value == '[UI] 已重置：KubeJS 輸入已清空'


def test_reset_ftb_inputs_restores_defaults(monkeypatch):
    """驗證 FTB 輸入重置行為"""
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    view.ftb_in_dir.value = 'X'
    view.ftb_out_dir.value = 'Y'
    view.ftb_step_export.value = False
    view.ftb_step_clean.value = False
    view.ftb_step_translate.value = False
    view.ftb_step_inject.value = False
    view.ftb_write_new_cache.value = False

    view._reset_ftb_inputs()

    assert view.ftb_in_dir.value == ''
    assert view.ftb_out_dir.value == ''
    assert view.ftb_step_export.value is True
    assert view.ftb_step_clean.value is True
    assert view.ftb_step_translate.value is True
    assert view.ftb_step_inject.value is True
    assert view.ftb_write_new_cache.value is True
    assert view.log_view.controls[-1].value == '[UI] 已重置：FTB Quests 輸入已清空'


def test_clear_logs_removes_all_controls(monkeypatch):
    """驗證 _clear_logs 正確清除所有日誌"""
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    view._append_log('line1')
    view._append_log('line2')
    assert len(view.log_view.controls) == 3  # initial + 2

    view._clear_logs()

    assert len(view.log_view.controls) == 0


def test_append_log_trims_to_max_400_lines(monkeypatch):
    """驗證 _append_log 不會无限增长"""
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    for i in range(450):
        view._append_log(f'log{i}')

    assert len(view.log_view.controls) < 400


def test_set_status_updates_chip_label_and_color(monkeypatch):
    """驗證 _set_status 更新 status_chip 的文字與背景顏色"""
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    view._set_status('工作中', '#FF0000')

    assert view.status_chip.label.value == '工作中'
    assert view.status_chip.bgcolor == '#FF0000'


def test_pick_directory_into_sets_target_field(monkeypatch):
    """驗證 _pick_directory_into 正確設定目標欄位"""
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker.set_mock_path('/test/path')
    view = tv.TranslationView(page, picker)

    target = tv.ft.TextField()
    view._pick_directory_into(target)
    page._run_all_tasks()

    assert target.value == '/test/path'
    assert page.updated >= 1


def test_show_snack_adds_to_page_overlay(monkeypatch):
    """驗證 _show_snack 正確將 SnackBar 加入 page.overlay"""
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    view._show_snack('Test message', '#00FF00')

    assert len(page.overlay) == 1
    assert page.overlay[0].open is True


def test_kjs_controls_accessible_and_resettable(monkeypatch):
    """驗證 KJS 所有控件可存取且 Reset 正確"""
    monkeypatch.setattr(tv, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    assert hasattr(view, 'kjs_in_dir')
    assert hasattr(view, 'kjs_out_dir')
    assert hasattr(view, 'kjs_step_extract')
    assert hasattr(view, 'kjs_step_translate')
    assert hasattr(view, 'kjs_step_inject')
    assert hasattr(view, 'kjs_write_new_cache')

    view.kjs_in_dir.value = 'C:/KJS/In'
    view.kjs_out_dir.value = 'C:/KJS/Out'
    view.kjs_step_extract.value = False
    view.kjs_step_translate.value = False
    view.kjs_step_inject.value = False
    view.kjs_write_new_cache.value = False

    view._reset_kjs_inputs()

    assert view.kjs_in_dir.value == ''
    assert view.kjs_out_dir.value == ''
    assert view.kjs_step_extract.value is True
    assert view.kjs_step_translate.value is True
    assert view.kjs_step_inject.value is True
    assert view.kjs_write_new_cache.value is True


def test_run_kjs_calls_run_kjs_service(monkeypatch):
    """驗證 _run_kjs 正確呼叫 run_kjs"""
    page = mock_page()
    picker = mock_filepicker()
    calls = {}

    monkeypatch.setattr(tv, 'TaskSession', _Session)
    monkeypatch.setattr(tv.threading, 'Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr(tv.TranslationView, '_start_ui_timer', lambda self: None)

    def fake_service(input_dir, session, **kwargs):
        calls['input_dir'] = input_dir
        calls.update(kwargs)

    monkeypatch.setattr(tv, 'run_kubejs_tooltip_service', fake_service)

    view = tv.TranslationView(page, picker)
    view.kjs_in_dir.value = 'C:/KJS/In'
    view.kjs_out_dir.value = 'C:/KJS/Out'
    view.kjs_step_extract.value = True
    view.kjs_step_translate.value = False
    view.kjs_step_inject.value = True
    view.kjs_write_new_cache.value = True

    view._run_kjs(dry_run=False)

    assert calls['input_dir'] == 'C:/KJS/In'
    assert calls['output_dir'] == 'C:/KJS/Out'
    assert calls['dry_run'] is False
    assert calls['step_translate'] is False
    assert view.status_chip.label.value == '執行中'


def test_run_md_calls_run_md_service(monkeypatch):
    """驗證 _run_md 正確呼叫 run_md"""
    page = mock_page()
    picker = mock_filepicker()
    calls = {}

    monkeypatch.setattr(tv, 'TaskSession', _Session)
    monkeypatch.setattr(tv.threading, 'Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr(tv.TranslationView, '_start_ui_timer', lambda self: None)

    def fake_service(input_dir, session, **kwargs):
        calls['input_dir'] = input_dir
        calls.update(kwargs)

    monkeypatch.setattr(tv, 'run_md_translation_service', fake_service)

    view = tv.TranslationView(page, picker)
    view.md_in_dir.value = 'C:/MD/In'
    view.md_out_dir.value = 'C:/MD/Out'
    view.md_step_extract.value = True
    view.md_step_translate.value = True
    view.md_step_inject.value = False
    view.md_write_new_cache.value = True

    view._run_md(dry_run=True)

    assert calls['input_dir'] == 'C:/MD/In'
    assert calls['output_dir'] == 'C:/MD/Out'
    assert calls['dry_run'] is True
    assert calls['step_inject'] is False


def test_run_ftb_calls_run_ftb_service(monkeypatch):
    """驗證 _run_ftb 正確呼叫 run_ftb"""
    page = mock_page()
    picker = mock_filepicker()
    calls = {}

    monkeypatch.setattr(tv, 'TaskSession', _Session)
    monkeypatch.setattr(tv.threading, 'Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr(tv.TranslationView, '_start_ui_timer', lambda self: None)

    def fake_service(input_dir, session, **kwargs):
        calls['input_dir'] = input_dir
        calls.update(kwargs)

    monkeypatch.setattr(tv, 'run_ftb_translation_service', fake_service)

    view = tv.TranslationView(page, picker)
    view.ftb_in_dir.value = 'C:/FTB/In'
    view.ftb_out_dir.value = 'C:/FTB/Out'
    view.ftb_step_export.value = True
    view.ftb_step_clean.value = True
    view.ftb_step_translate.value = True
    view.ftb_step_inject.value = True
    view.ftb_write_new_cache.value = True

    view._run_ftb(dry_run=False)

    assert calls['input_dir'] == 'C:/FTB/In'
    assert calls['output_dir'] == 'C:/FTB/Out'
    assert calls['dry_run'] is False


def test_pick_directory_into_without_page_update_when_no_result(monkeypatch):
    """驗證 _pick_directory_into 在取消選擇時不更新"""
    page = mock_page()
    picker = mock_filepicker()
    picker._mock_path = None
    view = tv.TranslationView(page, picker)

    target = tv.ft.TextField(value="original")
    view._pick_directory_into(target)
    page._run_all_tasks()

    assert target.value == "original"
    assert page.updated == 0


def test_translation_view_show_snack_adds_to_overlay():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    view._show_snack('Test error', '#FF0000')
    assert len(page.overlay) >= 1


def test_translation_view_path_row_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert view._path_row is not None


def test_translation_view_action_row_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert view._action_row is not None


def test_translation_view_async_pick_directory_into_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert hasattr(view, '_async_pick_directory_into')
    assert callable(view._async_pick_directory_into)


def test_translation_view_status_chip_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert view.status_chip is not None


def test_translation_view_progress_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert view.progress is not None
