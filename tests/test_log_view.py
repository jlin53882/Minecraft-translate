"""tests/test_log_view.py

LogView widget 單元測試。

PR refactor/unified-log-view Stage 2 驗證。

驗證項目：
- 預設外觀（theme token、Consolas、圓角 8）
- add() 行為（不走 LogPresenter、直接 append）
- add_error() 用 theme.TEXT_LOG_ERROR
- clear() 清空
- sync_from_session() 從 TaskSession 拿資料
- show_levels 過濾
- max_lines 截斷
- tail 模式 tail_lines 截斷
"""

from unittest.mock import MagicMock

import flet as ft

from app.ui import theme
from app.views._log import LogEntry, LogView, TaskSession


def test_log_view_default_appearance():
    """預設外觀：theme token、Consolas、圓角 8。"""
    page = MagicMock()
    view = LogView(page=page)
    assert view.bgcolor == theme.BG_LOG_PANEL
    assert view.border_radius == 8
    assert view.padding == 10
    assert isinstance(view.content, ft.ListView)
    assert view.content.spacing == 4
    assert view.content.auto_scroll is True


def test_log_view_add_appends_text_entry():
    """add() 應直接 append ft.Text（不走 LogPresenter）。"""
    page = MagicMock()
    view = LogView(page=page)
    view.add("hello", level="info")
    assert len(view._list_view.controls) == 1
    assert isinstance(view._list_view.controls[0], ft.Text)


def test_log_view_add_error_uses_red():
    """add_error 應用 theme.TEXT_LOG_ERROR。"""
    page = MagicMock()
    view = LogView(page=page)  # colorize 內部 hardcode，不需傳入
    view.add_error("boom")
    text_control = view._list_view.controls[0]
    # add() 直接走 theme token（不是 LogPresenter）
    assert text_control.color == theme.TEXT_LOG_ERROR


def test_log_view_clear_empties():
    """clear() 應清空所有 log。"""
    view = LogView(page=MagicMock())
    view.add("a")
    view.add("b")
    view.clear()
    assert len(view._list_view.controls) == 0


def test_log_view_sync_from_session():
    """sync_from_session 應讀取 TaskSession.snapshot() 並渲染。"""
    page = MagicMock()
    session = TaskSession()
    session.add_log("line 1", level="info")
    session.add_log("line 2", level="error")

    view = LogView(page=page)
    view.sync_from_session(session)
    assert len(view._list_view.controls) == 2


def test_log_view_filters_levels():
    """add() 應套用 show_levels 白名單過濾。"""
    view = LogView(page=MagicMock(), show_levels=["error"])
    view.add("info line", level="info")
    view.add("error line", level="error")
    assert len(view._list_view.controls) == 1


def test_log_view_truncates_over_max():
    """add() append 模式超過 max_lines 應自動截斷。"""
    view = LogView(page=MagicMock(), max_lines=3)
    for i in range(5):
        view.add(f"line {i}")
    assert len(view._list_view.controls) == 3


def test_log_view_tail_mode():
    """add() tail 模式只保留最後 tail_lines 筆。"""
    view = LogView(page=MagicMock(), mode="tail", tail_lines=2)
    for i in range(5):
        view.add(f"line {i}")
    # tail 模式只保留最後 2 筆
    assert len(view._list_view.controls) == 2