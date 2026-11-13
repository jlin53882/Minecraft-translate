"""消除「盲等」：快取重載／儲存／重建索引回報真實進度，設定頁清除自訂來源改在背景執行。"""

from __future__ import annotations

import asyncio
import threading

import flet as ft

from app.services_impl.cache import cache_services
from translation_tool.utils import cache_manager
from translation_tool.utils.cache_search import rebuild_from_cache_dicts

from .test_cache_lazy_reads import _prepare_disk_cache


def _assert_sane(calls: list[tuple[int, int]]):
    assert calls, "應至少回報一次進度"
    totals = {total for _cur, total in calls}
    assert len(totals) == 1
    total = totals.pop()
    values = [cur for cur, _total in calls]
    assert values == sorted(values) and len(set(values)) == len(values)
    assert 0 < values[0] and values[-1] <= total


class _ProgressFacade:
    """只實作重建索引，並依真實類型逐一回報進度的搜尋外觀替身。"""

    def rebuild_search_index(self, cache_types, translation_cache, on_progress=None):
        for index, cache_type in enumerate(cache_types, start=1):
            if on_progress is not None:
                on_progress(index, len(cache_types), f"已索引 {cache_type}")
        return True


def test_reload_service_reports_monotonic_progress_across_load_and_index(
    tmp_path, monkeypatch
):
    _prepare_disk_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(cache_manager, "_get_search_facade", lambda: _ProgressFacade())
    calls: list[tuple[int, int]] = []

    cache_services.cache_reload_service(
        on_progress=lambda cur, total, _msg=None: calls.append((cur, total))
    )

    type_count = len(cache_manager.CACHE_TYPES)
    _assert_sane(calls)
    assert calls[-1] == (type_count * 2, type_count * 2)
    assert len(calls) == type_count * 2


def test_reload_service_ignores_progress_regression_from_index_retry(monkeypatch):
    type_count = len(cache_manager.CACHE_TYPES)

    def fake_reload(on_progress=None):
        for i in range(1, type_count + 1):
            on_progress(i, type_count, "")

    def fake_rebuild(on_progress=None):
        for _attempt in range(2):  # 檔案鎖重試：第二輪從頭開始
            for i in range(1, type_count + 1):
                on_progress(i, type_count, "")
        return True

    monkeypatch.setattr(cache_manager, "reload_translation_cache", fake_reload)
    monkeypatch.setattr(cache_manager, "rebuild_search_index", fake_rebuild)
    monkeypatch.setattr(cache_manager, "get_cache_overview", dict)
    calls: list[tuple[int, int]] = []

    cache_services.cache_reload_service(
        on_progress=lambda cur, total, _msg=None: calls.append((cur, total))
    )

    _assert_sane(calls)


def test_save_service_reports_progress_per_type(monkeypatch):
    monkeypatch.setattr(cache_manager, "save_translation_cache", lambda *_a, **_k: True)
    monkeypatch.setattr(cache_manager, "get_cache_overview", dict)
    calls: list[tuple[int, int]] = []

    cache_services.cache_save_all_service(
        only_types=["lang", "md"],
        on_progress=lambda cur, total, _msg=None: calls.append((cur, total)),
    )

    assert calls == [(1, 2), (2, 2)]


def test_rebuild_index_service_reports_progress(monkeypatch):
    type_count = len(cache_manager.CACHE_TYPES)

    def fake_rebuild(on_progress=None):
        for i in range(1, type_count + 1):
            on_progress(i, type_count, "")
        return True

    monkeypatch.setattr(cache_manager, "rebuild_search_index", fake_rebuild)
    calls: list[tuple[int, int]] = []

    result = cache_services.cache_rebuild_index_service(
        on_progress=lambda cur, total, _msg=None: calls.append((cur, total))
    )

    assert result["success"] is True
    _assert_sane(calls)
    assert calls[-1] == (type_count, type_count)


def test_rebuild_from_cache_dicts_reports_once_per_type():
    class _Engine:
        def index_batch(self, entries):
            pass

    calls: list[tuple[int, int]] = []
    rebuild_from_cache_dicts(
        _Engine(),
        ["lang", "md"],
        {"lang": {}, "md": {}},
        lambda cur, total, _msg=None: calls.append((cur, total)),
    )

    assert calls == [(1, 2), (2, 2)]


def test_progress_callback_failure_does_not_break_reload(tmp_path, monkeypatch):
    _prepare_disk_cache(tmp_path, monkeypatch)

    def boom(*_a):
        raise RuntimeError("ui gone")

    cache_manager.reload_translation_cache(on_progress=boom)

    assert cache_manager.is_cache_initialized()


def test_reload_all_action_receives_progress_callback():
    """「全部重新載入」的 work_fn 必須有 on_progress 參數，run_cache_action 才會注入回呼。"""
    from app.views.cache_manager import cache_view_overview
    from app.views.cache_manager.cache_actions import _accepts_on_progress

    captured = {}

    class _View:
        def _run_action(self, reason, work_fn, success_msg, **kwargs):
            captured["work_fn"] = work_fn
            captured["show_progress"] = kwargs.get("show_progress")

    seen = {}
    original = cache_view_overview.cache_reload_service
    cache_view_overview.cache_reload_service = lambda on_progress=None: seen.update(
        cb=on_progress
    )
    try:
        cache_view_overview.CacheOverviewMixin._on_reload_all(_View(), None)
        marker = object()
        assert _accepts_on_progress(captured["work_fn"])
        captured["work_fn"](on_progress=marker)
    finally:
        cache_view_overview.cache_reload_service = original

    assert captured["show_progress"] is True
    assert seen["cb"] is marker


# ---------------- 設定頁：清除未使用的自訂來源改背景執行 ----------------


class _FakePage:
    """run_task 立刻在呼叫端排隊，測試手動 drain，模擬 UI 事件迴圈。"""

    def __init__(self):
        self.tasks = []

    def run_task(self, coro_fn, *args):
        self.tasks.append((coro_fn, args))

    async def drain(self):
        while self.tasks:
            fn, args = self.tasks.pop(0)
            await fn(*args)


def _banner(monkeypatch, db_location):
    monkeypatch.setattr(
        db_location, "current_settings", lambda: type("S", (), {"path": "x.db"})()
    )
    monkeypatch.setattr(
        db_location,
        "describe_db_path",
        lambda _p: ("ok", "ok", __import__("pathlib").Path("x.db")),
    )
    banner = db_location.DbLocationBanner()
    page = _FakePage()
    monkeypatch.setattr(banner, "_page_or_none", lambda: page)
    return banner, page


def _run_background(monkeypatch, db_location, record):
    """launch_page_operation 替身：在另一條執行緒跑，並記錄執行緒 id。"""

    def fake_launch(_page, target, **_kwargs):
        def run():
            record["worker_thread"] = threading.get_ident()
            target()

        thread = threading.Thread(target=run)
        record["thread"] = thread
        thread.start()
        return True

    monkeypatch.setattr(db_location, "launch_page_operation", fake_launch)


def test_prune_runs_off_ui_thread_and_restores_button(monkeypatch):
    from app.views.config import db_location

    banner, page = _banner(monkeypatch, db_location)
    record: dict = {}
    _run_background(monkeypatch, db_location, record)
    release = threading.Event()

    def fake_report(_path):
        record["prune_thread"] = threading.get_ident()
        release.wait(5)
        return ["測試"], [], {"測試"}

    monkeypatch.setattr(db_location, "prune_unused_custom_sources_report", fake_report)
    pruned: list[set[str]] = []
    banner.on_pruned = pruned.append

    banner._on_prune()

    assert banner.prune_btn.disabled is True
    assert "請稍候" in banner.note.value
    release.set()
    record["thread"].join(5)
    asyncio.run(page.drain())

    assert record["prune_thread"] != threading.get_ident()
    assert banner.prune_btn.disabled is False
    assert "測試" in banner.note.value
    assert pruned == [{"測試"}]


def test_prune_error_restores_button_and_shows_reason(monkeypatch):
    from app.views.config import db_location

    banner, page = _banner(monkeypatch, db_location)
    record: dict = {}
    _run_background(monkeypatch, db_location, record)

    def boom(_path):
        raise OSError("disk locked")

    monkeypatch.setattr(db_location, "prune_unused_custom_sources_report", boom)
    monkeypatch.setattr(db_location, "log_error", lambda *_a, **_k: None)

    banner._on_prune()
    record["thread"].join(5)
    asyncio.run(page.drain())

    assert banner.prune_btn.disabled is False
    assert "失敗" in banner.note.value and "disk locked" in banner.note.value


def test_prune_not_launched_when_app_closing_restores_button(monkeypatch):
    from app.views.config import db_location

    banner, _page = _banner(monkeypatch, db_location)
    monkeypatch.setattr(db_location, "launch_page_operation", lambda *_a, **_k: False)

    banner._on_prune()

    assert banner.prune_btn.disabled is False
    assert "失敗" in banner.note.value


def test_refresh_keeps_button_disabled_while_pruning(monkeypatch):
    from app.views.config import db_location

    banner, _page = _banner(monkeypatch, db_location)
    banner._pruning = True

    banner.refresh()

    assert banner.prune_btn.disabled is True
    assert isinstance(banner.prune_btn, ft.Button)
