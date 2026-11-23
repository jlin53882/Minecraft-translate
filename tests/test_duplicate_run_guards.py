"""長任務執行中不可重複啟動（避免重複送出 API、同時寫入同一輸出/快取）。"""

import asyncio

from app.tasks import operation_registry
from app.views import bundler_view as bv
from tests.conftest import mock_filepicker, mock_page


class _FakeThreadFactory:
    def __init__(self, started):
        self.started = started

    def __call__(self, **kwargs):
        self.started.append(kwargs)
        return type("T", (), {"start": lambda _self: None})()


def test_bundler_start_is_ignored_while_running(monkeypatch):
    page = mock_page()
    view = bv.BundlerView(page, mock_filepicker())
    started = []
    monkeypatch.setattr(
        operation_registry.threading, "Thread", _FakeThreadFactory(started)
    )
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

    view._bundling_worker("C:/Root", "C:/out.zip", 34, None, "", "")
    assert view._bundling_running is False
    # 背景執行緒不直接改控制項；51 次更新被節流/背壓成少量 event loop 任務
    ran = 0
    while page._tasks:
        handler, args = page._tasks.pop(0)
        asyncio.run(handler(*args))
        ran += 1
    assert 1 <= ran < 10
    assert view.progress_bar.value == 1.0
    assert view.start_button.disabled is False
    assert view.progress_bar.visible is False
