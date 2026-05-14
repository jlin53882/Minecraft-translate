import tempfile
from pathlib import Path
import flet as ft
from app.views.icon_preview_view import IconPreviewView


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self._tasks = []

    def update(self, *args, **kwargs):
        self.updated += 1

    def run_task(self, coro, *args):
        self._tasks.append((coro, args))

    def _run_all_tasks(self):
        for coro, args in self._tasks:
            result = coro(*args)
            if result is not None:
                try:
                    result.send(None)
                except StopIteration:
                    pass


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


def test_icon_preview_view_all_controls_exist():
    """測試 IconPreviewView 所有 UI 控件存在"""
    view = IconPreviewView(_Page())

    assert view.header.value == '🧩 JAR 圖示預覽'
    assert isinstance(view.header, ft.Text)

    assert isinstance(view.mod_search_tf, ft.TextField)
    assert view.mod_search_tf.label == '搜尋模組'

    assert isinstance(view.mod_search_status, ft.Text)
    assert view.mod_search_status.value == ''

    assert isinstance(view.back_btn, ft.IconButton)
    assert view.back_btn.icon == ft.Icons.ARROW_BACK
    assert view.back_btn.visible is False

    assert isinstance(view.pick_source_btn, ft.Button)
    assert view.pick_source_btn.content == '選擇模組資料夾（例：mods 資料夾）'

    assert isinstance(view.pick_review_btn, ft.Button)
    assert view.pick_review_btn.content == '選擇資源包路徑'

    assert view.source_label.value == '模組資料夾：尚未選擇'
    assert view.review_label.value == '資源包路徑：尚未選擇'

    assert isinstance(view.load_btn, ft.Button)
    assert view.load_btn.content == '載入模組清單'
    assert view.load_btn.disabled is True

    assert isinstance(view.save_btn, ft.Button)
    assert view.save_btn.content == '💾 儲存翻譯'
    assert view.save_btn.visible is False

    assert isinstance(view.progress_bar, ft.ProgressBar)
    assert view.progress_bar.visible is False

    assert view.progress_text.value == '準備就緒'

    assert isinstance(view.prev_page_btn, ft.IconButton)
    assert view.prev_page_btn.icon == ft.Icons.CHEVRON_LEFT

    assert isinstance(view.next_page_btn, ft.IconButton)
    assert view.next_page_btn.icon == ft.Icons.CHEVRON_RIGHT

    assert isinstance(view.page_info, ft.Text)
    assert view.page_info.value == ''

    assert isinstance(view.page_size_selector, ft.Dropdown)
    assert view.page_size_selector.value == '50'

    assert hasattr(view, 'source_picker')
    assert hasattr(view, 'review_picker')
