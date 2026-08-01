
from pathlib import Path
import flet as ft
import pytest
from app.views.extractor_view import ExtractorView
from app.views._log import LogView
from tests.conftest import mock_page, mock_filepicker


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


def test_extractor_view_has_preview_and_extract_buttons(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    assert view.lang_button.content == '提取 Lang'
    assert view.book_button.content == '提取 Book'
    assert view.preview_lang_button.content == '預覽 Lang'
    assert view.preview_book_button.content == '預覽 Book'


def test_clear_output_path_appends_system_log(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())
    view.output_dir_textfield.value = 'C:/Out'

    view.clear_output_path()

    assert view.output_dir_textfield.value == ''
    # PR refactor/unified-log-view: log_view 改為 LogView widget（ft.Container）
    # 內部 ListView 透過 _list_view 存取
    assert view.log_view._list_view.controls[-1].value == '[系統] 已清除輸出路徑'


def test_extractor_view_mods_dir_textfield_exists(monkeypatch):
    """測試 mods_dir_textfield 存在且可設定"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view.mods_dir_textfield.value = 'C:/Mods'
    assert view.mods_dir_textfield.value == 'C:/Mods'
    assert view.mods_dir_textfield.hint_text == './mods 或 %USERPROFILE%/Mods'


def test_extractor_view_output_dir_textfield_exists(monkeypatch):
    """測試 output_dir_textfield 存在"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view.output_dir_textfield.value = 'C:/Out'
    assert view.output_dir_textfield.value == 'C:/Out'
    assert view.output_dir_textfield.hint_text == '（未指定將自動產生）'


def test_extractor_view_log_view_exists(monkeypatch):
    """測試 log_view 存在且為 LogView widget（ft.Container 包 ft.ListView）。"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    # PR refactor/unified-log-view: log_view 改為 LogView widget（ft.Container）
    assert isinstance(view.log_view, LogView)
    # 內部 ListView 仍有 auto_scroll
    assert view.log_view._list_view.auto_scroll is True


def test_extractor_view_all_buttons_have_on_click(monkeypatch):
    """測試所有按鈕都有 on_click 回調"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    assert view.lang_button.on_click is not None
    assert view.book_button.on_click is not None
    assert view.preview_lang_button.on_click is not None
    assert view.preview_book_button.on_click is not None


def test_extractor_view_pick_directory_schedules_async_task(monkeypatch):
    """測試 pick_directory 正確排程 async task"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker.set_mock_path('/test/dir')
    view = ExtractorView(page, picker)

    view.pick_directory(view.mods_dir_textfield)

    assert len(page._tasks) == 1
    page._run_all_tasks()

    assert view.mods_dir_textfield.value == '/test/dir'
    assert page.updated >= 1


def test_extractor_view_show_snack_bar_adds_to_overlay(monkeypatch):
    """測試 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    page = mock_page()
    view = ExtractorView(page, mock_filepicker())

    view._show_snack_bar('Test error', '#FF0000')

    assert len(page.overlay) >= 1


def test_extractor_view_append_log_line_adds_control(monkeypatch):
    """測試 _append_log_line 將日誌行加入 log_view"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view._append_log_line('Test log entry')

    # PR refactor/unified-log-view: _append_log_line 內部走 LogView.add()
    # LogView 的內部 ListView 透過 _list_view 存取
    # 🐛 2026-08-01 user review:更精確斷言 - 確認最後一個 control 是 'Test log entry'
    # (原本 len(...) >= 1 太弱,任何 add log 都通過)
    assert view.log_view._list_view.controls[-1].value == 'Test log entry'


def test_extractor_view_logs_panel_visible(monkeypatch):
    """S1 修復 (2026-08-01 user review):日誌面板在主 UI 上可見

    2026-08-01 user review 之前:view._logs_panel 用 .visible=False 隱藏,
    所以 _append_log_line 寫進 self.log_view 但使用者看不到任何 log。
    S1 修復:把 _logs_panel 從 .visible=False 改成 .visible=True 並加進 self.controls。

    驗證:
    - view.controls 有 2 個 styled_card
    - 第 2 個卡片是「日誌」卡片
    - 日誌卡片內含 LogView widget
    """
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    # S1 修復 (2026-08-01 user review):日誌面板從 .visible=False 改成 visible=True,
    # 加上 controls 內 2 個 styled_card (設定 + 日誌)。
    # 直接驗證關鍵狀態:
    # 1. view.controls 有 2 個
    # 2. 第 2 個 cards title = '日誌'
    # 3. view._logs_panel.visible = True (S1 修復核心)
    # 4. view._logs_panel 內含 self.log_view (LogView widget)
    
    # S1 修復 (2026-08-01 user review):日誌面板從 .visible=False 改成 visible=True,
    # 加上 view.controls 內 2 個 (設定 + 日誌)。
    # 直接驗證 view._logs_panel 結構 (S1 修復核心):view._logs_panel 應該存在、
    # 內含 ft.Column([self.log_view]) 並 visible=True。

    assert len(view.controls) == 2, (
        f"回歸:S1 修復後 view.controls 應有 2 個 (設定 + 日誌),實際 {len(view.controls)} 個"
    )
    # S1 修復核心:_logs_panel 改為 visible=True (原本 False 隱藏)
    assert view._logs_panel.visible is True, (
        "回歸:view._logs_panel.visible=True (S1 修復沒生效,還被 .visible=False 隱藏)"
    )
    # view._logs_panel 應該是 ft.Column([self.log_view])
    from app.views._log import LogView
    assert isinstance(view._logs_panel, ft.Column), (
        f"回歸:view._logs_panel 應是 ft.Column,實際 {type(view._logs_panel).__name__}"
    )
    assert len(view._logs_panel.controls) >= 1, (
        f"回歸:view._logs_panel 沒裝 LogView widget,實際 {len(view._logs_panel.controls)} 個 controls"
    )
    # 第一個 control 應是 LogView (S1 修復:ft.Column([self.log_view]))
    assert isinstance(view._logs_panel.controls[0], LogView), (
        f"回歸:view._logs_panel 第一個 control 應是 LogView widget,實際 {type(view._logs_panel.controls[0]).__name__}"
    )


def test_extractor_view_set_controls_disabled_toggles_inputs(monkeypatch):
    """測試 set_controls_disabled 正確切換輸入控制項狀態"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view.set_controls_disabled(True)

    assert view.mods_dir_textfield.disabled is True
    assert view.output_dir_textfield.disabled is True
    assert view.lang_button.disabled is True

    view.set_controls_disabled(False)

    assert view.mods_dir_textfield.disabled is False
    assert view.output_dir_textfield.disabled is False


def test_extractor_view_build_settings_card(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())
    assert hasattr(view, '_build_settings_card')


def test_extractor_view_build_logs_card(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())
    assert hasattr(view, '_build_logs_card')


def test_extractor_view_pick_button_exists(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())
    assert hasattr(view, '_pick_button')


def test_auto_fill_output_path_lang_mode(monkeypatch):
    """測試 _auto_fill_output_path 在 lang 模式下產生正確的輸出路徑"""
    mock_cfg = {
        "extractor": {
            "output_folder_names": {
                "lang_extract": "_lang_out",
                "book_extract": "_book_out",
                "dual_extract": "_dual_out",
            }
        }
    }
    monkeypatch.setattr('app.services_impl.pipelines.extract_service.load_config', lambda: mock_cfg)
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view._auto_fill_output_path('/test/mods', mode='lang')

    assert Path(view.output_dir_textfield.value).name == 'mods_lang_out'


def test_auto_fill_output_path_book_mode(monkeypatch):
    """測試 _auto_fill_output_path 在 book 模式下產生正確的輸出路徑"""
    mock_cfg = {
        "extractor": {
            "output_folder_names": {
                "lang_extract": "_lang_out",
                "book_extract": "_book_out",
                "dual_extract": "_dual_out",
            }
        }
    }
    monkeypatch.setattr('app.services_impl.pipelines.extract_service.load_config', lambda: mock_cfg)
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view._auto_fill_output_path('/test/mods', mode='book')

    assert Path(view.output_dir_textfield.value).name == 'mods_book_out'


def test_auto_fill_output_path_dual_mode(monkeypatch):
    """測試 _auto_fill_output_path 在 dual 模式下產生正確的輸出路徑"""
    mock_cfg = {
        "extractor": {
            "output_folder_names": {
                "lang_extract": "_lang_out",
                "book_extract": "_book_out",
                "dual_extract": "_dual_out",
            }
        }
    }
    monkeypatch.setattr('app.services_impl.pipelines.extract_service.load_config', lambda: mock_cfg)
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view._auto_fill_output_path('/test/mods', mode='dual')

    assert Path(view.output_dir_textfield.value).name == 'mods_dual_out'


def test_auto_fill_output_path_falls_back_to_default_on_unknown_mode(monkeypatch):
    """測試 _auto_fill_output_path 在未知模式時 fallback 到 lang_extract"""
    mock_cfg = {
        "extractor": {
            "output_folder_names": {
                "lang_extract": "_custom_lang",
            }
        }
    }
    monkeypatch.setattr('app.services_impl.pipelines.extract_service.load_config', lambda: mock_cfg)
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view._auto_fill_output_path('/test/mods', mode='unknown_mode')

    assert Path(view.output_dir_textfield.value).name == 'mods_custom_lang'


def test_extractor_view_skip_zh_cn_switch_has_label(monkeypatch):
    """測試 skip_zh_cn_switch 有 label 且文字為 '跳過 zh_cn 抽取'"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    assert view.skip_zh_cn_switch.label == '跳過 zh_cn 抽取'
    assert view.skip_zh_cn_switch.value is False


