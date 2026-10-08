import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import flet as ft

from app.ui.snack import show_snack
from app.views.icon_preview_view import IconPreviewView
from tests.conftest import mock_page


def test_icon_preview_view_initializes_core_sections():
    view = IconPreviewView(mock_page())

    assert view.page_size == 50
    assert view.current_page == 0
    assert view.list_view is not None
    assert view.page_bar is not None


def _drain(page):
    """執行 mock page 排入的 async 工作（圖示準備／存檔都在 to_thread，之後回 event loop 套用）。"""
    import asyncio

    while page._tasks:
        handler, args = page._tasks.pop(0)
        asyncio.run(handler(*args))


def test_render_current_page_uses_current_page_size():
    page = mock_page()
    view = IconPreviewView(page)
    view.current_modid = "demo"
    view.mods = {
        "demo": [
            type("E", (), {"key": f"k{i}", "en": f"en{i}", "zh_tw": ""})()
            for i in range(120)
        ]
    }
    view.source_root = Path(".")
    view._zh_data = {}

    view._render_current_page()
    # 頁碼立即更新；列在背景準備圖示後才套用（event loop 上不做圖示 I/O）
    assert view.total_pages == 3
    assert view.list_view.controls == []
    _drain(page)

    assert len(view.list_view.controls) == 50


def test_save_current_zh_writes_modified_json():
    page = mock_page()
    view = IconPreviewView(page)

    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "icons.json"
        worker_targets = []
        page.run_thread = worker_targets.append
        view._current_zh_file = json_path
        view._zh_data = {"k": "青蘋果"}

        view._save_current_zh(None)
        assert not json_path.exists()  # 寫檔在背景執行緒，點擊當下不寫
        worker = threading.Thread(target=worker_targets.pop())
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        _drain(page)

        assert "青蘋果" in json_path.read_text(encoding="utf-8")
        assert page.overlay


def test_registry_owned_icon_preview_save_error_marks_operation_failed(
    tmp_path, monkeypatch
):
    from app.tasks.operation_registry import OperationRegistry

    page = mock_page()
    registry = OperationRegistry()
    page.operation_registry = registry
    view = IconPreviewView(page)
    view._current_zh_file = tmp_path / "zh_tw.json"
    view._zh_data = {"key": "translation"}
    failure = PermissionError("destination is read-only")
    monkeypatch.setattr(view, "_write_zh_file", lambda *_args: failure)
    finished = []
    registry.subscribe(
        lambda event, handle: finished.append(handle) if event == "finish" else None
    )

    view._save_current_zh(None)

    assert registry.wait_for_idle(timeout=2)
    assert finished[0].terminal_reason == "failed"
    assert finished[0].error is failure


def test_icon_preview_view_all_controls_exist():
    """測試 IconPreviewView 所有 UI 控件存在"""
    view = IconPreviewView(mock_page())

    assert view.header.value == "🧩 JAR 圖示預覽"
    assert isinstance(view.header, ft.Text)

    assert isinstance(view.mod_search_tf, ft.TextField)
    assert view.mod_search_tf.label == "搜尋模組"

    assert isinstance(view.mod_search_status, ft.Text)
    assert view.mod_search_status.value == ""

    assert isinstance(view.back_btn, ft.IconButton)
    assert view.back_btn.icon == ft.Icons.ARROW_BACK
    assert view.back_btn.visible is False

    assert isinstance(view.pick_source_btn, ft.Button)
    assert view.pick_source_btn.content == "選擇模組資料夾（例：mods 資料夾）"

    assert isinstance(view.pick_review_btn, ft.Button)
    assert view.pick_review_btn.content == "選擇資源包路徑"

    assert isinstance(view.source_path_input, ft.TextField)
    assert view.source_path_input.label == "模組資料夾路徑"
    assert view.source_path_input.value == ""
    assert view.source_path_input.path_input is True
    assert getattr(view.source_path_input.on_change, "_sync_wrapped", False)
    assert view.source_path_input.on_change.__wrapped__.__self__ is view
    assert (
        view.source_path_input.on_change.__wrapped__.__name__
        == "_on_source_path_changed"
    )
    assert isinstance(view.review_path_input, ft.TextField)
    assert view.review_path_input.label == "資源包／lang_output 路徑"
    assert view.review_path_input.value == ""
    assert view.review_path_input.path_input is True
    assert getattr(view.review_path_input.on_change, "_sync_wrapped", False)
    assert view.review_path_input.on_change.__wrapped__.__self__ is view
    assert (
        view.review_path_input.on_change.__wrapped__.__name__
        == "_on_review_path_changed"
    )

    assert isinstance(view.load_btn, ft.Button)
    assert view.load_btn.content == "載入模組清單"
    assert view.load_btn.disabled is True

    assert isinstance(view.save_btn, ft.Button)
    assert view.save_btn.content == "💾 儲存翻譯"
    assert view.save_btn.visible is False

    assert isinstance(view.progress_bar, ft.ProgressBar)
    assert view.progress_bar.visible is False

    assert view.progress_text.value == "準備就緒"

    assert isinstance(view.prev_page_btn, ft.IconButton)
    assert view.prev_page_btn.icon == ft.Icons.CHEVRON_LEFT

    assert isinstance(view.next_page_btn, ft.IconButton)
    assert view.next_page_btn.icon == ft.Icons.CHEVRON_RIGHT

    assert isinstance(view.page_info, ft.Text)
    assert view.page_info.value == ""

    assert isinstance(view.page_size_selector, ft.Dropdown)
    assert view.page_size_selector.value == "50"

    assert hasattr(view, "source_picker")
    assert hasattr(view, "review_picker")


def test_icon_preview_view_show_snack_adds_to_overlay():
    """測試 _show_snack 正確將 SnackBar 加入 page.overlay"""
    page = mock_page()
    view = IconPreviewView(page)

    show_snack(view.page, "Test error", "#FF0000")

    assert len(page.overlay) >= 1


def test_icon_preview_view_page_controls_exist():
    """測試分頁控制項存在"""
    view = IconPreviewView(mock_page())

    assert view.prev_page_btn is not None
    assert view.next_page_btn is not None
    assert view.page_info is not None


def test_icon_preview_view_page_size_selector():
    """測試 page_size_selector 存在"""
    view = IconPreviewView(mock_page())

    assert view.page_size_selector is not None
    assert view.page_size_selector.value == "50"


def test_icon_preview_view_update_page_bar_for_mods():
    view = IconPreviewView(mock_page())
    view.current_page = 1
    view.total_pages = 5
    view.mods = {"a": [1, 2, 3], "b": [4, 5, 6]}

    view._update_page_bar_for_mods()

    assert view.page_info.value is not None


def test_icon_preview_view_on_pick_source(monkeypatch):
    view = IconPreviewView(mock_page())

    class E:
        path = "/test/source"

    view._on_pick_source(E())

    assert "test" in str(view.source_root).lower()
    assert "source" in str(view.source_root).lower()


def test_icon_preview_view_on_pick_review(monkeypatch):
    view = IconPreviewView(mock_page())

    class E:
        path = "/test/review"

    view._on_pick_review(E())

    assert hasattr(view, "review_root")


def test_icon_preview_view_load_entries_method():
    view = IconPreviewView(mock_page())
    view.source_root = "test"

    class E:
        pass

    try:
        view._load_entries(E())
    except Exception:  # noqa: BLE001, S110 - 只驗證不會讓測試程序崩潰
        pass


def test_icon_preview_view_cancel_mod_search_debounce():
    view = IconPreviewView(mock_page())
    view._mod_search_timer = type("T", (), {"cancel": lambda self: None})()

    view._cancel_mod_search_debounce()

    assert view._mod_search_timer is not None


def test_icon_preview_view_progress_bar_exists():
    view = IconPreviewView(mock_page())

    assert view.progress_bar is not None
    assert view.progress_text is not None


def test_icon_preview_view_load_btn_disabled_initially():
    view = IconPreviewView(mock_page())

    assert view.load_btn.disabled is True


def test_icon_preview_view_source_path_input_initially_empty():
    view = IconPreviewView(mock_page())

    assert view.source_path_input.value == ""


def test_icon_preview_view_review_path_input_initially_empty():
    view = IconPreviewView(mock_page())

    assert view.review_path_input.value == ""


def test_manual_path_input_sets_roots_and_enables_load(tmp_path):
    page = mock_page()
    view = IconPreviewView(page)
    mods = tmp_path / "mods"
    review = tmp_path / "lang_output"
    mods.mkdir()
    review.mkdir()

    view.source_path_input.value = f'"{mods}"'
    view.review_path_input.value = str(review)
    view._on_source_path_input(type("E", (), {"control": view.source_path_input})())
    view._on_review_path_input(type("E", (), {"control": view.review_path_input})())

    assert view.source_root == mods
    assert view.review_root == review
    assert view.load_btn.disabled is False
    assert view._validate_input_paths() is True
    assert view.source_path_input.value == str(mods)


def test_manual_path_input_rejects_missing_directory(tmp_path):
    view = IconPreviewView(mock_page())
    view.source_path_input.value = str(tmp_path / "missing-mods")
    view.review_path_input.value = str(tmp_path)

    assert view._validate_input_paths() is False


def test_manual_path_change_does_not_rerender_until_blur(tmp_path):
    view = IconPreviewView(mock_page())
    mods = tmp_path / "mods"
    mods.mkdir()
    view.review_root = tmp_path
    event = type(
        "E",
        (),
        {"control": view.source_path_input, "data": str(mods)},
    )()

    with patch.object(view, "update") as update:
        view.source_path_input.on_change(event)
        assert view.source_root == mods
        assert view.load_btn.disabled is True
        update.assert_not_called()

        # Some Web blur events carry no text; retain the latest change payload.
        view.source_path_input.value = ""
        blur_event = type("E", (), {"control": view.source_path_input, "data": None})()
        view.source_path_input.on_blur(blur_event)
        assert view.source_path_input.value == str(mods)
        update.assert_called_once()

    assert view.load_btn.disabled is False


def test_icon_preview_view_mod_search_tf_exists():
    view = IconPreviewView(mock_page())

    assert view.mod_search_tf is not None
    assert view.mod_search_tf.label == "搜尋模組"


def test_icon_preview_view_save_btn_initially_hidden():
    view = IconPreviewView(mock_page())

    assert view.save_btn.visible is False


def test_icon_preview_view_mod_search_tf_on_change():
    view = IconPreviewView(mock_page())
    assert view.mod_search_tf.on_change is not None


def test_icon_preview_view_page_size_selector_on_select():
    view = IconPreviewView(mock_page())
    assert view.page_size_selector.on_select is not None


def test_icon_preview_view_mod_search_status_exists():
    view = IconPreviewView(mock_page())
    assert view.mod_search_status is not None


def test_icon_preview_view_back_btn_on_click():
    view = IconPreviewView(mock_page())
    assert view.back_btn.on_click is not None


def test_icon_preview_view_load_btn_on_click():
    view = IconPreviewView(mock_page())
    assert view.load_btn.on_click is not None


def test_icon_preview_view_source_picker_exists():
    view = IconPreviewView(mock_page())
    assert view.source_picker is not None


def test_icon_preview_view_review_picker_exists():
    view = IconPreviewView(mock_page())
    assert view.review_picker is not None


def test_icon_preview_view_current_page_init():
    view = IconPreviewView(mock_page())
    assert view.current_page == 0


def test_icon_preview_view_total_pages_init():
    view = IconPreviewView(mock_page())
    assert view.total_pages == 0


def test_icon_preview_view_mods_init():
    view = IconPreviewView(mock_page())
    assert view.mods is not None


def test_icon_preview_view_detect_source_mode():
    view = IconPreviewView(mock_page())
    assert hasattr(view, "_detect_source_mode")
    assert callable(view._detect_source_mode)


def test_icon_preview_view_load_entries_from_jar_directory():
    view = IconPreviewView(mock_page())
    assert hasattr(view, "_load_entries_from_jar_directory")
    assert callable(view._load_entries_from_jar_directory)


def test_icon_preview_view_on_value_changed():
    view = IconPreviewView(mock_page())
    assert hasattr(view, "_on_value_changed")
    assert callable(view._on_value_changed)


def test_icon_preview_view_cancel_detail_search_debounce():
    view = IconPreviewView(mock_page())
    assert hasattr(view, "_cancel_detail_search_debounce")
    assert callable(view._cancel_detail_search_debounce)
