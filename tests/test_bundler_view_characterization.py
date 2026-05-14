from app.views.bundler_view import BundlerView


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self.run_task_called = []

    def update(self):
        self.updated += 1

    def run_task(self, coroutine):
        self.run_task_called.append(coroutine)


class _FilePicker:
    def __init__(self):
        self._callback = None

    def on_upload(self, callback):
        self._callback = callback


def test_bundler_view_initializes_core_controls():
    view = BundlerView(_Page(), _FilePicker())

    assert view.progress_bar.visible is False
    assert hasattr(view, "version_search")
    assert hasattr(view, "version_list")
    assert hasattr(view, "description_field")
    assert hasattr(view, "pack_image_field")
    assert hasattr(view, "root_dir_field")
    assert hasattr(view, "output_zip_field")
    assert hasattr(view, "extra_folders_view")


def test_bundler_view_loads_version_data():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    assert isinstance(view.version_data, dict)


def test_bundler_view_extra_folders_list_initialized():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    assert view.extra_folders == []
    assert len(view.extra_folders_view.controls) == 0


def test_bundler_view_version_list():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    assert hasattr(view, "version_search")
    assert hasattr(view, "version_list")
    assert hasattr(view, "version_data")


def test_bundler_view_version_list():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    assert hasattr(view, "version_list")
    assert hasattr(view, "version_expanded")
    assert view.version_expanded is False


def test_version_toggle_expand():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    assert view.version_expanded is False
    view._toggle_version_expand(None)
    assert view.version_expanded is True
    view._toggle_version_expand(None)
    assert view.version_expanded is False


def test_version_select():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    view._select_version("1.20.1")
    assert view.version_search.value == "1.20.1"
    assert view.version_expanded is False


def test_bundler_view_hint_texts():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    assert view.version_search.hint_text == "輸入版本關鍵字..."
    assert view.description_field.hint_text == "支援 JSON 格式或 §顏色代碼"
    assert view.pack_image_field.hint_text == "選擇 pack.png 圖片（可選）"
    assert view.root_dir_field.hint_text == "包含所有翻譯產出的最上層資料夾"
    assert view.output_zip_field.hint_text == "輸出位置與檔名"


def test_start_bundling_without_required_paths_shows_error():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    view.start_bundling_clicked(None)

    assert page.overlay
    assert "請填寫" in page.overlay[-1].content.value


def test_start_bundling_without_output_zip_shows_error():
    page = _Page()
    view = BundlerView(page, _FilePicker())
    view.root_dir_field.value = "C:/test"

    view.start_bundling_clicked(None)

    assert page.overlay
    assert "請填寫" in page.overlay[-1].content.value


def test_start_bundling_without_root_dir_shows_error():
    page = _Page()
    view = BundlerView(page, _FilePicker())
    view.output_zip_field.value = "C:/output.zip"

    view.start_bundling_clicked(None)

    assert page.overlay
    assert "請填寫" in page.overlay[-1].content.value


def test_bundling_worker_updates_progress_and_reenables_controls(monkeypatch):
    page = _Page()
    view = BundlerView(page, _FilePicker())
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
    page = _Page()
    view = BundlerView(page, _FilePicker())
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
    page = _Page()
    view = BundlerView(page, _FilePicker())
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
    page = _Page()
    view = BundlerView(page, _FilePicker())
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
    page = _Page()
    view = BundlerView(page, _FilePicker())
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
    page = _Page()
    view = BundlerView(page, _FilePicker())
    view.extra_folders = ["C:/folder1", "C:/folder2"]
    monkeypatch.setattr(view, "_refresh_extra_folders", lambda: None)
    monkeypatch.setattr(view, "update", lambda: None)
    monkeypatch.setattr(view.log_view, "scroll_to", lambda **kwargs: None)

    view._remove_extra_folder("C:/folder1")

    assert "C:/folder1" not in view.extra_folders
    assert "C:/folder2" in view.extra_folders


def test_extra_folders_list_initially_empty():
    page = _Page()
    view = BundlerView(page, _FilePicker())
    assert view.extra_folders == []
    assert len(view.extra_folders_view.controls) == 0


def test_show_snack_bar_adds_to_overlay(monkeypatch):
    page = _Page()
    view = BundlerView(page, _FilePicker())

    view._show_snack_bar("Test message")

    assert len(page.overlay) == 1
    assert page.overlay[-1].content.value == "Test message"


def test_bundling_worker_with_error(monkeypatch):
    page = _Page()
    view = BundlerView(page, _FilePicker())
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