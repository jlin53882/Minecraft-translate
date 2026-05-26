from app.views.lookup_view import LookupView
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

    assert view.batch_result_textfield.value == '{"ok": true}'
    assert view.batch_progress_bar.visible is False
    assert view.batch_button.disabled is False


def test_lookup_view_show_snack_bar_adds_to_overlay():
    page = mock_page()
    view = LookupView(page)

    view._show_snack_bar('Test error', '#FF0000')

    assert len(page.overlay) == 1
    assert page.overlay[0].open is True


def test_lookup_view_batch_lookup_clicked_triggers_service(monkeypatch):
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


def test_single_lookup_worker_calls_run_task(monkeypatch):
    """驗證 single_lookup_worker 正確呼叫 page.run_task 更新 UI。"""
    page = mock_page()
    view = LookupView(page)

    view.single_input.value = "test_name"
    view.single_button.disabled = True
    view.single_progress_ring.visible = True

    monkeypatch.setattr("app.views.lookup_view.run_manual_lookup_service", lambda n: "result_value")

    view.single_lookup_worker("test_name")

    assert len(page._tasks) == 1


def test_batch_lookup_worker_error_branch_sets_error_text(monkeypatch):
    """驗證 batch_lookup_worker 在收到 error update 時設定錯誤文字。"""
    page = mock_page()
    view = LookupView(page)

    monkeypatch.setattr(
        "app.views.lookup_view.run_batch_lookup_service",
        lambda t: iter([{"error": True, "log": "Error occurred"}]),
    )

    view.batch_lookup_worker("[]")

    assert "Error occurred" in view.batch_result_textfield.value


def test_batch_lookup_worker_progress_branch_updates_progress(monkeypatch):
    """驗證 batch_lookup_worker 在收到 progress update 時更新進度條。"""
    page = mock_page()
    view = LookupView(page)

    monkeypatch.setattr(
        "app.views.lookup_view.run_batch_lookup_service",
        lambda t: iter([{"progress": 0.5}]),
    )

    view.batch_lookup_worker("[]")

    assert view.batch_progress_bar.value == 0.5