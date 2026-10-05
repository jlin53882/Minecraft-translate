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
