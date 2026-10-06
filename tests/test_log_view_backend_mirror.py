"""LogView.add 的 UI→後台鏡像。"""

from __future__ import annotations

import logging

import pytest

from app.views._log import LogView
from translation_tool.utils import ui_mirror


@pytest.fixture(autouse=True)
def _clean_tracker():
    ui_mirror.BACKEND_SEEN_TRACKER.clear()
    yield
    ui_mirror.BACKEND_SEEN_TRACKER.clear()


def _view():
    return LogView(page=None)


def _mirrored(caplog):
    return [r for r in caplog.records if getattr(r, "ui_mirrored", False)]


def test_add_mirrors_even_when_level_is_filtered_from_ui(caplog):
    view = _view()
    view.set_show_levels(["error"])
    with caplog.at_level(logging.DEBUG):
        view.add("除錯訊息", level="debug", update=False)
    assert [r.getMessage() for r in _mirrored(caplog)] == ["除錯訊息"]
    assert len(view._list_view.controls) == 0


def test_mirror_text_overrides_decorated_ui_text(caplog):
    view = _view()
    with caplog.at_level(logging.INFO):
        view.add(">> ▶ 開始：提取", mirror_text="▶ 開始：提取", update=False)
    assert [r.getMessage() for r in _mirrored(caplog)] == ["▶ 開始：提取"]


def test_backend_source_and_add_many_do_not_mirror(caplog):
    view = _view()
    with caplog.at_level(logging.INFO):
        view.add("來自 logger", source="logger", update=False)
        view.add_many([("批次行", "info")])
    assert _mirrored(caplog) == []


def test_mirror_lines_dedupes_against_backend(caplog):
    ui_mirror.ensure_tracker()
    with caplog.at_level(logging.INFO):
        logging.getLogger("core").info("後台已有")
        ui_mirror.mirror_lines([("後台已有", "info"), ("只有 UI", "warning")])
    assert [r.getMessage() for r in _mirrored(caplog)] == ["只有 UI"]


# ---------------------------------------------------------------- UI 自己的事件 vs 轉送後台內容


def test_ui_own_event_is_written_even_if_the_backend_has_identical_text(caplog):
    """UI 自己的事件（按鈕、重置…）後台沒有對應記錄，不能因為別筆同文字的後台記錄而被吃掉。"""
    ui_mirror.ensure_tracker()
    view = _view()
    with caplog.at_level(logging.INFO):
        logging.getLogger("other").info("開始執行")  # 與這個事件無關的後台記錄
        view.add("開始執行", update=False)  # 預設：UI 自己的事件，無條件寫入

    assert [r.getMessage() for r in _mirrored(caplog)] == ["開始執行"]


def test_forwarded_content_is_deduped_when_dedupe_is_requested(caplog):
    ui_mirror.ensure_tracker()
    view = _view()
    with caplog.at_level(logging.INFO):
        logging.getLogger("core").info("核心流程已記錄")
        view.add("核心流程已記錄", update=False, dedupe=True)  # 轉送：後台已有，不重複

    assert _mirrored(caplog) == []


def _fake_extractor_ctx():
    class _Batcher:
        def __init__(self):
            self.lines = []

        def add_lines(self, lines):
            self.lines.extend(lines)

    class _Ctx:
        batcher = _Batcher()

        def flush_ui(self):
            pass

    return _Ctx()


def test_extractor_dialog_logs_ui_events_unconditionally_and_dedupes_forwarded(caplog):
    from app.views.extractor.extractor_dialog_ui import _extractor_add_log

    ui_mirror.ensure_tracker()
    ctx = _fake_extractor_ctx()
    with caplog.at_level(logging.INFO):
        logging.getLogger("core").info("[1/3] a.jar")
        _extractor_add_log(ctx, "[1/3] a.jar", forwarded=True)  # 核心流程 yield 的 log
        _extractor_add_log(ctx, "[系統] 任務已取消", "warning")  # 對話框自己的事件
        logging.getLogger("other").info("[系統] 任務已取消")
        _extractor_add_log(
            ctx, "[系統] 任務已取消", "warning"
        )  # 自己的事件不被別筆同文字吃掉

    assert [r.getMessage() for r in _mirrored(caplog)] == [
        "[系統] 任務已取消",
        "[系統] 任務已取消",
    ]
    assert len(ctx.batcher.lines) == 3  # 畫面三行都有


def test_preview_dialog_forwarded_flag_controls_dedupe(caplog):
    from types import SimpleNamespace

    from app.views.extractor.extractor_preview_dialog import _preview_add_log

    ui_mirror.ensure_tracker()
    ctx = SimpleNamespace(log_view=_view())
    with caplog.at_level(logging.INFO):
        logging.getLogger("core").info("掃描 a.jar")
        _preview_add_log(ctx, "掃描 a.jar", update=False, forwarded=True)
        _preview_add_log(ctx, "[系統] 開始預覽", "system", update=False)

    assert [r.getMessage() for r in _mirrored(caplog)] == ["[系統] 開始預覽"]


def test_in_new_task_gives_each_worker_run_its_own_attribution():
    import threading

    seen = []

    def probe():
        seen.append(ui_mirror.current_task())

    for _ in range(2):
        t = threading.Thread(target=ui_mirror.in_new_task("qc", probe))
        t.start()
        t.join()

    assert len(seen) == 2 and None not in seen and seen[0] != seen[1]
    assert str(seen[0]).startswith("qc-")
    assert ui_mirror.current_task() is None  # 不外洩到呼叫端
