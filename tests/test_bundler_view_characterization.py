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
    assert hasattr(view, "version_dropdown")
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


def test_start_bundling_without_required_paths_shows_error():
    page = _Page()
    view = BundlerView(page, _FilePicker())

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