from app.views.bundler_view import BundlerView


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
    def update(self):
        self.updated += 1


class _FilePicker:
    pass


def test_bundler_view_initializes_core_controls():
    view = BundlerView(_Page(), _FilePicker())

    assert view.start_button.text == '開始打包'
    assert view.progress_bar.visible is False


def test_start_bundling_without_required_paths_shows_error():
    page = _Page()
    view = BundlerView(page, _FilePicker())

    view.start_bundling_clicked(None)

    assert page.overlay
    assert '請同時提供' in page.overlay[-1].content.value


def test_bundling_worker_updates_progress_and_reenables_controls(monkeypatch):
    page = _Page()
    view = BundlerView(page, _FilePicker())
    view.start_button.disabled = True
    view.root_dir_textfield.disabled = True
    view.output_zip_textfield.disabled = True
    monkeypatch.setattr(view.log_view, 'scroll_to', lambda **kwargs: None)

    monkeypatch.setattr(
        'app.views.bundler_view.run_bundling_service',
        lambda root, out: iter([
            {'log': 'step1', 'progress': 0.5},
            {'log': 'done', 'progress': 1.0},
        ]),
    )

    view.bundling_worker('C:/Root', 'C:/out.zip')

    assert view.progress_bar.value == 1.0
    assert view.start_button.disabled is False
    assert view.root_dir_textfield.disabled is False
    assert view.output_zip_textfield.disabled is False
