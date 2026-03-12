from app.views.qc_view import QCView


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self.dialog = None
    def update(self):
        self.updated += 1


class _FilePicker:
    pass


def test_qc_view_initializes_three_cards_and_shared_log_area():
    view = QCView(_Page(), _FilePicker())

    assert view.untranslated_start_button.text == '開始檢查未翻譯'
    assert view.compare_start_button.text == '啟動：JSON 資料夾差異比對'
    assert view.compare_tsv_start_button.text == '啟動：TSV 單檔案差異比對'
    assert view.progress_bar.visible is False
    assert view.log_view is not None


def test_set_controls_disabled_toggles_all_qc_inputs():
    view = QCView(_Page(), _FilePicker())

    view.set_controls_disabled(True)
    assert view.en_dir_textfield.disabled is True
    assert view.compare_start_button.disabled is True
    assert view.tsv_out_file_textfield.disabled is True

    view.set_controls_disabled(False)
    assert view.en_dir_textfield.disabled is False
    assert view.compare_start_button.disabled is False
    assert view.tsv_out_file_textfield.disabled is False


def test_start_task_untranslated_requires_all_paths():
    page = _Page()
    view = QCView(page, _FilePicker())

    view.start_task('untranslated')

    assert page.overlay
    assert '錯誤：請填寫所有' in page.overlay[-1].content.value


def test_task_worker_consumes_generator_updates(monkeypatch):
    page = _Page()
    view = QCView(page, _FilePicker())
    monkeypatch.setattr(view.log_view, 'scroll_to', lambda **kwargs: None)

    def fake_service(*args):
        yield {'log': 'line1\nline2', 'progress': 0.5}
        yield {'log': 'done', 'progress': 1.0}

    view.task_worker(fake_service, tuple())

    assert len(view.log_view.controls) >= 3
    assert view.progress_bar.value == 1.0
