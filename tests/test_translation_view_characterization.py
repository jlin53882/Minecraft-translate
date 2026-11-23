"""任務翻譯頁：三個分頁都是流水線工作台；舊版 Markdown 續跑資料的處理。"""

from types import SimpleNamespace

from app.views import translation_view as tv
from app.views.kubejs_ftb_pipeline_view import KubejsFtbPipelineView
from app.views.md_pipeline_view import MarkdownPipelineView
from tests.conftest import mock_filepicker, mock_page


def test_translation_view_has_three_embedded_pipeline_tabs():
    view = tv.TranslationView(mock_page(), mock_filepicker())

    assert isinstance(view.ftb_view, KubejsFtbPipelineView)
    assert isinstance(view.kjs_view, KubejsFtbPipelineView)
    assert isinstance(view.md_view, MarkdownPipelineView)
    assert view.tabs.length == 3


def test_page_property_returns_the_page():
    page = mock_page()

    assert tv.TranslationView(page, mock_filepicker()).page is page


def test_resume_of_old_markdown_task_points_to_the_pipeline_tab():
    page = mock_page()
    view = tv.TranslationView(page, mock_filepicker())
    task = SimpleNamespace(kind="md", input_dir="C:/pack", output_dir=None, options={})

    view.resume_interrupted(task)

    assert view.tabs.selected_index == 2
    assert "Markdown" in page.overlay[-1].content.value


def test_resume_of_unknown_kind_does_not_switch_tab():
    view = tv.TranslationView(mock_page(), mock_filepicker())

    view.resume_interrupted(SimpleNamespace(kind="other"))

    assert view.tabs.selected_index == 0
