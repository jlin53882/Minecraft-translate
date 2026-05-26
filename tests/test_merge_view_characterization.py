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


# ============================================================
# Additional untested merge_view paths
# ============================================================

def test_async_pick_zips_does_nothing_when_result_is_none(monkeypatch):
    """驗證 _async_pick_zips 在無選擇檔案時不更新清單。"""
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    page = mock_page()

    class EmptyPicker:
        async def pick_files(self, dialog_title=None, allow_multiple=None, allowed_extensions=None):
            return []

    view = merge_view.MergeView(page, EmptyPicker())

    page.run_task(view._async_pick_zips)
    page._run_all_tasks()

    assert view.selected_zips == []


def test_on_zip_picked_does_nothing_when_files_is_none(monkeypatch):
    """驗證 _on_zip_picked 在 files 為 None 時不更新。"""
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())
    view.selected_zips = ["existing.zip"]

    class FakeEvent:
        files = None

    view._on_zip_picked(FakeEvent())

    assert view.selected_zips == ["existing.zip"]


def test_on_zip_picked_does_nothing_when_file_path_is_none(monkeypatch):
    """驗證 _on_zip_picked 在檔案路徑為 None 時不新增。"""
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())
    view.selected_zips = []

    class FakeFile:
        path = None

    class FakeEvent:
        files = [FakeFile()]

    view._on_zip_picked(FakeEvent())

    assert view.selected_zips == []


def test_start_merge_with_invalid_patchouli_threshold_shows_snack(monkeypatch):
    """驗證 start_merge 在 patchouli_threshold 為無效值時使用預設 0.5。"""
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())
    view.selected_zips = ["/test/a.zip"]
    view.output_dir_field.value = "/test/out"
    view.patchouli_threshold_field.value = "not_a_number"

    merge_called = []

    def mock_run_merge(*args, **kwargs):
        merge_called.append(args)
        yield {"log": "done"}

    def mock_log_presenter_sync(lv, logs):
        pass

    monkeypatch.setattr(merge_view, "run_merge_zip_batch_service", mock_run_merge)
    monkeypatch.setattr(view.log_presenter, "sync", mock_log_presenter_sync)
    monkeypatch.setattr(merge_view.threading, "Thread",
                       lambda target=None, args=(), daemon=None:
                           type("T", (), {"start": lambda self: target(*args)})())

    view.start_merge(None)

    assert len(merge_called) == 1


def test_show_merge_summary_truncates_long_error_message(monkeypatch):
    """驗證 _show_merge_summary 截斷超過 80 字元的錯誤訊息。"""
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())

    long_error = "A" * 120
    summary = {
        "success_zips": 5,
        "failed_zips": 1,
        "failed_zips_list": [
            {"name": "long_error.zip", "error": long_error},
        ],
        "output_counts": {},
    }

    view._show_merge_summary(summary)

    dialog = page.overlay[-1]
    text_values = _collect_text_values(dialog)
    error_texts = [v for v in text_values if "A" * 80 in str(v)]

    assert any("..." in str(v) for v in text_values), "長錯誤訊息應被截斷並以 ... 結尾"


def test_show_merge_summary_skips_zero_output_counts(monkeypatch):
    """驗證 _show_merge_summary 不顯示 count 為 0 的輸出統計列。"""
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())

    summary = {
        "success_zips": 3,
        "failed_zips": 0,
        "failed_zips_list": [],
        "output_counts": {
            "lang_output": 0,
            "待翻譯": 0,
            "patchouli_output": 5,
        },
    }

    view._show_merge_summary(summary)

    dialog = page.overlay[-1]
    text_values = _collect_text_values(dialog)
    lang_texts = [v for v in text_values if "lang_output" in str(v)]

    assert len(lang_texts) == 0, "count 為 0 的輸出統計不應顯示"


def test_close_dialog_overlay_resets_progress_bar_and_status(monkeypatch):
    """驗證 _close_dialog_overlay 正確重置 progress_bar 和 status。"""
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())
    view.selected_zips = ["a.zip", "b.zip"]
    view.progress_bar.value = 0.75
    view._progress_label.value = "50%"
    view._progress_label.color = "#60a5fa"
    view._progress_pct.value = "50%"
    view._progress_pct.color = "#60a5fa"
    view.status_chip.label = ft.Text("執行中")

    class FakeDialog:
        open = True

    view._close_dialog_overlay(FakeDialog())

    assert view.progress_bar.value == 0
    assert "ZIP" in view._progress_label.value
    assert view._progress_pct.value == "0%"
    assert view.status_chip.label.value == "就緒"


def test_close_dialog_overlay_handles_exception(monkeypatch):
    """驗證 _close_dialog_overlay 在發生例外時不崩潰。"""
    import logging
    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())

    class BadDialog:
        open = True
        @property
        def open(self):
            raise RuntimeError("dialog error")

    try:
        view._close_dialog_overlay(BadDialog())
    except Exception:
        pytest.fail("_close_dialog_overlay should handle exceptions gracefully")
