
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


def test_clear_output_path_shows_snackbar(monkeypatch):
    # 🐛 2026-08-01 user review: 改用 SnackBar,不再寫 log_view
    # user 決定系統訊息走 SnackBar 跳出提示 (而不是掛到 log UI)
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())
    view.output_dir_textfield.value = 'C:/Out'

    # Snapshot SnackBar 之前 overlay (因為 _show_snack_bar 會 append)
    overlay_before = view.page.overlay.copy()

    view.clear_output_path()

    assert view.output_dir_textfield.value == ''
    # 驗證有 SnackBar 被 append 到 page.overlay
    added_snackbars = [
        c for c in view.page.overlay[len(overlay_before):]
        if isinstance(c, ft.SnackBar)
    ]
    assert len(added_snackbars) >= 1, (
        f"回歸:clear_output_path 應該呼叫 _show_snack_bar 但 page.overlay 沒新增 SnackBar"
    )
    # 驗證 SnackBar 內容含「已清除輸出路徑」
    last_snackbar = added_snackbars[-1]
    # SnackBar 內部 content 結構是 ft.Text(message),.value 才是文字
    snack_text = last_snackbar.content
    assert hasattr(snack_text, 'value'), (
        f"回歸:SnackBar 內應是 ft.Text,但 {type(snack_text).__name__} 沒 value 屬性"
    )
    assert '已清除輸出路徑' in snack_text.value, (
        f"回歸:SnackBar 內容應該含「已清除輸出路徑」,實際 {snack_text.value!r}"
    )


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


def test_extractor_view_logs_panel_hidden(monkeypatch):
    """2026-08-01 user review (撤回 S1):日誌面板不渲染到主畫面

    規格書原本 S1 修復要把日誌面板掛回主 UI,但 user 之後實測確認
    日誌面板擠壓主畫面,改變主意撤回 S1。
    改用狀態:
    - view._logs_panel 屬性仍存在 (供 _append_log_line 跟 dialog 用)
    - 但 view.controls 只有 1 個 styled_card (設定)
    - view._logs_panel.visible = False (不渲染到主畫面)
    - self.log_view 屬性仍存在

    重要:此測試防止 future commit 偷偷掛回日誌面板 (再次擠壓主畫面)。
    """
    monkeypatch.setattr('app.views.extractor_view.TaskSession', _Session)
    view = ExtractorView(mock_page(), mock_filepicker())

    # 主畫面只應有 1 個 styled_card (設定),不是 2 個 (無日誌卡片)
    assert len(view.controls) == 1, (
        f"回歸:主畫面不應有日誌卡片 (user 撤回 S1),實際 view.controls 有 {len(view.controls)} 個"
    )
    # view._logs_panel 屬性還在
    assert hasattr(view, '_logs_panel'), (
        "回歸:view._logs_panel 屬性應還存在 (供 dialog 內用)"
    )
    # 但 _logs_panel.visible = False (不渲染到主畫面)
    assert view._logs_panel.visible is False, (
        "回歸:view._logs_panel.visible=False (撤回 S1,不渲染到主畫面)"
    )
    # self.log_view 仍建構 (供 _append_log_line 跟 dialog 用)
    assert hasattr(view, 'log_view'), (
        "回歸:view.log_view 屬性應存在 (供 _append_log_line 跟 dialog 用)"
    )


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


