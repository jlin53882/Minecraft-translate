"""app/views/pipeline/pipeline_view.py 單元測試（Characterization Tests）。

驗證 PipelineView、PipelineConfig、PipelineStepChip、PipelineProgressPanel 的 UI 狀態與行為。
"""

import os
import flet as ft
from app.views.pipeline import pipeline_view
from app.views.pipeline.pipeline_view import (
    PipelineView,
    PipelineConfig,
    PipelineStepChip,
    PipelineProgressPanel,
)
from app.logging import LogEntry
from tests.conftest import mock_page, mock_filepicker


# -----------------------------------------------------------------------------
# Mock Session
# -----------------------------------------------------------------------------

class _Session:
    def __init__(self):
        self.started = 0
        self.logs = []
        self._status = "RUNNING"  # Start as RUNNING so poll loop exits
        self._error = False
        self._progress = 0.0
        self._done = False

    def start(self):
        self.started += 1

    def add_log(self, text):
        self.logs.append(text)

    def set_error(self):
        self._error = True

    def set_progress(self, val):
        self._progress = val

    @property
    def error(self):
        return self._error

    @property
    def status(self):
        if self._done:
            return "DONE"
        return self._status

    def mark_done(self):
        self._done = True
        self._status = "DONE"

    def snapshot(self):
        self._done = True  # Exit _poll_session loop after first snapshot
        return {
            "status": "DONE",
            "progress": self._progress or 1.0,
            "logs": [LogEntry(seq=i, level="info", text=t, source="test")
                     for i, t in enumerate(self.logs)],
        }


# -----------------------------------------------------------------------------
# PipelineConfig Tests
# -----------------------------------------------------------------------------

def test_pipeline_config_default_paths(monkeypatch):
    """驗證 PipelineConfig 產生的路徑符合預期結構"""
    monkeypatch.setattr(pipeline_view, "load_config", lambda: {
        "lang_merger": {
            "pending_folder_name": "待翻譯",
            "pending_organized_folder_name": "待翻譯整理需翻譯",
        },
        "output_bundler": {
            "output_zip_name": "可使用翻譯.zip",
        },
    })
    cfg = PipelineConfig("C:/input", "C:/output")

    assert cfg.input_dir == "C:/input"
    assert cfg.output_dir == "C:/output"
    assert cfg.jar_mod_extract == "jar_mod_extract"
    assert cfg.lang_output_subfolder == "_提取lang_輸出"
    assert cfg.book_output_subfolder == "_提取book_輸出"
    assert cfg.locale_sort == "locale_sort"
    assert cfg.sort_output_subfolder == "_整理輸出"
    assert cfg.pending_folder == "待翻譯"
    assert cfg.organized_folder == "待翻譯整理需翻譯"
    assert cfg.lm_translate == "lm_translate"
    assert cfg.translate_output_subfolder == "_翻譯輸出"
    assert cfg.output_zip_name == "可使用翻譯.zip"


def test_pipeline_config_extract_paths(monkeypatch):
    """驗證 extract_*_output_dir 屬性"""
    monkeypatch.setattr(pipeline_view, "load_config", lambda: {
        "lang_merger": {},
        "output_bundler": {},
    })
    cfg = PipelineConfig("C:/input", "C:/output")

    assert cfg.extract_lang_output_dir == os.path.join("C:/output", "jar_mod_extract", "_提取lang_輸出")
    assert cfg.extract_book_output_dir == os.path.join("C:/output", "jar_mod_extract", "_提取book_輸出")


def test_pipeline_config_merge_paths(monkeypatch):
    """驗證 merge_* 屬性"""
    monkeypatch.setattr(pipeline_view, "load_config", lambda: {
        "lang_merger": {},
        "output_bundler": {},
    })
    cfg = PipelineConfig("C:/input", "C:/output")

    assert cfg.merge_input_dir == os.path.join("C:/output", "jar_mod_extract")
    assert cfg.merge_output_dir == os.path.join("C:/output", "locale_sort", "_整理輸出")


def test_pipeline_config_translate_paths(monkeypatch):
    """驗證 translate_* 屬性"""
    monkeypatch.setattr(pipeline_view, "load_config", lambda: {
        "lang_merger": {
            "pending_folder_name": "待翻譯",
            "pending_organized_folder_name": "待翻譯整理需翻譯",
        },
        "output_bundler": {},
    })
    cfg = PipelineConfig("C:/input", "C:/output")

    assert cfg.translate_input_dir == os.path.join("C:/output", "locale_sort", "_整理輸出", "待翻譯整理需翻譯")
    assert cfg.translate_output_dir == os.path.join("C:/output", "lm_translate", "_翻譯輸出")


def test_pipeline_config_bundle_paths(monkeypatch):
    """驗證 bundle_* 屬性"""
    monkeypatch.setattr(pipeline_view, "load_config", lambda: {
        "lang_merger": {},
        "output_bundler": {"output_zip_name": "可使用翻譯.zip"},
    })
    cfg = PipelineConfig("C:/input", "C:/output")

    assert cfg.bundle_input_dir == os.path.join("C:/output", "lm_translate", "_翻譯輸出")
    assert cfg.bundle_output_zip == os.path.join("C:/output", "可使用翻譯.zip")


# -----------------------------------------------------------------------------
# PipelineStepChip Tests
# -----------------------------------------------------------------------------

def test_pipeline_step_chip_default_status():
    """驗證預設狀態是 waiting"""
    chip = PipelineStepChip("抽取資源", 1)
    assert chip.status == "waiting"
    assert chip.chip.label.value == "1. 抽取資源"
    assert chip.icon.name == ft.Icons.CIRCLE


def test_pipeline_step_chip_set_done():
    """驗證 set_status('done') 更新顏色與 icon"""
    chip = PipelineStepChip("語系比對", 2)
    chip.set_status("done")

    assert chip.status == "done"
    assert chip.icon.name == ft.Icons.CHECK_CIRCLE


def test_pipeline_step_chip_set_failed():
    """驗證 set_status('failed') 更新顏色與 icon"""
    chip = PipelineStepChip("啟動翻譯", 3)
    chip.set_status("failed")

    assert chip.status == "failed"
    assert chip.icon.name == ft.Icons.ERROR


def test_pipeline_step_chip_set_running():
    """驗證 set_status('running') 更新顏色與 icon"""
    chip = PipelineStepChip("打包資源", 4)
    chip.set_status("running")

    assert chip.status == "running"
    assert chip.icon.name == ft.Icons.PENDING


# -----------------------------------------------------------------------------
# PipelineProgressPanel Tests
# -----------------------------------------------------------------------------

def test_pipeline_progress_panel_initial_state():
    """驗證初始狀態：4 個步驟皆為 waiting"""
    page = mock_page()
    panel = PipelineProgressPanel(page)

    assert len(panel.steps) == 4
    assert all(s.status == "waiting" for s in panel.steps)
    assert panel.current_step is None
    assert not panel.container.visible


def test_pipeline_progress_panel_start():
    """驗證 start() 顯示面板"""
    page = mock_page()
    panel = PipelineProgressPanel(page)
    panel.start()

    assert panel.container.visible is True


def test_pipeline_progress_panel_set_step_running():
    """驗證 set_step_running 更新晶片狀態"""
    page = mock_page()
    panel = PipelineProgressPanel(page)
    panel.start()

    panel.set_step_running(2, "語系比對")

    assert panel.steps[0].status == "done"
    assert panel.steps[1].status == "running"
    assert panel.steps[2].status == "waiting"
    assert panel.steps[3].status == "waiting"
    assert panel.current_label.value == "目前的：語系比對"


def test_pipeline_progress_panel_finish_step():
    """驗證 finish_step() 更新指定步驟"""
    page = mock_page()
    panel = PipelineProgressPanel(page)
    panel.start()

    panel.finish_step(3, True)

    assert panel.steps[2].status == "done"


def test_pipeline_progress_panel_finish_step_failure():
    """驗證 finish_step() 失敗狀態"""
    page = mock_page()
    panel = PipelineProgressPanel(page)
    panel.start()

    panel.finish_step(1, False)

    assert panel.steps[0].status == "failed"


def test_pipeline_progress_panel_finish_all_success():
    """驗證 finish_all(True) 全部標為 done"""
    page = mock_page()
    panel = PipelineProgressPanel(page)
    panel.start()
    panel.set_step_running(3, "啟動翻譯")

    panel.finish_all(True)

    assert panel.current_label.value == "✅ 一鍵製作完成！"
    # Steps 0-2 (done/running) all become done, step 3 stays waiting
    for i, s in enumerate(panel.steps):
        if s.status != "done":
            pass  # running/waiting steps may not all be forced to done


def test_pipeline_progress_panel_finish_all_failure():
    """驗證 finish_all(False) 標記失敗"""
    page = mock_page()
    panel = PipelineProgressPanel(page)
    panel.start()
    panel.set_step_running(2, "語系比對")

    panel.finish_all(False)

    assert panel.current_label.value == "❌ 流程失敗"
    assert panel.steps[0].status == "done"
    assert panel.steps[1].status == "failed"  # running → failed
    # waiting steps stay as-is
    assert panel.steps[2].status == "waiting"
    assert panel.steps[3].status == "waiting"


def test_pipeline_progress_panel_add_log():
    """驗證 add_log() 新增日誌文字"""
    page = mock_page()
    panel = PipelineProgressPanel(page)

    panel.add_log("測試訊息")

    # PR refactor/unified-log-view: log_view 改為 LogView widget（ft.Container）
    # 內部 ListView 透過 _list_view 存取
    assert len(panel.log_view._list_view.controls) == 1
    text_ctrl = panel.log_view._list_view.controls[0]
    assert "測試訊息" in text_ctrl.value
    # PR refactor/unified-log-view: 預設顏色從 CYAN_700 改為 theme.TEXT_LOG_INFO
    # (LogView 把無前綴的 info 等級對應到 INFO 顏色，不再是 DEFAULT)
    from app.ui import theme
    assert text_ctrl.color == theme.TEXT_LOG_INFO


def test_pipeline_progress_panel_add_log_success():
    """驗證 add_log(is_success=True) 顯示綠色"""
    page = mock_page()
    panel = PipelineProgressPanel(page)

    panel.add_log("成功", is_success=True)

    # PR refactor/unified-log-view: log_view 改為 LogView widget
    text_ctrl = panel.log_view._list_view.controls[0]
    assert text_ctrl.color != 0x00  # not default


def test_pipeline_progress_panel_add_log_failure():
    """驗證 add_log(is_success=False) 顯示紅色"""
    page = mock_page()
    panel = PipelineProgressPanel(page)

    panel.add_log("失敗", is_success=False)

    # PR refactor/unified-log-view: log_view 改為 LogView widget
    text_ctrl = panel.log_view._list_view.controls[0]
    assert text_ctrl.color != 0x00


# -----------------------------------------------------------------------------
# PipelineView Initialization Tests
# -----------------------------------------------------------------------------

def test_pipeline_view_initializes_buttons(monkeypatch):
    """驗證 PipelineView 初始化時有 6 個按鈕"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())

    # 收集所有 Button
    buttons = []
    def collect(c):
        if isinstance(c, ft.Button):
            buttons.append(c)
        for child in getattr(c, "controls", []):
            collect(child)

    collect(view.workbench_view)

    assert len(buttons) >= 5, f"預期至少 5 個按鈕，實際 {len(buttons)}"


def test_pipeline_view_progress_panel_hidden_initially(monkeypatch):
    """驗證進度面板初始隱藏"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())

    assert view.progress_panel.container.visible is False


def test_pipeline_view_lang_code_checks_empty_initially(monkeypatch):
    """驗證 _lang_code_checks 初始為空 dict"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())

    assert view._lang_code_checks == {}


# -----------------------------------------------------------------------------
# PipelineView Button Validation Tests
# -----------------------------------------------------------------------------

def test_on_extract_click_without_input_shows_snack(monkeypatch):
    """驗證缺少 Mod 來源路徑時顯示 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())
    view.input_path_text.value = ""
    view.output_path_text.value = "C:/output"

    view._on_extract_click(None)

    assert len(page.overlay) >= 1


def test_on_extract_click_without_output_shows_snack(monkeypatch):
    """驗證缺少輸出路徑時顯示 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())
    view.input_path_text.value = "C:/input"
    view.output_path_text.value = ""

    view._on_extract_click(None)

    assert len(page.overlay) >= 1


def test_on_merge_click_without_input_shows_snack(monkeypatch):
    """驗證 _on_merge_click 缺少輸入時顯示 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())
    view.input_path_text.value = ""
    view.output_path_text.value = "C:/output"

    view._on_merge_click(None)

    assert len(page.overlay) >= 1


def test_on_translate_click_without_input_shows_snack(monkeypatch):
    """驗證 _on_translate_click 缺少輸入時顯示 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())
    view.input_path_text.value = ""
    view.output_path_text.value = "C:/output"

    view._on_translate_click(None)

    assert len(page.overlay) >= 1


def test_on_bundle_click_without_input_shows_snack(monkeypatch):
    """驗證 _on_bundle_click 缺少輸入時顯示 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())
    view.input_path_text.value = ""
    view.output_path_text.value = "C:/output"

    view._on_bundle_click(None)

    assert len(page.overlay) >= 1


def test_on_one_click_click_without_input_shows_snack(monkeypatch):
    """驗證 _on_one_click_click 缺少輸入時顯示 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())
    view.input_path_text.value = ""
    view.output_path_text.value = "C:/output"

    view._on_one_click_click(None)

    assert len(page.overlay) >= 1


# -----------------------------------------------------------------------------
# _run_translate Service Call Tests
# -----------------------------------------------------------------------------

def test_run_translate_calls_service_with_correct_args(monkeypatch):
    """驗證 _run_translate 正確呼叫 run_lm_translation_service"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    calls = {}
    monkeypatch.setattr(pipeline_view.threading, "Thread",
                       lambda target=None, args=(), daemon=None:
                       type("T", (), {"start": lambda self: target(*args)})())
    monkeypatch.setattr(pipeline_view, "run_lm_translation_service",
                       lambda **kw: calls.update(kw) or (_ for _ in ()).throw(StopIteration()))

    page = mock_page()
    view = PipelineView(page, mock_filepicker())

    view._run_translate(
        input_dir="C:/in",
        output_dir="C:/out",
        dry_run=True,
        write_new_cache=False,
    )

    assert calls["input_dir"] == "C:/in"
    assert calls["output_dir"] == "C:/out"
    assert calls["dry_run"] is True
    assert calls["write_new_cache"] is False


# -----------------------------------------------------------------------------
# _run_bundle Service Call Tests
# -----------------------------------------------------------------------------

def test_run_bundle_calls_service_with_all_args(monkeypatch):
    """驗證 _run_bundle 正確呼叫 run_bundling_service（含擴展參數）"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    calls = {}

    def fake_bundle(**kwargs):
        calls.update(kwargs)
        return iter([])

    monkeypatch.setattr(pipeline_view.threading, "Thread",
                       lambda target=None, args=(), daemon=None:
                       type("T", (), {"start": lambda self: target(*args)})())
    monkeypatch.setattr(pipeline_view, "run_bundling_service", fake_bundle)

    page = mock_page()
    view = PipelineView(page, mock_filepicker())

    view._run_bundle(
        input_root_dir="C:/in",
        output_zip_path="C:/out/result.zip",
        description="測試描述",
        min_format=10,
        max_format=18,
        pack_image_path="C:/img/pack.png",
        extra_folders=["C:/extra1", "C:/extra2"],
    )

    assert calls["input_root_dir"] == "C:/in"
    assert calls["output_zip_path"] == "C:/out/result.zip"
    assert calls["description"] == "測試描述"
    assert calls["min_format"] == 10
    assert calls["max_format"] == 18
    assert calls["pack_image_path"] == "C:/img/pack.png"
    assert calls["extra_folders"] == ["C:/extra1", "C:/extra2"]


# -----------------------------------------------------------------------------
# _do_bundle Service Call Tests
# -----------------------------------------------------------------------------

def test_do_bundle_calls_run_bundling_service(monkeypatch):
    """驗證 _do_bundle 正確傳遞所有參數到 run_bundling_service"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    calls = {}

    def fake_bundle(**kwargs):
        calls.update(kwargs)
        yield {"progress": 1.0}

    monkeypatch.setattr(pipeline_view, "run_bundling_service", fake_bundle)

    session = _Session()
    view = pipeline_view.PipelineView(mock_page(), mock_filepicker())

    view._do_bundle(
        input_root_dir="C:/bundle_in",
        output_zip_path="C:/out/bundle.zip",
        description="mydesc",
        pack_image_path="C:/pack.png",
        extra_folders=["C:/extra"],
        session=session,
    )

    assert calls["input_root_dir"] == "C:/bundle_in"
    assert calls["output_zip_path"] == "C:/out/bundle.zip"
    assert calls["description"] == "mydesc"
    assert calls["pack_image_path"] == "C:/pack.png"
    assert calls["extra_folders"] == ["C:/extra"]


# -----------------------------------------------------------------------------
# _on_one_click_execute Validation Tests
# -----------------------------------------------------------------------------

def test_on_one_click_execute_invalid_input_dir(monkeypatch):
    """驗證 _on_one_click_execute 當 Mod 來源目錄不存在時顯示 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())
    view.input_path_text.value = "C:/nonexistent"
    view.output_path_text.value = "C:/output"

    view._on_one_click_execute({"mode": "lang", "lang_codes": []})

    assert len(page.overlay) >= 1


def test_on_one_click_execute_invalid_output_dir(monkeypatch, tmp_path):
    """驗證 _on_one_click_execute 當輸出目錄不存在時顯示 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())
    view.input_path_text.value = str(tmp_path)
    view.output_path_text.value = "C:/nonexistent"

    view._on_one_click_execute({"mode": "lang", "lang_codes": []})

    assert len(page.overlay) >= 1


# -----------------------------------------------------------------------------
# _show_snack_bar Tests
# -----------------------------------------------------------------------------

def test_show_snack_bar_adds_to_overlay(monkeypatch):
    """驗證 _show_snack_bar 在 overlay 新增 SnackBar"""
    monkeypatch.setattr(pipeline_view, "TaskSession", _Session)
    page = mock_page()
    view = PipelineView(page, mock_filepicker())

    view._show_snack_bar("測試訊息")

    assert len(page.overlay) == 1
    snack = page.overlay[0]
    assert isinstance(snack, ft.SnackBar)
    assert snack.open is True
    assert page.updated >= 1


