"""Flet 1.0 遷移後的 UI 回歸測試。

涵蓋：
- 快速跳轉面板：`ft.Colors.BLACK38` 在 1.0 移除；`self.page` 為唯讀 property；focus() 改為 coroutine。
- PipelineView：五個任務按鈕曾引用已刪除的 `_show_snack_bar`。
- 快取管理複製：`page.set_clipboard()` 不存在，改用 `ft.Clipboard().set()`（async）。
"""

import asyncio

import flet as ft

from app.ui import quick_jump
from app.views import cache_shard_panel, cache_view
from app.views.cache_manager.cache_state import CacheShardState
from app.views.cache_shard_panel import CacheShardPanel
from app.views.pipeline import pipeline_view
from app.views.pipeline.pipeline_view import PipelineView
from tests.conftest import mock_filepicker, mock_page


class _FakeClipboard:
    """取代 ft.Clipboard，記錄 set() 的內容。"""

    values: list = []

    async def set(self, value: str) -> None:
        _FakeClipboard.values.append(value)


def _patch_clipboard(monkeypatch):
    _FakeClipboard.values = []
    monkeypatch.setattr(ft, "Clipboard", _FakeClipboard)
    return _FakeClipboard.values


# -----------------------------------------------------------------------------
# 快速跳轉面板
# -----------------------------------------------------------------------------

def test_show_quick_jump_panel_adds_overlay_and_schedules_focus():
    page = mock_page()
    scheduled = []
    page.run_task = lambda handler, *a, **k: scheduled.append(handler)
    registry = [{"key": "config", "view": ft.Container()}, {"key": "rules", "view": ft.Container()}]

    quick_jump.show_quick_jump_panel(page, registry, lambda i: None)

    overlay = page.overlay[-1]
    assert overlay.bgcolor == ft.Colors.BLACK_38
    assert isinstance(overlay.content, quick_jump.QuickJumpPanel)
    # focus() 在 Flet 1.0 是 coroutine，必須經 run_task 排程而非直接呼叫
    assert scheduled == [overlay.content.search_field.focus]


def test_quick_jump_panel_does_not_assign_readonly_page_property():
    page = mock_page()
    panel = quick_jump.QuickJumpPanel(
        page=page,
        view_registry=[{"key": "config", "view": ft.Container()}],
        on_jump_callback=lambda i: None,
        on_close_callback=lambda: None,
    )
    assert panel._page is page


# -----------------------------------------------------------------------------
# PipelineView 任務按鈕
# -----------------------------------------------------------------------------

def test_pipeline_task_buttons_pass_callable_show_snack_bar(monkeypatch, tmp_path):
    captured = {}

    def _fake_open(name):
        def _open(**kwargs):
            captured[name] = kwargs["show_snack_bar"]
        return _open

    for name in ("extract", "merge", "translate", "bundle", "one_click"):
        monkeypatch.setattr(pipeline_view, f"open_{name}_dialog", _fake_open(name))
    shown = []
    monkeypatch.setattr(pipeline_view, "show_snack", lambda page, msg, *a, **k: shown.append(msg))

    view = PipelineView(mock_page(), mock_filepicker())
    view.input_path_text.value = str(tmp_path)
    view.output_path_text.value = str(tmp_path / "out")

    view._on_extract_click()
    view._on_merge_click()
    view._on_translate_click()
    view._on_bundle_click()
    view._on_one_click_click()

    assert set(captured) == {"extract", "merge", "translate", "bundle", "one_click"}
    for name, show_snack_bar in captured.items():
        show_snack_bar(f"msg-{name}")
    assert shown == [f"msg-{n}" for n in captured]


# -----------------------------------------------------------------------------
# 剪貼簿複製
# -----------------------------------------------------------------------------

def test_cache_view_copy_logs_uses_clipboard_service(monkeypatch):
    values = _patch_clipboard(monkeypatch)
    snacks = []
    monkeypatch.setattr(cache_view, "show_snack", lambda page, msg, *a, **k: snacks.append(msg))
    view = cache_view.CacheView.__new__(cache_view.CacheView)
    view._all_logs = ["[INFO] a", "[WARN] b"]
    monkeypatch.setattr(cache_view.CacheView, "page", property(lambda self: mock_page()))

    asyncio.run(view._copy_logs())

    assert values == ["[INFO] a\n[WARN] b"]
    assert snacks == ["已複製日誌"]


def test_cache_view_shard_dst_copy_uses_clipboard_service(monkeypatch):
    values = _patch_clipboard(monkeypatch)
    view = cache_view.CacheView.__new__(cache_view.CacheView)
    view.shard_detail_selected_key = "k"
    view.shard_dst_field = ft.TextField(value="目標內容")
    notes = []
    view._notify = lambda msg, level: notes.append((msg, level))

    asyncio.run(view._on_shard_dst_copy(None))

    assert values == ["目標內容"]
    assert notes == [("已複製 C3 DST 內容", "info")]


def test_cache_shard_panel_dst_copy_uses_clipboard_service(monkeypatch):
    values = _patch_clipboard(monkeypatch)
    snacks = []
    monkeypatch.setattr(cache_shard_panel, "show_snack", lambda page, msg, *a, **k: snacks.append(msg))
    state = CacheShardState()
    state.selected_key = "k"
    panel = CacheShardPanel(mock_page(), state, {"types": {}})
    panel.shard_dst_field.value = "DST 內容"

    asyncio.run(panel._on_shard_dst_copy(None))

    assert values == ["DST 內容"]
    assert snacks == ["已複製 DST 內容"]
