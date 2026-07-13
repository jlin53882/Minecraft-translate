
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


def test_update_stats_from_log_counts_success_warning_failure(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view._update_stats_from_log('已檢查 10/10 個 JAR 檔案。\n  - 新提取或更新的檔案: 3 個\n  - 因內容相同而跳過的檔案: 5 個')
    view._update_stats_from_log('[ERROR] 提取某個檔案時產生例外')

    assert view._extraction_stats['success'] == 10
    assert view._extraction_stats['warnings'] == 5
    assert view._extraction_stats['failures'] == 1
    assert view._extraction_stats['total_files'] == 3


def test_extractor_view_mods_dir_textfield_exists(monkeypatch):
    """測試 mods_dir_textfield 存在且可設定"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view.mods_dir_textfield.value = 'C:/Mods'
    assert view.mods_dir_textfield.value == 'C:/Mods'
    assert view.mods_dir_textfield.hint_text == './mods 或 %USERPROFILE%/Mods'


def test_extractor_view_status_text_and_progress_bar(monkeypatch):
    """測試 status_text 和 progress_bar 存在"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    assert view.status_text.value == '狀態：閒置'
    assert isinstance(view.progress_bar, ft.ProgressBar)
    assert view.progress_bar.visible is True
    assert view.progress_bar.value == 0


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


def test_extractor_view_update_stats_resets_counters(monkeypatch):
    """測試 _update_stats_from_log 的計數邏輯"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    view._extraction_stats = {"success": 0, "warnings": 0, "failures": 0, "total_files": 0}

    view._update_stats_from_log('已檢查 8/8 個 JAR 檔案。\n  - 新提取或更新的檔案: 5 個\n  - 因內容相同而跳過的檔案: 2 個')
    view._update_stats_from_log('[ERROR] 嚴重錯誤')

    assert view._extraction_stats['success'] == 8
    assert view._extraction_stats['warnings'] == 2
    assert view._extraction_stats['failures'] == 1
    assert view._extraction_stats['total_files'] == 5


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
    assert len(view.log_view._list_view.controls) >= 1


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


def test_extractor_view_show_extraction_summary_exists(monkeypatch):
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())
    assert hasattr(view, '_show_extraction_summary')


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


def test_extractor_view_progress_pct_label(monkeypatch):
    """測試 _progress_pct 百分比標籤存在且初始為 '0%'"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    assert hasattr(view, '_progress_pct')
    assert view._progress_pct.value == '0%'
    assert isinstance(view._progress_pct, ft.Text)


def test_extractor_view_stats_badge_texts(monkeypatch):
    """測試統計徽章文字存在（_stats_success, _stats_warnings, _stats_failures）"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    assert hasattr(view, '_stats_success')
    assert hasattr(view, '_stats_warnings')
    assert hasattr(view, '_stats_failures')
    assert view._stats_success.value == '0'
    assert view._stats_warnings.value == '0'
    assert view._stats_failures.value == '0'


def test_extractor_view_skip_zh_cn_switch_has_label(monkeypatch):
    """測試 skip_zh_cn_switch 有 label 且文字為 '跳過 zh_cn 抽取'"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    assert view.skip_zh_cn_switch.label == '跳過 zh_cn 抽取'
    assert view.skip_zh_cn_switch.value is False


def test_extractor_view_main_layout_is_column(monkeypatch):
    """測試 build_main_layout 回傳 ft.Column（單欄垂直）"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    from app.views.extractor.extractor_panels import build_main_layout
    layout = build_main_layout(view)
    assert isinstance(layout, ft.Column)
    assert layout.scroll == ft.ScrollMode.ADAPTIVE


def test_extractor_view_logs_panel_has_fixed_height(monkeypatch):
    """測試日誌面板的 log_view Container 有固定高度 350"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    from app.views.extractor.extractor_panels import build_logs_panel
    panel = build_logs_panel(view)
    log_container = panel.controls[1]
    assert log_container.height == 350


def test_extractor_view_status_bar_has_left_border(monkeypatch):
    """測試狀態列有 4px 左側邊線"""
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    from app.views.extractor.extractor_panels import build_logs_panel
    panel = build_logs_panel(view)
    status_bar = panel.controls[0]
    assert status_bar.border is not None
    assert status_bar.border.left.width == 4
    assert status_bar.border.left.color == ft.Colors.GREY_400
