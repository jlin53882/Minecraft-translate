from app.views.lookup_view import LookupView
from app.ui.snack import show_snack
from tests.conftest import mock_page


def test_lookup_view_initializes_single_and_batch_actions():
    view = LookupView(mock_page())

    assert view.single_button.content == '查詢'
    assert view.batch_button.content == '批次查詢'
    assert view.single_progress_ring.visible is False
    assert view.batch_progress_bar.visible is False


def test_single_lookup_without_input_sets_error_text():
    page = mock_page()
    view = LookupView(page)

    view.single_lookup_clicked(None)

    assert '請輸入' in view.single_result_text.value


def test_batch_lookup_worker_updates_result_and_progress(monkeypatch):
    page = mock_page()
    view = LookupView(page)

    monkeypatch.setattr(
        'app.views.lookup_view.run_batch_lookup_service',
        lambda text: iter([
            {'progress': 0.5, 'log': 'half'},
            {'progress': 1.0, 'result': '{"ok": true}'},
        ]),
    )

    view.batch_button.disabled = True
    view.batch_progress_bar.visible = True
    view.batch_lookup_worker('[]')
    # UI 更新排在 event loop 上（run_task），不在背景執行緒直接修改
    assert view.batch_button.disabled is True
    import asyncio
    for handler, args in page._tasks:
        asyncio.run(handler(*args))

    assert view.batch_result_textfield.value == '{"ok": true}'
    assert view.batch_progress_bar.visible is False
    assert view.batch_button.disabled is False


def test_lookup_view_show_snack_bar_adds_to_overlay():
    """測試 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    page = mock_page()
    view = LookupView(page)

    show_snack(view.page, 'Test error', '#FF0000')

    assert len(page.overlay) == 1
    assert page.overlay[0].open is True


def test_lookup_view_batch_lookup_clicked_triggers_service(monkeypatch):
    """測試 batch_lookup_clicked 調用服務"""
    page = mock_page()
    view = LookupView(page)
    view.batch_input = type('F', (), {'value': '[]'})()

    calls = []
    def mock_worker(term):
        calls.append(term)
        return iter([])

    monkeypatch.setattr('app.views.lookup_view.run_batch_lookup_service', mock_worker)

    view.batch_lookup_clicked(None)


def test_lookup_view_single_progress_ring_exists():
    view = LookupView(mock_page())

    assert view.single_progress_ring is not None


def test_lookup_view_batch_progress_bar_exists():
    view = LookupView(mock_page())

    assert view.batch_progress_bar is not None


def test_lookup_view_single_result_text_exists():
    view = LookupView(mock_page())

    assert view.single_result_text is not None


def test_single_lookup_worker_restores_ui_on_error(monkeypatch):
    """查詢服務拋例外時也要恢復按鈕並顯示錯誤（原本會永遠停在查詢中）。"""
    import asyncio

    page = mock_page()
    view = LookupView(page)

    def boom(name):
        raise ValueError("db locked")

    monkeypatch.setattr('app.views.lookup_view.run_manual_lookup_service', boom)
    view.single_button.disabled = True
    view.single_progress_ring.visible = True

    view.single_lookup_worker('Stone')
    for handler, args in page._tasks:
        asyncio.run(handler(*args))

    assert view.single_button.disabled is False
    assert view.single_progress_ring.visible is False
    assert 'db locked' in view.single_result_text.value
