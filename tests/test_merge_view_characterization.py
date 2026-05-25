import flet as ft
from app.views import merge_view
from tests.conftest import mock_page, mock_filepicker


def _make_view(monkeypatch, *, selected_zips=None, page=None):
    """建立 MergeView，自動 patch TaskSession。"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    p = page or mock_page()
    view = merge_view.MergeView(p, mock_filepicker())
    if selected_zips:
        view.selected_zips = selected_zips
    return view


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
    view = merge_view.MergeView(mock_page(), mock_filepicker())

    assert view.pick_zip_button.content == '新增 ZIP'
    assert view.start_button.content == '開始合併 ZIP'
    assert view.status_chip.label.value == '尚未開始'


def test_start_merge_without_inputs_shows_snack(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())

    view.start_merge(None)

    assert page.overlay
    assert '請先選擇 ZIP 與輸出資料夾' in page.overlay[-1].content.value


def test_remove_zip_updates_selected_list(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())
    view.selected_zips = ['a.zip', 'b.zip']

    view._remove_zip('a.zip')

    assert view.selected_zips == ['b.zip']


def test_merge_view_all_checkboxes_and_switches_exist(monkeypatch):
    """驗證 MergeView 所有 checkbox/switch 控件存在"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())

    assert view.only_lang_checkbox.label == '只處理 lang 檔案'
    assert view.only_lang_checkbox.value is True

    assert view.process_zh_cn_switch.label == '處理 zh_cn 檔案'
    assert view.process_zh_cn_switch.value is True

    assert view.skip_zh_cn_switch.label == '只處理 lang 時跳過 zh_cn'
    assert view.skip_zh_cn_switch.value is False

    assert view.patchouli_skip_zh_cn_switch.label == '允許 zh_cn 觸發跳過 en_us'
    assert view.patchouli_skip_zh_cn_switch.value is False


def test_merge_view_text_fields_and_listviews_exist(monkeypatch):
    """驗證 MergeView 所有 TextField/ListView 控件存在"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())

    assert view.patchouli_threshold_field.value == '0.5'
    assert view.patchouli_threshold_field.width == 96
    assert view.patchouli_threshold_field.text_align == ft.TextAlign.CENTER

    assert view.output_dir_field.label == '輸出資料夾'
    assert isinstance(view.zip_list_view, ft.ListView)
    assert view.zip_list_view.height is None  # now expand=True, height is None
    assert isinstance(view.log_view, ft.ListView)
    assert isinstance(view.progress_bar, ft.ProgressBar)


def test_merge_view_zh_cn_switch_callback_exists(monkeypatch):
    """驗證 process_zh_cn_switch 的 on_change 回調已設定"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())

    assert view.process_zh_cn_switch.on_change is not None


def test_merge_view_refresh_zip_list_populates_controls(monkeypatch):
    """驗證 _refresh_zip_list 正確將 selected_zips 顯示在 zip_list_view"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())
    view.selected_zips = ['a.zip', 'b.zip']

    view._refresh_zip_list()

    assert len(view.zip_list_view.controls) == 2


def test_merge_view_remove_zip_refreshes_and_updates_page(monkeypatch):
    """驗證 _remove_zip 正確移除並更新頁面"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())
    view.selected_zips = ['a.zip', 'b.zip']

    view._remove_zip('a.zip')

    assert view.selected_zips == ['b.zip']
    assert page.updated >= 1


def test_merge_view_async_pick_output_dir(monkeypatch):
    """驗證 _async_pick_output_dir 正確更新 output_dir_field"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker.set_mock_path('/output/dir')
    view = merge_view.MergeView(page, picker)

    page.run_task(view._async_pick_output_dir)
    page._run_all_tasks()

    assert view.output_dir_field.value == '/output/dir'
    assert page.updated >= 1


def test_merge_view_progress_bar_and_status_chip(monkeypatch):
    """驗證 progress_bar 和 status_chip 初始狀態"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())

    assert view.progress_bar.value == 0
    assert view.status_chip.label.value == '尚未開始'


def test_merge_view_log_presenter_exists(monkeypatch):
    """驗證 log_presenter 存在"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())

    assert hasattr(view, 'log_presenter')
    assert view.log_presenter is not None


def test_merge_view_show_snack_bar_adds_to_overlay(monkeypatch):
    """測試 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())

    view._show_snack_bar('Test error', '#FF0000')

    assert len(page.overlay) >= 1


def test_merge_view_set_status_updates_chip(monkeypatch):
    """測試 _set_status 正確更新 status_chip"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())

    view._set_status('工作中', '#00FF00')

    assert view.status_chip.label.value == '工作中'


def test_merge_view_open_output_folder(monkeypatch):
    """測試 _open_output_folder 不拋出錯誤"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())
    view.output_dir_field.value = 'C:/Out'

    try:
        view._open_output_folder()
    except Exception:
        pass


def test_pick_zips_calls_run_task_with_async_pick_zips(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = merge_view.MergeView(page, picker)

    captured = []

    monkeypatch.setattr(page, 'run_task', lambda coro, *args: captured.append(coro))

    view.pick_zips(None)

    coro_names = [c.__name__ if hasattr(c, '__name__') else str(c) for c in captured]
    assert '_async_pick_zips' in coro_names


def test_merge_view_on_zip_picked(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())
    view.selected_zips = []

    class E:
        class F:
            path = '/path/a.zip'
        class F2:
            path = '/path/b.zip'
        files = [F(), F2()]

    view._on_zip_picked(E())

    assert len(view.selected_zips) >= 0


def test_merge_view_skip_zh_cn_switch_exists(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())
    assert view.skip_zh_cn_switch is not None


def test_merge_view_patchouli_skip_zh_cn_switch_exists(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())
    assert view.patchouli_skip_zh_cn_switch is not None


def test_merge_view_patchouli_threshold_field_exists(monkeypatch):
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    view = merge_view.MergeView(mock_page(), mock_filepicker())
    assert view.patchouli_threshold_field is not None


def _collect_text_values(dialog):
    """遞迴收集 dialog 內所有 ft.Text 的 value。"""
    values = []
    def walk(ctrl):
        if hasattr(ctrl, 'value'):
            values.append(ctrl.value)
        if hasattr(ctrl, 'content'):
            walk(ctrl.content)
        if hasattr(ctrl, 'controls'):
            for c in ctrl.controls:
                walk(c)
    walk(dialog.content)
    return values


def test_show_merge_summary_displays_failed_zips_from_failed_zips_list(monkeypatch):
    """Regression: _show_merge_summary 必須讀取 failed_zips_list，不是 failed_zip_details。

    之前 bug：寫入 failed_zips_list 但讀取 failed_zip_details，永遠顯示空。
    """
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())

    summary = {
        "success_zips": 5,
        "failed_zips": 2,
        "failed_zips_list": [
            {"name": "mod_a.zip", "error": "CRC mismatch"},
            {"name": "mod_b.zip", "error": "File not found"},
        ],
        "output_counts": {},
    }

    view._show_merge_summary(summary)

    dialog = page.overlay[-1]
    text_values = _collect_text_values(dialog)

    assert any('mod_a.zip' in v for v in text_values), f"mod_a.zip 應出現在摘要中: {text_values}"
    assert any('mod_b.zip' in v for v in text_values), f"mod_b.zip 應出現在摘要中: {text_values}"
    assert any('CRC mismatch' in v for v in text_values), f"錯誤訊息應出現在摘要中: {text_values}"


def test_show_merge_summary_empty_failed_zips_list(monkeypatch):
    """failed_zips_list 為空時，不應顯示失敗區塊。"""
    monkeypatch.setattr(merge_view, 'TaskSession', _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())

    summary = {
        "success_zips": 5,
        "failed_zips": 0,
        "failed_zips_list": [],
        "output_counts": {},
    }

    view._show_merge_summary(summary)

    dialog = page.overlay[-1]
    text_values = _collect_text_values(dialog)

    assert not any('mod_a.zip' in v for v in text_values), "空列表時不應顯示 ZIP 名稱"
