import pytest
from app.views.bundler_view import BundlerView
from tests.conftest import mock_page, mock_filepicker


def test_bundler_view_initializes_core_controls():
    view = BundlerView(mock_page(), mock_filepicker())

    assert view.progress_bar.visible is False
    assert hasattr(view, "version_search")
    assert hasattr(view, "version_list")
    assert hasattr(view, "description_field")
    assert hasattr(view, "pack_image_field")
    assert hasattr(view, "root_dir_field")
    assert hasattr(view, "output_zip_field")
    assert hasattr(view, "extra_folders_view")


def test_bundler_view_loads_version_data():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert isinstance(view.version_data, dict)


def test_bundler_view_extra_folders_list_initialized():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert view.extra_folders == []
    assert len(view.extra_folders_view.controls) == 0


def test_bundler_view_version_list():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert hasattr(view, "version_search")
    assert hasattr(view, "version_list")
    assert hasattr(view, "version_data")


def test_bundler_view_version_list():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert hasattr(view, "version_list")
    assert hasattr(view, "version_expanded")
    assert view.version_expanded is False


def test_version_toggle_expand():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert view.version_expanded is False
    view._toggle_version_expand(None)
    assert view.version_expanded is True
    view._toggle_version_expand(None)
    assert view.version_expanded is False


def test_version_select():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    view._select_version("1.20.1")
    assert view.version_search.value == "1.20.1"
    assert view.version_expanded is False


def test_bundler_view_hint_texts():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert view.version_search.hint_text == "輸入版本關鍵字..."
    assert view.description_field.hint_text == "直接輸入文字，或使用 § 顏色代碼"
    assert view.pack_image_field.hint_text == "選擇 pack.png 圖片（可選）"
    assert view.root_dir_field.hint_text == "包含所有翻譯產出的最上層資料夾"
    assert view.output_zip_field.hint_text == "輸出位置與檔名"


def test_start_bundling_without_required_paths_shows_error():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    view.start_bundling_clicked(None)

    assert page.overlay
    assert "請填寫" in page.overlay[-1].content.value


def test_start_bundling_without_output_zip_shows_error():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.root_dir_field.value = "C:/test"

    view.start_bundling_clicked(None)

    assert page.overlay
    assert "請填寫" in page.overlay[-1].content.value


def test_start_bundling_without_root_dir_shows_error():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.output_zip_field.value = "C:/output.zip"

    view.start_bundling_clicked(None)

    assert page.overlay
    assert "請填寫" in page.overlay[-1].content.value


def test_bundling_worker_updates_progress_and_reenables_controls(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    monkeypatch.setattr(view.log_view, "scroll_to", lambda **kwargs: None)

    def mock_generator(**kwargs):
        return iter([
            {"log": "step1", "progress": 0.5},
            {"log": "done", "progress": 1.0},
        ])

    monkeypatch.setattr(
        "translation_tool.core.output_bundler.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", "", "", "")

    assert view.progress_bar.value == 1.0


def test_bundling_worker_with_version_info(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.version_data = {"1.20.1": {"min_format": 15, "max_format": 15}}
    monkeypatch.setattr(view.log_view, "scroll_to", lambda **kwargs: None)

    captured_kwargs = {}

    def mock_generator(**kwargs):
        captured_kwargs.update(kwargs)
        return iter([
            {"log": "done", "progress": 1.0},
        ])

    monkeypatch.setattr(
        "translation_tool.core.output_bundler.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", "1.20.1", "Test Description", "")

    assert captured_kwargs["min_format"] == 15
    assert captured_kwargs["max_format"] == 15
    assert captured_kwargs["description"] == "Test Description"


def test_bundling_worker_passes_extra_folders(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.extra_folders = ["C:/extra1", "C:/extra2"]
    monkeypatch.setattr(view.log_view, "scroll_to", lambda **kwargs: None)

    captured_kwargs = {}

    def mock_generator(**kwargs):
        captured_kwargs.update(kwargs)
        return iter([{"log": "done", "progress": 1.0}])

    monkeypatch.setattr(
        "translation_tool.core.output_bundler.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", "", "", "")

    assert captured_kwargs["extra_folders"] == ["C:/extra1", "C:/extra2"]


def test_bundling_worker_passes_pack_image(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    monkeypatch.setattr(view.log_view, "scroll_to", lambda **kwargs: None)

    captured_kwargs = {}

    def mock_generator(**kwargs):
        captured_kwargs.update(kwargs)
        return iter([{"log": "done", "progress": 1.0}])

    monkeypatch.setattr(
        "translation_tool.core.output_bundler.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", "", "", "C:/pack.png")

    assert captured_kwargs["pack_image_path"] == "C:/pack.png"


def test_bundling_worker_empty_pack_image(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.pack_image_field.value = ""
    monkeypatch.setattr(view.log_view, "scroll_to", lambda **kwargs: None)

    captured_kwargs = {}

    def mock_generator(**kwargs):
        captured_kwargs.update(kwargs)
        return iter([{"log": "done", "progress": 1.0}])

    monkeypatch.setattr(
        "translation_tool.core.output_bundler.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", "", "", "")

    assert captured_kwargs["pack_image_path"] is None


def test_remove_extra_folder(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.extra_folders = ["C:/folder1", "C:/folder2"]
    monkeypatch.setattr(view, "_refresh_extra_folders", lambda: None)
    monkeypatch.setattr(view, "update", lambda: None)
    monkeypatch.setattr(view.log_view, "scroll_to", lambda **kwargs: None)

    view._remove_extra_folder("C:/folder1")

    assert "C:/folder1" not in view.extra_folders
    assert "C:/folder2" in view.extra_folders


def test_extra_folders_list_initially_empty():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert view.extra_folders == []
    assert len(view.extra_folders_view.controls) == 0


def test_show_snack_bar_adds_to_overlay(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    view._show_snack_bar("Test message")

    assert len(page.overlay) == 1
    assert page.overlay[-1].content.value == "Test message"


def test_bundling_worker_with_error(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    monkeypatch.setattr(view.log_view, "scroll_to", lambda **kwargs: None)

    def mock_generator(**kwargs):
        raise RuntimeError("Test error")

    monkeypatch.setattr(
        "translation_tool.core.output_bundler.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", "", "", "")

    assert view.progress_bar.color == "red"
    assert not view.progress_bar.visible


def test_bundler_view_show_snack_bar_adds_to_overlay():
    """測試 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    view._show_snack_bar('Test error', '#FF0000')

    assert len(page.overlay) == 1
    assert page.overlay[0].open is True


def test_bundler_view_append_log_adds_control():
    """測試 _append_log 正確將日誌行加入 log_view"""
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    view._append_log('test log entry')

    assert len(view.log_view.controls) >= 1

def test_bundler_view_version_list_exists():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert view.version_list is not None


def test_bundler_view_version_search_exists():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert view.version_search is not None


def test_bundler_view_version_data_exists():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert view.version_data is not None


def test_bundler_view_pick_pack_image():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert hasattr(view, '_pick_pack_image')


def test_bundler_view_extra_folders_list():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert view.extra_folders is not None
    assert isinstance(view.extra_folders, list)
