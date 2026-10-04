"""#117：log_level 存檔即時套用、日誌行數下次任務套用、打包頁提示重讀。"""

from __future__ import annotations

import logging

from app.services_impl import logging_service as ls


class _Cfg:
    def __init__(self, level="INFO", fmt="%(message)s"):
        self.level = level
        self.fmt = fmt
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {"logging": {"log_level": self.level, "log_format": self.fmt}}


def test_level_change_is_applied_immediately():
    cfg = _Cfg("DEBUG")
    root = logging.getLogger()
    before = root.level
    try:
        assert ls.apply_logging_config_if_changed(frozenset({"logging.log_level"}), cfg)
        assert root.level == logging.DEBUG
        assert ls.UI_LOG_HANDLER.level == logging.DEBUG
        cfg.level = "WARNING"
        assert ls.apply_logging_config_if_changed(frozenset({"logging.log_level"}), cfg)
        assert root.level == logging.WARNING
    finally:
        root.setLevel(before)


def test_unrelated_paths_do_not_touch_logging():
    cfg = _Cfg()
    assert not ls.apply_logging_config_if_changed(frozenset({"translator.x"}), cfg)
    assert cfg.calls == 0


def test_apply_failure_is_swallowed():
    def boom():
        raise RuntimeError("config broken")

    assert not ls.apply_logging_config_if_changed(
        frozenset({"logging.log_level"}), boom
    )


def test_watch_subscribes_and_unsubscribes():
    registered = []

    def subscribe(cb):
        registered.append(cb)
        return lambda: registered.remove(cb)

    cfg = _Cfg("ERROR")
    root = logging.getLogger()
    before = root.level
    try:
        unsub = ls.watch_logging_config(subscribe, cfg)
        registered[0](frozenset({"logging.log_level"}))
        assert root.level == logging.ERROR
        unsub()
        assert registered == []
    finally:
        root.setLevel(before)


def test_log_view_tail_lines_updates_presenter():
    from app.views._log import LogView

    class Page:
        def update(self):
            pass

    view = LogView(page=Page(), mode="tail", tail_lines=250)
    view.set_tail_lines(40)
    assert view.tail_lines == 40 and view._presenter.tail_lines == 40
    for bad in (0, -5, True, "x", None):
        view.set_tail_lines(bad)
    assert view.tail_lines == 40
