from app.views.lookup_view import LookupView


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
    def update(self):
        self.updated += 1


def test_lookup_view_initializes_single_and_batch_actions():
    view = LookupView(_Page())

    assert view.single_button.text == '查詢'
    assert view.batch_button.text == '批次查詢'
    assert view.single_progress_ring.visible is False
    assert view.batch_progress_bar.visible is False


def test_single_lookup_without_input_sets_error_text():
    page = _Page()
    view = LookupView(page)

    view.single_lookup_clicked(None)

    assert '請輸入' in view.single_result_text.value


def test_batch_lookup_worker_updates_result_and_progress(monkeypatch):
    page = _Page()
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
