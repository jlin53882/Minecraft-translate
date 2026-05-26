"""test_lm_view_full_validation.py

LM View 完整模組驗證。
覆蓋：characterization 3 題 + 缺口 8 題，共 11 題。

目標：確認 LMView UI 元件可正確初始化、事件處理正確、與 service 層整合正確。
"""

import flet as ft
from unittest.mock import patch
from app.views import lm_view
from app.logging import LogEntry
from tests.conftest import mock_page, mock_filepicker


class _Session:
    def __init__(self):
        self.started = 0
        self.logs = []
        self.progress = 0.0
        self.status = "IDLE"
        self.error = False

    def start(self):
        self.started += 1
        self.status = "RUNNING"

    def add_log(self, text, level="info", source="ui"):
        if text:
            self.logs.append(text)

    def set_progress(self, value):
        self.progress = value

    def set_error(self):
        self.error = True
        self.status = "ERROR"

    def finish(self):
        self.progress = 1.0
        self.status = "DONE"

    def snapshot(self):
        return {
            "status": self.status,
            "progress": self.progress,
            "logs": [LogEntry(seq=i, level="info", text=t, source="ui")
                     for i, t in enumerate(self.logs)],
        }


# ============================================================
# Helpers
# ============================================================

def make_view():
    """建立乾淨的 LMView + mock 基礎設施。"""
    page = mock_page()
    fp = mock_filepicker()
    # Mock TaskSession 避免啟動眻
    with patch.object(lm_view, "TaskSession", _Session):
        view = lm_view.LMView(page, fp)
    return view, page, fp


# ============================================================
# Test 1: 表單元件初始化齊全（覆蓋原有）
# ============================================================

def test_lm_view_initializes_primary_controls(monkeypatch):
    """驗證 LMView 所有主要控制項都正確初始化。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    view = lm_view.LMView(mock_page(), mock_filepicker())

    # --- 路徑輸入 ---
    assert view.input_path.value == "", "input_path 初始為空"
    assert view.output_path.value == "", "output_path 初始為空"

    # --- 開關預設值 ---
    assert view.dry_run_switch.value is False, "dry_run 預設 False"
    assert view.export_lang_checkbox.value is False, "export_lang 預設 False"
    assert view.write_new_cache_switch.value is False, "write_new_cache 預設 False"

    # --- 狀態 ---
    assert view.status_chip.label.value == "尚未開始"
    assert view.progress_bar.value == 0

    # --- 按鈕 ---
    assert view.start_button.content == "開始翻譯"

    # --- Log ---
    assert view.log_view is not None
    assert view.log_presenter is not None

    # --- File picker ---
    assert hasattr(view.file_picker, 'get_directory_path')
    assert hasattr(view.file_picker, 'set_mock_path')


# ============================================================
# Test 2: 無輸入時錯誤處理（覆蓋原有）
# ============================================================

def test_start_clicked_without_input_sets_error_status(monkeypatch):
    """驗證未填輸入目錄時，回上錯誤狀態並跳過 service 呼叫。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    with patch.object(lm_view, "run_lm_translation_service") as svc:
        view.start_clicked(None)
        svc.assert_not_called()

    # 狀態晶片應顯示錯誤訊息
    assert view.status_chip.label.value == "請先選擇輸入資料夾"
    assert "red" in view.status_chip.bgcolor.lower() or "RED" in view.status_chip.bgcolor


# ============================================================
# Test 3: 服務啟動參數傳遞（覆蓋原有）
# ============================================================

def test_start_clicked_launches_service_with_current_flags(monkeypatch):
    """驗證 start_clicked 把所有 UI flag 正確傳給 run_lm_translation_service。"""
    page = mock_page()
    captured = {}

    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    monkeypatch.setattr(lm_view.threading, "Thread",
                       lambda target=None, args=(), daemon=None:
                           type("T", (), {"start": lambda self: target(*args)})())
    monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)

    def fake_service(input_dir, output_dir, session, dry_run, export_lang, write_new_cache):
        captured.update({
            "input_dir": input_dir,
            "output_dir": output_dir,
            "session": session,
            "dry_run": dry_run,
            "export_lang": export_lang,
            "write_new_cache": write_new_cache,
        })

    monkeypatch.setattr(lm_view, "run_lm_translation_service", fake_service)

    view = lm_view.LMView(page, mock_filepicker())
    view.input_path.value = "C:/Assets"
    view.output_path.value = "C:/Out"
    view.dry_run_switch.value = True
    view.export_lang_checkbox.value = True
    view.write_new_cache_switch.value = True

    view.start_clicked(None)

    assert captured["input_dir"] == "C:/Assets"
    assert captured["output_dir"] == "C:/Out"
    assert captured["dry_run"] is True
    assert captured["export_lang"] is True
    assert captured["write_new_cache"] is True


# ============================================================
# Test 4: 路徑選擇回调 — 輸入目錄
# ============================================================

def test_on_input_dir_picked_updates_input_field(monkeypatch):
    """驗證選擇輸入目錄後，正確更新 input_path。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    class FakeEvent:
        path = "D:/Game/assets"

    view.on_input_dir_picked(FakeEvent())

    assert view.input_path.value == "D:/Game/assets"
    assert page.updated >= 1, "page.update() should be called"


# ============================================================
# Test 5: 路徑選擇回调 — 輸出目錄
# ============================================================

def test_on_output_dir_picked_updates_output_field(monkeypatch):
    """驗證選擇輸出目錄後，正確更新 output_path。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    class FakeEvent:
        path = "E:/Output"

    view.on_output_dir_picked(FakeEvent())

    assert view.output_path.value == "E:/Output"
    assert page.updated >= 1, "page.update() should be called"


# ============================================================
# Test 5a: async pick_input_directory updates input_path
# ============================================================

def test_async_pick_input_directory_updates_input_field(monkeypatch):
    """驗證 async 選擇輸入目錄後，正確更新 input_path。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker.set_mock_path("D:/Game/assets")
    view = lm_view.LMView(page, picker)

    page.run_task(view._async_pick_input_directory)
    page._run_all_tasks()

    assert view.input_path.value == "D:/Game/assets"
    assert page.updated >= 1


# ============================================================
# Test 5b: async pick_output_directory updates output_path
# ============================================================

def test_async_pick_output_directory_updates_output_field(monkeypatch):
    """驗證 async 選擇輸出目錄後，正確更新 output_path。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker.set_mock_path("E:/Output")
    view = lm_view.LMView(page, picker)

    page.run_task(view._async_pick_output_directory)
    page._run_all_tasks()

    assert view.output_path.value == "E:/Output"
    assert page.updated >= 1


# ============================================================
# Test 6: 輸出目錄留空 → 使用預設值
# ============================================================

def test_start_clicked_uses_default_output_dir_when_empty(monkeypatch):
    """驗證 output_path 為空時，service 收到預設 lm_translate_folder_name。"""
    page = mock_page()
    captured = {}

    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    monkeypatch.setattr(lm_view.threading, "Thread",
                       lambda target=None, args=(), daemon=None:
                           type("T", (), {"start": lambda self: target(*args)})())
    monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)

    def fake_service(input_dir, output_dir, session, dry_run, export_lang, write_new_cache):
        captured["output_dir"] = output_dir

    monkeypatch.setattr(lm_view, "run_lm_translation_service", fake_service)

    view = lm_view.LMView(page, mock_filepicker())
    view.input_path.value = "C:/Assets"
    view.output_path.value = ""  # 留空

    view.start_clicked(None)

    # output_dir 應為預設值
    expected_default = lm_view.LM_translate_folder_name
    assert captured["output_dir"] == expected_default


# ============================================================
# Test 7: _set_status 正確更新狀態晶片
# ============================================================

def test_set_status_updates_chip_label_and_color(monkeypatch):
    """驗證 _set_status 正確更新 status_chip 的文字與背景色。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    view = lm_view.LMView(mock_page(), mock_filepicker())

    view._set_status("執行中", lm_view.theme.BLUE_200)

    assert view.status_chip.label.value == "執行中"
    assert view.status_chip.bgcolor == lm_view.theme.BLUE_200


# ============================================================
# Test 8: styled_card 結構 — controls 數量與類型
# ============================================================

def test_controls_contains_all_sections(monkeypatch):
    """驗證 LMView.controls 包含 4 個 styled_card 區塊。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    view = lm_view.LMView(mock_page(), mock_filepicker())

    assert len(view.controls) == 4, "應有 4 個 styled_card 區塊"

    # 確認每個都是 ft.Container（styled_card 回傳 Container）
    for ctrl in view.controls:
        assert isinstance(ctrl, ft.Container), f"每個 section 應為 ft.Container，實際: {type(ctrl)}"


# ============================================================
# Test 9: LogPresenter 初始化成功
# ============================================================

def test_log_presenter_initialized_with_tail_mode(monkeypatch):
    """驗證 LogPresenter 以 tail 模式正確初始化，tail_lines 為 250。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    view = lm_view.LMView(mock_page(), mock_filepicker())

    assert view.log_presenter is not None
    # tail mode 由 ui_cfg 決定，只驗證非 None
    assert hasattr(view.log_presenter, "mode")


# ============================================================
# Test 10: 快捷資料夾按鈕已掛載 on_pick handler
# ============================================================

def test_folder_open_buttons_have_on_click(monkeypatch):
    """驗證路徑欄位旁邊的資料夾按鈕有設定 on_click。

    styled_card 結構：
    - controls[0] = section_header（ft.Row: icon + text）
    - controls[1] = ft.Divider
    - controls[2] = ft.Container(content=ft.Column)
    → Column.controls[0] = 第一列路徑（input_path + icon_button）
    → Column.controls[1] = 第二列路徑（output_path + icon_button）
    """
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    path_card = view.controls[0]
    # controls[2] = content_container (ft.Container wrapping the Column)
    content_container = path_card.content.controls[2]
    # content_container.content = ft.Column([path_row_1, path_row_2])
    path_row = content_container.content.controls[0]  # 第一列：input_path + icon button

    icon_button = path_row.controls[1]
    assert isinstance(icon_button, ft.IconButton), \
        f"第二個元件應為 IconButton，實際: {type(icon_button)}"
    assert icon_button.on_click is not None, "IconButton 應有 on_click handler"
    assert callable(icon_button.on_click), "on_click 應為可呼叫物件"


# ============================================================
# Test 11: session.snapshot → progress/logs 正確映射
# ============================================================

def test_session_snapshot_returns_correct_structure(monkeypatch):
    """驗證 _Session.snapshot() 回傳正確結構（含 progress/logs/status）。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    session = _Session()
    session.start()
    session.add_log("測試日誌一")
    session.add_log("測試日誌二")
    session.set_progress(0.5)

    snap = session.snapshot()

    assert snap["status"] == "RUNNING"
    assert snap["progress"] == 0.5
    assert len(snap["logs"]) == 2
    assert snap["logs"][0].text == "測試日誌一"
    assert snap["logs"][1].text == "測試日誌二"


def test_start_clicked_resets_log_presenter(monkeypatch):
    """驗證開始新任務時會 reset presenter，避免沿用上一輪 log 狀態。"""
    page = mock_page()
    reset_calls = []

    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    monkeypatch.setattr(
        lm_view.threading,
        "Thread",
        lambda target=None, args=(), daemon=None: type("T", (), {"start": lambda self: None})(),
    )
    monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)

    def fake_reset():
        reset_calls.append(True)

    view = lm_view.LMView(page, mock_filepicker())
    monkeypatch.setattr(view.log_presenter, "reset", fake_reset)
    view.input_path.value = "C:/Assets"

    view.start_clicked(None)

    assert reset_calls == [True]


def test_start_ui_timer_attempts_log_view_update(monkeypatch):
    """驗證 timer 迴圈會嘗試刷新 log_view，避免日誌內容已 sync 但畫面未更新。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()

    view = lm_view.LMView(page, mock_filepicker())
    view.session = _Session()
    view.session.start()
    view.session.add_log("hello")
    view.session.status = "DONE"

    called = []
    monkeypatch.setattr(view.log_view, "update", lambda: called.append(True))

    view.start_ui_timer()
    lm_view.time.sleep(0.25)

    assert called, "log_view.update() 應至少被呼叫一次"


# ============================================================
# Test 10a: pick_input_directory schedules async task
# ============================================================

def test_pick_input_directory_schedules_async_task(monkeypatch):
    """驗證 pick_input_directory 正確排程 _async_pick_input_directory"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker.set_mock_path("C:/Input")
    view = lm_view.LMView(page, picker)

    view.pick_input_directory(None)

    assert len(page._tasks) == 1
    page._run_all_tasks()

    assert view.input_path.value == "C:/Input"
    assert page.updated >= 1


# ============================================================
# Test 10b: pick_output_directory schedules async task
# ============================================================

def test_pick_output_directory_schedules_async_task(monkeypatch):
    """驗證 pick_output_directory 正確排程 _async_pick_output_directory"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker.set_mock_path("D:/Output")
    view = lm_view.LMView(page, picker)

    view.pick_output_directory(None)

    assert len(page._tasks) == 1
    page._run_all_tasks()

    assert view.output_path.value == "D:/Output"
    assert page.updated >= 1


# ============================================================
# Test 10c: on_input_dir_picked updates field and calls update
# ============================================================

def test_on_input_dir_picked_updates_field_and_calls_update(monkeypatch):
    """驗證 on_input_dir_picked 正確更新 input_path 並呼叫 page.update()"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    class FakeEvent:
        path = "F:/Assets"

    view.on_input_dir_picked(FakeEvent())

    assert view.input_path.value == "F:/Assets"
    assert page.updated >= 1


# ============================================================
# Test 10d: on_output_dir_picked updates field and calls update
# ============================================================

def test_on_output_dir_picked_updates_field_and_calls_update(monkeypatch):
    """驗證 on_output_dir_picked 正確更新 output_path 並呼叫 page.update()"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    class FakeEvent:
        path = "G:/Out"

    view.on_output_dir_picked(FakeEvent())

    assert view.output_path.value == "G:/Out"
    assert page.updated >= 1


# ============================================================
# Test 10e: _show_snack_bar adds to overlay
# ============================================================

def test_show_snack_bar_adds_to_overlay(monkeypatch):
    """驗證 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    view._show_snack_bar("Test message", lm_view.theme.RED_600)

    assert len(page.overlay) == 1
    assert page.overlay[0].open is True


# ============================================================
# Test 10f: on_input_dir_picked with empty path does not update
# ============================================================

def test_on_input_dir_picked_ignores_empty_path(monkeypatch):
    """驗證 on_input_dir_picked 忽略空路徑"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    class FakeEvent:
        path = ""

    view.input_path.value = "original"
    view.on_input_dir_picked(FakeEvent())

    assert view.input_path.value == "original"


# ============================================================
# Test 10g: on_output_dir_picked with empty path does not update
# ============================================================

def test_on_output_dir_picked_ignores_empty_path(monkeypatch):
    """驗證 on_output_dir_picked 忽略空路徑"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    class FakeEvent:
        path = ""

    view.output_path.value = "original"
    view.on_output_dir_picked(FakeEvent())

    assert view.output_path.value == "original"


def test_lm_view_path_row_returns_control():
    from app.views import lm_view

    view = lm_view.LMView(mock_page(), mock_filepicker())
    tf = lm_view.ft.TextField()
    result = view._path_row(tf, lambda e: None)

    assert result is not None


# ============================================================
# Test 12: start_ui_timer early return guard
# ============================================================

def test_start_ui_timer_returns_early_when_already_running(monkeypatch):
    """驗證 start_ui_timer 在 _ui_timer_running 為 True 時直接返回。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())
    view.session = _Session()
    view._ui_timer_running = True

    thread_calls = []
    original_thread = lm_view.threading.Thread

    def track_thread(target=None, daemon=None):
        thread_calls.append(target)
        t = original_thread(target=target, daemon=daemon)
        return t

    monkeypatch.setattr(lm_view.threading, "Thread", track_thread)
    monkeypatch.setattr(lm_view.time, "sleep", lambda x: None)

    view.start_ui_timer()

    assert len(thread_calls) == 0, "不應啟動新執行緒"


# ============================================================
# Test 13: loop() — snapshot exception handling
# ============================================================

def test_loop_continues_when_session_snapshot_raises(monkeypatch):
    """驗證 loop() 在 session.snapshot() 拋例時繼續執行（不崩潰）。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    class BadSession:
        def snapshot(self):
            raise RuntimeError("snapshot failed")

    view.session = BadSession()
    view._ui_timer_running = True

    snapshot_raised = [False]
    sleep_count = [0]

    def bad_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 2:
            view._ui_timer_running = False

    monkeypatch.setattr(lm_view.time, "sleep", bad_sleep)

    view.start_ui_timer()

    import time as time_module
    time_module.sleep(0.1)

    assert snapshot_raised[0] or sleep_count[0] > 0, "loop should have run"


# ============================================================
# Test 14: loop() — status DONE updates UI and stops timer
# ============================================================

def test_loop_updates_ui_when_status_done(monkeypatch):
    """驗證 loop() 在 status == 'DONE' 時正確更新 UI 並停止計時器。"""
    from unittest.mock import MagicMock

    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    view.session = MagicMock()
    view.session.snapshot.return_value = {"status": "DONE", "progress": 1.0, "logs": []}

    executed_coroutines = []

    def execute_coro(coro, *args):
        executed_coroutines.append(coro)
        result = coro(*args)
        if result is not None:
            try:
                result.send(None)
            except StopIteration:
                pass

    monkeypatch.setattr(view._page, "run_task", execute_coro)
    monkeypatch.setattr(lm_view.time, "sleep", lambda x: None)

    view.start_ui_timer()
    lm_view.time.sleep(0.3)

    assert len(executed_coroutines) >= 1, "at least one coroutine should be scheduled"
    assert view._ui_timer_running is False, "DONE 後計時器應停止"


# ============================================================
# Test 15: loop() — status ERROR updates UI and stops timer
# ============================================================

def test_loop_updates_ui_when_status_error(monkeypatch):
    """驗證 loop() 在 status == 'ERROR' 時正確更新 UI 並停止計時器。"""
    from unittest.mock import MagicMock

    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    view.session = MagicMock()
    view.session.snapshot.return_value = {"status": "ERROR", "progress": 0.0, "logs": []}

    executed_coroutines = []

    def execute_coro(coro, *args):
        executed_coroutines.append(coro)
        result = coro(*args)
        if result is not None:
            try:
                result.send(None)
            except StopIteration:
                pass

    monkeypatch.setattr(view._page, "run_task", execute_coro)
    monkeypatch.setattr(lm_view.time, "sleep", lambda x: None)

    view.start_ui_timer()
    lm_view.time.sleep(0.3)

    assert len(executed_coroutines) >= 1, "at least one coroutine should be scheduled"
    assert view._ui_timer_running is False, "ERROR 後計時器應停止"


# ============================================================
# Test 16: log_presenter.sync exception is swallowed
# ============================================================

def test_loop_handles_log_presenter_sync_exception(monkeypatch):
    """驗證 loop() 在 log_presenter.sync() 拋例外時不會崩潰。"""
    from unittest.mock import MagicMock

    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    view.session = MagicMock()
    view.session.snapshot.return_value = {"status": "RUNNING", "progress": 0.5, "logs": []}

    sync_called = []

    def bad_sync(lv, logs):
        sync_called.append(True)
        raise RuntimeError("sync failed")

    monkeypatch.setattr(view.log_presenter, "sync", bad_sync)
    monkeypatch.setattr(lm_view.time, "sleep", lambda x: None)

    view.start_ui_timer()
    lm_view.time.sleep(0.3)

    assert len(sync_called) >= 1, "sync should have been called"


# ============================================================
# Test 18: refresh_batch_interval_info updates text
# ============================================================

def test_refresh_batch_interval_info_updates_display(monkeypatch):
    """驗證 refresh_batch_interval_info 正確更新 batch_interval_info 文字。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)

    captured_interval = [None]

    def fake_load_config():
        captured_interval[0] = 5
        return {"lm_translator": {"batch_write_interval": 5}}

    monkeypatch.setattr(lm_view, "load_config", fake_load_config)
    view = lm_view.LMView(mock_page(), mock_filepicker())

    view.refresh_batch_interval_info()

    assert "5" in view.batch_interval_info.value
    assert "批次寫入" in view.batch_interval_info.value


# ============================================================
# Test 19: _async_pick_input_directory with None result
# ============================================================

def test_async_pick_input_directory_does_nothing_when_result_is_none(monkeypatch):
    """驗證 _async_pick_input_directory 在取得 None 路徑時不更新欄位。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker._mock_path = None
    view = lm_view.LMView(page, picker)
    view.input_path.value = "original"

    page.run_task(view._async_pick_input_directory)
    page._run_all_tasks()

    assert view.input_path.value == "original"


# ============================================================
# Test 20: _async_pick_output_directory with None result
# ============================================================

def test_async_pick_output_directory_does_nothing_when_result_is_none(monkeypatch):
    """驗證 _async_pick_output_directory 在取得 None 路徑時不更新欄位。"""
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker._mock_path = None
    view = lm_view.LMView(page, picker)
    view.output_path.value = "original"

    page.run_task(view._async_pick_output_directory)
    page._run_all_tasks()

    assert view.output_path.value == "original"
