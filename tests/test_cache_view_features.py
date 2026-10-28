import asyncio
import json
import threading
import time

import flet as ft
import pytest

from app.services_impl.cache import cache_services
from app.services_impl.cache.cache_services import CacheSaveError
from app.tasks.operation_registry import OperationDescriptor, OperationRegistry
from app.views.cache_manager import cache_history_store
from app.views.cache_manager import cache_view_overview as cache_overview_module
from app.views.cache_manager import cache_view_query as cache_query_module
from app.views.cache_manager import cache_view_shard_detail as cache_shard_detail_module
from app.views.cache_manager.cache_actions import _execute_cache_work
from app.views.cache_view import CacheView


def test_cache_action_handled_error_is_a_failed_operation():
    registry = OperationRegistry()
    handle = registry.reserve(OperationDescriptor(name="cache save", owner="test"))
    assert handle is not None
    failure = OSError("cache destination unavailable")
    payloads = []

    assert handle.launch(
        lambda: payloads.append(
            _execute_cache_work(None, lambda: (_ for _ in ()).throw(failure), None)
        )
    )
    assert handle.done_event.wait(2)
    assert handle.terminal_reason == "failed"
    assert handle.error is failure
    assert payloads[0][0] is None
    assert payloads[0][1][0] is failure


class FakePage:
    def __init__(self):
        self.updated = 0
        self.overlay = []
        self.clipboard = ""

    def update(self):
        self.updated += 1

    def set_clipboard(self, text: str):
        self.clipboard = text


def _prepare_cache_action_view(page):
    view = CacheView.__new__(CacheView)
    view._page = page
    view.ui_busy = False
    view._all_logs = []
    view._last_overview_data = {}
    view.overview_status = type("Status", (), {"value": ""})()
    view._notify_calls = []
    view._notify = lambda msg, level="info": view._notify_calls.append((level, msg))
    view._append_log = lambda _text: None
    view._set_state = lambda busy, _reason, _trace: setattr(view, "ui_busy", busy)
    view._refresh_overview_ui = lambda _data: None
    view._refresh_query_type_options = lambda: None
    view._render_query_type_shard_page = lambda: None
    view._warm_shard_cache = lambda _data: None
    view._refresh_disabled_state = lambda: None
    return view


def test_load_shard_rows_includes_cleaned_shards_and_excludes_cache_main(tmp_path):
    cache_root = tmp_path / "cache_root"
    lang_dir = cache_root / "lang"
    lang_dir.mkdir(parents=True)

    # Should be excluded
    (lang_dir / "lang_cache_main.json").write_text("{}", encoding="utf-8")
    # Should be included
    (lang_dir / "lang_00001.json").write_text(
        json.dumps({"k1": {}}, ensure_ascii=False), encoding="utf-8"
    )
    (lang_dir / "cleaned_shard_00002.json").write_text(
        json.dumps({"k2": {}, "k3": {}}, ensure_ascii=False), encoding="utf-8"
    )

    view = CacheView.__new__(CacheView)
    view._last_overview_data = {"cache_root": str(cache_root)}

    rows = CacheView._load_shard_rows(view, "lang", "00001", 2500)
    names = [r["filename"] for r in rows]

    assert "lang_cache_main.json" not in names
    assert "lang_00001.json" in names
    assert "cleaned_shard_00002.json" in names
    active = [r for r in rows if r["is_active"]]
    assert len(active) == 1
    assert active[0]["filename"] == "lang_00001.json"


def test_format_shard_src_text_preview_and_raw_modes():
    view = CacheView.__new__(CacheView)

    src = "line1\\nline2"
    preview = CacheView._format_shard_src_text(view, src, "preview")
    raw = CacheView._format_shard_src_text(view, src, "raw")

    assert preview == "line1\nline2"
    assert raw.startswith('"') and raw.endswith('"')
    assert "\\\\n" in raw


def test_on_shard_dst_apply_updates_cache_and_history(monkeypatch):
    tasks = []

    class LoopPage(FakePage):
        def run_task(self, handler, *args):
            tasks.append((handler, args))

    view = CacheView.__new__(CacheView)
    view._page = LoopPage()
    view.ui_busy = False
    view._all_logs = []
    view._last_overview_data = {}
    view.overview_status = type("Status", (), {"value": ""})()

    view.shard_detail_selected_type = "lang"
    view.shard_detail_selected_file = "lang_00001.json"
    view.shard_detail_selected_key = "k1"
    view.shard_dst_original = "old"
    view.shard_dst_loaded_sig = ("lang", "lang_00001.json", "k1")
    view.shard_dst_field = ft.TextField(value="new")

    view.query_results = [{"cache_type": "lang", "key": "k1", "preview": "old"}]

    notify_calls = []
    history_calls = []
    save_calls = []
    update_calls = []

    view._notify = lambda msg, level="info": notify_calls.append((level, msg))
    view._history_now_ts = lambda: "2026-02-12T02:00:00+08:00"
    view._history_append_event = lambda ctype, event: history_calls.append(
        (ctype, event)
    )
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None
    view._render_shard_src_panel = lambda: None
    view._render_shard_dst_panel = lambda: None
    view._refresh_disabled_state = lambda: None
    view._append_log = lambda _text: None
    view._set_state = lambda busy, _reason, _trace: setattr(view, "ui_busy", busy)
    view._refresh_overview_ui = lambda _data: None
    view._refresh_query_type_options = lambda: None
    view._render_query_type_shard_page = lambda: None
    view._warm_shard_cache = lambda _data: None

    worker_threads = []

    monkeypatch.setattr(
        cache_shard_detail_module,
        "cache_update_dst_service",
        lambda ctype, key, new_dst: (
            worker_threads.append(threading.current_thread())
            or update_calls.append((ctype, key, new_dst))
            or True
        ),
    )
    monkeypatch.setattr(
        cache_shard_detail_module,
        "cache_save_all_service",
        lambda **kwargs: (
            worker_threads.append(threading.current_thread())
            or save_calls.append(kwargs)
            or {"ok": True}
        ),
    )

    CacheView._on_shard_dst_apply(view, None)
    assert update_calls == []
    assert save_calls == []
    assert tasks

    handler, args = tasks.pop(0)
    asyncio.run(handler(*args))

    assert update_calls == [("lang", "k1", "new")]
    assert save_calls == [{"write_new_shard": False, "only_types": ["lang"]}]
    assert history_calls and history_calls[0][0] == "lang"
    assert history_calls[0][1]["action"] == "apply_from_shard_detail"
    assert history_calls[0][1]["old_dst"] == "old"
    assert history_calls[0][1]["new_dst"] == "new"
    assert view.query_results[0]["preview"] == "new"
    assert view.shard_dst_original == "new"
    assert any("已套用 C3 DST" in msg for _, msg in notify_calls)
    assert worker_threads and all(
        t is not threading.main_thread() for t in worker_threads
    )


def test_query_dst_apply_returns_before_delayed_save_and_commits_on_ui_task(
    monkeypatch,
):
    tasks = []

    class LoopPage(FakePage):
        def run_task(self, handler, *args):
            tasks.append((handler, args))

    view = _prepare_cache_action_view(LoopPage())
    view.query_selected_result = {"cache_type": "lang", "key": "k1", "shard": "s1"}
    view.query_detail_dst = ft.TextField(value="new")
    view.query_original_dst = "old"
    view.query_results = [{"cache_type": "lang", "key": "k1", "preview": "old"}]
    view._history_now_ts = lambda: "2026-02-12T02:00:00+08:00"
    history_calls = []
    view._history_append_event = lambda ctype, event: history_calls.append(
        (ctype, event)
    )
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None
    worker_threads = []
    save_started = threading.Event()

    monkeypatch.setattr(
        cache_query_module,
        "cache_update_dst_service",
        lambda *_args: worker_threads.append(threading.current_thread()) or True,
    )

    def delayed_save(**_kwargs):
        worker_threads.append(threading.current_thread())
        save_started.set()
        time.sleep(0.05)
        return {"ok": True}

    monkeypatch.setattr(cache_query_module, "cache_save_all_service", delayed_save)

    started = time.perf_counter()
    CacheView._on_apply_dst(view, None)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.04
    assert not save_started.is_set()
    assert tasks
    assert view.query_results[0]["preview"] == "old"

    handler, args = tasks.pop(0)
    asyncio.run(handler(*args))

    assert save_started.is_set()
    assert view.query_results[0]["preview"] == "new"
    assert history_calls and history_calls[0][1]["action"] == "apply"
    assert any("已套用並寫入快取" in msg for _, msg in view._notify_calls)
    assert worker_threads and all(
        t is not threading.main_thread() for t in worker_threads
    )


def test_cache_apply_save_failure_does_not_append_history_or_report_success(
    monkeypatch,
):
    tasks = []

    class LoopPage(FakePage):
        def run_task(self, handler, *args):
            tasks.append((handler, args))

    view = _prepare_cache_action_view(LoopPage())
    view.shard_detail_selected_type = "lang"
    view.shard_detail_selected_file = "lang_00001.json"
    view.shard_detail_selected_key = "k1"
    view.shard_dst_original = "old"
    view.shard_dst_field = ft.TextField(value="new")
    view.shard_dst_loaded_sig = ("lang", "lang_00001.json", "k1")
    view.query_results = [{"cache_type": "lang", "key": "k1", "preview": "old"}]
    view._history_now_ts = lambda: "2026-02-12T02:00:00+08:00"
    history_calls = []
    view._history_append_event = lambda *args: history_calls.append(args)
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None
    view._render_shard_src_panel = lambda: None
    view._render_shard_dst_panel = lambda: None

    monkeypatch.setattr(
        cache_shard_detail_module,
        "cache_update_dst_service",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        cache_shard_detail_module,
        "cache_save_all_service",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    CacheView._on_shard_dst_apply(view, None)
    assert tasks
    handler, args = tasks.pop(0)
    asyncio.run(handler(*args))

    assert history_calls == []
    assert view.shard_dst_original == "old"
    assert not any("已套用 C3 DST" in msg for _, msg in view._notify_calls)
    assert any(level == "error" for level, _ in view._notify_calls)
    assert view.ui_busy is False


def test_refresh_stats_is_background_and_disables_duplicate_action(monkeypatch):
    tasks = []

    class LoopPage(FakePage):
        def run_task(self, handler, *args):
            tasks.append((handler, args))

    view = _prepare_cache_action_view(LoopPage())
    refreshed = []
    worker_threads = []

    def load_overview():
        worker_threads.append(threading.current_thread())
        return {"total_entries": 3, "types": {}}

    monkeypatch.setattr(
        cache_overview_module, "cache_get_overview_service", load_overview
    )
    view._refresh_overview_ui = lambda data: refreshed.append(data)

    CacheView._on_refresh_stats(view, None)
    assert view.ui_busy is True
    assert worker_threads == []
    assert tasks

    handler, args = tasks.pop(0)
    asyncio.run(handler(*args))

    assert refreshed == [{"total_entries": 3, "types": {}}]
    assert view.ui_busy is False
    assert worker_threads and worker_threads[0] is not threading.main_thread()
    assert any("已刷新統計" in msg for _, msg in view._notify_calls)


def test_cache_save_service_rejects_false_result(monkeypatch):
    monkeypatch.setattr(cache_services.cache_manager, "CACHE_TYPES", ["lang"])
    monkeypatch.setattr(
        cache_services.cache_manager,
        "save_translation_cache",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        cache_services.cache_manager,
        "get_cache_overview",
        lambda: {"dirty_type_count": 1},
    )

    with pytest.raises(CacheSaveError, match="lang") as exc_info:
        cache_services.cache_save_all_service(write_new_shard=False)

    assert exc_info.value.cache_types == ("lang",)
    assert exc_info.value.overview == {"dirty_type_count": 1}


def test_cold_history_index_is_loaded_in_background_and_stale_selection_is_dropped(
    tmp_path,
):
    tasks = []

    class LoopPage(FakePage):
        def run_task(self, handler, *args):
            tasks.append((handler, args))

    history_dir = tmp_path / "cache_history" / "lang" / "jsonl"
    history_dir.mkdir(parents=True)
    (history_dir / "lang_h000001.jsonl").write_text(
        json.dumps({"key": "k1", "old_dst": "old", "new_dst": "new"}) + "\n",
        encoding="utf-8",
    )
    cache_history_store.clear_history_memo()

    view = CacheView.__new__(CacheView)
    view._page = LoopPage()
    view._last_overview_data = {"cache_root": str(tmp_path)}
    view.query_selected_result = {"cache_type": "lang", "key": "k1"}
    view._refresh_disabled_state = lambda: None
    loaded = []

    records = CacheView._history_load_for_ui(
        view,
        "query",
        "lang",
        "k1",
        20,
        on_loaded=lambda rows: loaded.append(rows),
    )
    assert records is None
    assert tasks
    assert loaded == []

    # The user changes the selected result while the JSONL parser is running.
    view.query_selected_result = {"cache_type": "lang", "key": "k2"}
    handler, args = tasks.pop(0)
    asyncio.run(handler(*args))
    assert loaded == []

    # The worker still warmed the index, so the next selection is a cache hit.
    view.query_selected_result = {"cache_type": "lang", "key": "k1"}
    records = CacheView._history_load_for_ui(view, "query", "lang", "k1", 20)
    assert records and records[0]["new_dst"] == "new"
    cache_history_store.clear_history_memo()


def test_on_query_search_all_mode_deduplicates_per_type_key(monkeypatch):
    view = CacheView.__new__(CacheView)
    view._page = FakePage()
    view.ui_busy = False

    view.tf_query_input = ft.TextField(value="abc")
    view.dd_query_mode = ft.Dropdown(value="ALL")
    view.dd_query_type = ft.Dropdown(value="ALL")
    view.query_search_hint = ft.Text(value="")

    view._last_overview_data = {
        "types": {
            "lang": {"active_shard_id": "00001"},
            "patchouli": {"active_shard_id": "00002"},
        }
    }

    view.query_results = []
    view.query_selected_result = None
    view.query_page = 1

    # PR5-7: 新增屬性
    view.query_change_hint = ft.Text(value="")

    # 避免 update 錯誤
    view.update = lambda: None

    view._notify = lambda *args, **kwargs: None
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None

    def fake_search(cache_type, query, mode, limit):
        assert query == "abc"
        if mode == "key":
            return {"items": [{"key": "k1"}, {"key": "k2"}]}
        if mode == "dst":
            return {
                "items": [
                    {"key": "k2", "preview": "dup"},
                    {"key": "k3", "preview": "p3"},
                ]
            }
        return {"items": []}

    monkeypatch.setattr(cache_query_module, "cache_search_service", fake_search)
    monkeypatch.setattr(
        cache_query_module,
        "cache_get_entry_service",
        lambda ctype, key: {"dst": f"dst-{ctype}-{key}"},
    )

    CacheView._on_query_search(view, None)

    keys = {(r["cache_type"], r["key"]) for r in view.query_results}
    assert len(keys) == 6  # 2 cache types * (k1, k2, k3)
    assert len(view.query_results) == 6
    assert view.query_selected_result is not None
    assert view.query_search_hint.value.startswith("搜尋完成：6 筆")


def test_on_query_search_requires_input(monkeypatch):
    view = CacheView.__new__(CacheView)
    view._page = FakePage()
    view.ui_busy = False

    view.tf_query_input = ft.TextField(value="")
    view.dd_query_mode = ft.Dropdown(value="ALL")
    view.dd_query_type = ft.Dropdown(value="ALL")
    view.query_search_hint = ft.Text(value="")
    view._last_overview_data = {"types": {"lang": {}}}
    view.query_results = []
    view.query_selected_result = None

    calls = []
    view._notify = lambda msg, level="info": calls.append((level, msg))
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None

    monkeypatch.setattr(
        cache_query_module,
        "cache_search_service",
        lambda *args, **kwargs: {"items": []},
    )

    CacheView._on_query_search(view, None)

    assert calls and calls[0][0] == "warn"
    assert "請輸入查詢內容" in calls[0][1]


def test_on_query_search_runs_in_thread_and_applies_latest_only(monkeypatch):
    """B12：有 event loop 時搜尋在執行緒執行；連續搜尋只套用最後一次。"""
    import asyncio
    import threading

    tasks = []

    class LoopPage(FakePage):
        def run_task(self, handler, *args):
            tasks.append(handler)

    view = CacheView.__new__(CacheView)
    view._page = LoopPage()
    view.ui_busy = False
    view.tf_query_input = ft.TextField(value="abc")
    view.dd_query_mode = ft.Dropdown(value="DST")
    view.dd_query_type = ft.Dropdown(value="lang")
    view.query_search_hint = ft.Text(value="")
    view.query_search_hint.update = lambda: None
    view.query_change_hint = ft.Text(value="")
    view._last_overview_data = {"types": {"lang": {}}}
    view.query_results = []
    view.update = lambda: None
    view._notify = lambda *a, **k: None
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None

    threads = []

    def fake_search(cache_type, query, mode, limit):
        threads.append(threading.current_thread())
        return {"items": [{"key": query, "preview": "p"}]}

    monkeypatch.setattr(cache_query_module, "cache_search_service", fake_search)

    CacheView._on_query_search(view, None)
    view.tf_query_input.value = "xyz"
    CacheView._on_query_search(view, None)
    assert threads == []  # 點擊當下不在 event loop 上搜尋
    for t in tasks:
        asyncio.run(t())

    assert all(t is not threading.main_thread() for t in threads)
    assert [r["key"] for r in view.query_results] == ["xyz"]


def test_registry_owned_query_error_is_terminal_failure(monkeypatch):
    import asyncio

    tasks = []

    class LoopPage(FakePage):
        def run_task(self, handler, *args):
            tasks.append(handler)

    registry = OperationRegistry()
    page = LoopPage()
    page.operation_registry = registry
    view = CacheView.__new__(CacheView)
    view._page = page
    view.ui_busy = False
    view.tf_query_input = ft.TextField(value="needle")
    view.dd_query_mode = ft.Dropdown(value="DST")
    view.dd_query_type = ft.Dropdown(value="lang")
    view.query_search_hint = ft.Text(value="")
    view.query_search_hint.update = lambda: None
    view.query_change_hint = ft.Text(value="")
    view._last_overview_data = {"types": {"lang": {}}}
    view.query_results = []
    view.update = lambda: None
    notifications = []
    view._notify = lambda message, level="info": notifications.append((level, message))
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None
    failures = []
    registry.subscribe(
        lambda event, handle: failures.append(handle) if event == "finish" else None
    )
    failure = RuntimeError("search backend failed")
    monkeypatch.setattr(
        view,
        "_compute_query_results",
        lambda *_args: (_ for _ in ()).throw(failure),
    )

    CacheView._on_query_search(view, None)
    assert registry.wait_for_idle(timeout=2)
    asyncio.run(tasks[0]())

    assert failures[0].terminal_reason == "failed"
    assert failures[0].error is failure
    assert notifications == [("error", f"搜尋失敗：{failure}")]
