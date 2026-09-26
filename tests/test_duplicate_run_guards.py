"""長任務執行中不可重複啟動（避免重複送出 API、同時寫入同一輸出/快取）。"""

from app.views import bundler_view as bv
from app.views import translation_view as tv
from app.views.translation import translation_actions as ta
from tests.conftest import mock_filepicker, mock_page


class _FakeThreadFactory:
    def __init__(self, started):
        self.started = started

    def __call__(self, **kwargs):
        self.started.append(kwargs)
        return type("T", (), {"start": lambda _self: None})()


def test_translation_run_is_ignored_while_task_running(monkeypatch):
    page = mock_page()
    view = tv.TranslationView(page, mock_filepicker())
    started = []
    monkeypatch.setattr(ta.threading, "Thread", _FakeThreadFactory(started))
    view.ftb_in_dir.value = "C:/pack"
    view.kjs_in_dir.value = "C:/pack"
    view.md_in_dir.value = "C:/pack"
    view._ui_timer_running = True

    ta.run_ftb(view, dry_run=False)
    ta.run_kjs(view, dry_run=False)
    ta.run_md(view, dry_run=False)

    assert started == []
    assert "執行中" in page.overlay[-1].content.value


def test_translation_ui_timer_uses_event_loop_task():
    page = mock_page()
    view = tv.TranslationView(page, mock_filepicker())

    ta.start_ui_timer(view)
    ta.start_ui_timer(view)

    assert view._ui_timer_running is True
    assert len(page._tasks) == 1  # 只排入一個 async poller，且不另開執行緒


def test_bundler_start_is_ignored_while_running(monkeypatch):
    page = mock_page()
    view = bv.BundlerView(page, mock_filepicker())
    started = []
    monkeypatch.setattr(bv.threading, "Thread", _FakeThreadFactory(started))
    view.root_dir_field.value = "C:/root"

    view.start_bundling_clicked(None)
    assert len(started) == 1
    assert view.start_button.disabled is True

    view.start_bundling_clicked(None)
    assert len(started) == 1


def test_bundler_worker_restores_button_and_batches_ui(monkeypatch):
    page = mock_page()
    view = bv.BundlerView(page, mock_filepicker())
    monkeypatch.setattr(
        bv,
        "bundle_outputs_generator",
        lambda **kw: iter(
            [{"log": f"line {i}", "progress": i / 50} for i in range(51)]
        ),
    )
    view._bundling_running = True

    view._bundling_worker("C:/Root", "C:/out.zip", "", "", "")
    assert view._bundling_running is False
    assert view.progress_bar.value == 1.0
    # 51 次更新被節流成少量 UI 更新（不是每行一次 page.update）
    assert 1 <= len(page._tasks) < 10
