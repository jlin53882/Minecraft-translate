import tempfile
from pathlib import Path

from app.views.icon_preview_view import IconPreviewView


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
    def update(self, *args, **kwargs):
        self.updated += 1


def test_icon_preview_view_initializes_core_sections():
    view = IconPreviewView(_Page())

    assert view.page_size == 50
    assert view.current_page == 0
    assert view.list_view is not None
    assert view.page_bar is not None


def test_render_current_page_uses_current_page_size():
    view = IconPreviewView(_Page())
    view.current_modid = 'demo'
    view.mods = {
        'demo': [
            type('E', (), {'key': f'k{i}', 'en': f'en{i}', 'zh_tw': ''})()
            for i in range(120)
        ]
    }
    view.source_root = Path('.')
    view._zh_data = {}

    view._render_current_page()

    assert view.total_pages == 3
    assert len(view.list_view.controls) == 50


def test_save_current_zh_writes_modified_json():
    page = _Page()
    view = IconPreviewView(page)

    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / 'icons.json'
        view._current_zh_file = json_path
        view._zh_data = {'k': '青蘋果'}

        view._save_current_zh(None)

        assert '青蘋果' in json_path.read_text(encoding='utf-8')
        assert page.overlay
