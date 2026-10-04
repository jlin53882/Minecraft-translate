"""#114 契約的行為測試：poller 的 owner／teardown，以及 event loop 上不得做阻塞工作。

重點不是「任務最後會自己結束」，而是：
- View 卸載（will_unmount）後輪詢**立刻**停止，不再碰（已卸載的）控制項；
- teardown 可重複呼叫（idempotent）；
- 重新掛載（did_mount）後只存在**一個**新輪詢，不累積；
- 任務在卸載期間結束時，重新掛載會補上最終狀態；
- 阻塞工作（glob／讀快取／走訪 JAR）在背景執行緒，不在 event loop 的執行緒。
"""

from __future__ import annotations

import asyncio
import functools
import threading
import types

import pytest

from app.tasks import LogEntry
from app.ui.poller import PollerHandle
from tests.conftest import mock_filepicker, mock_page

# ---------------------------------------------------------------------------
# 共用工具
# ---------------------------------------------------------------------------


class _Session:
    """最小 TaskSession 替身（可控狀態）。"""

    def __init__(self, status="RUNNING", **kwargs):
        self.status = status
        self.progress = 0.0
        self.logs: list[str] = []
        self.error = False
        self.cancel_requested = False

    def start(self):
        self.status = "RUNNING"

    def add_log(self, text, level="info", source="ui"):
        self.logs.append(text)

    def set_error(self):
        self.error = True
        self.status = "ERROR"

    def finish(self):
        self.progress = 1.0
        self.status = "DONE"

    def request_cancel(self):
        self.cancel_requested = True

    def snapshot(self):
        return {
            "status": self.status,
            "progress": self.progress,
            "logs": [
                LogEntry(seq=i, level="info", text=t, source="ui")
                for i, t in enumerate(self.logs)
            ],
        }


class _FakeFuture:
    def __init__(self):
        self.cancelled = 0

    def cancel(self):
        self.cancelled += 1
        return True


class _RecordingPage:
    """記錄 run_task 並回傳可檢查的 Future 的 page（不真的執行）。"""

    def __init__(self):
        self.tasks: list = []
        self.futures: list[_FakeFuture] = []

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))
        fut = _FakeFuture()
        self.futures.append(fut)
        return fut


def _pending(page):
    """mock_page 的 _tasks：回傳尚未執行的 (coroutine function, args)。"""
    return list(page._tasks)


async def _run_poller_until(page, view, *, after_start, ticks=3):
    """在真實 event loop 上跑 page 排入的最後一個 poller，讓它跑幾輪後執行 ``after_start``。"""
    handler, args = page._tasks[-1]
    task = asyncio.ensure_future(handler(*args))
    for _ in range(ticks):
        await asyncio.sleep(0.05)
    after_start()
    await asyncio.wait_for(task, timeout=3)


# ---------------------------------------------------------------------------
# PollerHandle 本身
# ---------------------------------------------------------------------------


def test_poller_handle_start_stop_is_idempotent_and_clears_state():
    page = _RecordingPage()
    handle = PollerHandle()
    assert handle.running is False

    async def poll(alive):
        while alive():
            await asyncio.sleep(0.01)

    assert handle.start(page, poll) is True
    assert handle.running is True
    handle.stop()
    handle.stop()  # 重複 teardown 不丟例外
    assert handle.running is False
    assert page.futures[0].cancelled == 1  # 只取消一次（第二次 stop 時 Future 已清空）


def test_poller_handle_restart_replaces_the_previous_poller():
    page = _RecordingPage()
    handle = PollerHandle()
    alive_flags = []

    async def poll(alive):
        alive_flags.append(alive)

    handle.start(page, poll)
    first_runner, _ = page.tasks[0]
    handle.start(page, poll)
    assert page.futures[0].cancelled == 1  # 舊的被取消
    assert handle.running is True

    async def run_both():
        await first_runner()  # 舊 runner 就算還是跑到了，它的 alive() 也已是 False
        await page.tasks[1][0]()

    asyncio.run(run_both())
    assert alive_flags[0]() is False
    # 舊 runner 結束不能把「新輪詢」標成已結束
    assert alive_flags[1]() is False  # 新的也已正常結束 → running 清空
    assert handle.running is False


def test_poller_handle_survives_page_without_loop():
    class _BadPage:
        def run_task(self, *a):
            raise RuntimeError("page closed")

    handle = PollerHandle()

    async def poll(alive):
        pass

    assert handle.start(_BadPage(), poll) is False
    assert handle.running is False  # 不留下半啟動狀態


# ---------------------------------------------------------------------------
# Translation view
# ---------------------------------------------------------------------------


def _translation_view():
    from app.views import translation_view as tv

    page = mock_page()
    view = tv.TranslationView(page, mock_filepicker())
    view.session = _Session()
    return page, view


def test_translation_unmount_stops_poller_and_no_more_ui_updates(monkeypatch):
    from app.views.translation import translation_actions as ta

    page, view = _translation_view()
    syncs: list[int] = []
    monkeypatch.setattr(ta, "_sync_from_session", lambda v: syncs.append(1))

    view._start_ui_timer()
    assert view._poller.running is True

    async def scenario():
        await _run_poller_until(page, view, after_start=view.will_unmount, ticks=2)

    asyncio.run(scenario())
    stopped_at = len(syncs)
    assert stopped_at >= 1  # 卸載前確實有輪詢
    assert view._poller.running is False
    # 卸載後 session 再更新，也不會有任何同步
    view.session.progress = 0.9
    assert len(syncs) == stopped_at
    view.will_unmount()  # 重複 teardown
    view.will_unmount()


def test_translation_remount_has_exactly_one_poller_and_old_one_is_dead(monkeypatch):
    from app.views.translation import translation_actions as ta

    page, view = _translation_view()
    monkeypatch.setattr(ta, "_sync_from_session", lambda v: None)
    view._start_ui_timer()
    assert len(page._tasks) == 1
    first_alive = []

    # 偷看第一個 poller 的 alive：重新包裝 handler 會改變行為，所以改由 handle 判斷世代
    view.will_unmount()
    view.did_mount()  # 任務仍在追蹤 → 重新啟動輪詢
    view.did_mount()  # 重複 mount 事件也不會累積（舊的先被停止）
    assert view._poller.running is True
    assert len(page._tasks) == 3  # 每次 start 都排新的，但舊的立即作廢
    # 只有最後一個仍是 active generation
    assert view._poller._active_generation == view._poller._generation
    del first_alive


def test_translation_session_finished_while_unmounted_is_applied_on_remount():
    page, view = _translation_view()
    view._start_ui_timer()
    view.will_unmount()
    view.session.finish()  # 卸載期間任務結束

    view.did_mount()
    handler, args = page._tasks[-1]
    asyncio.run(asyncio.wait_for(handler(*args), timeout=3))

    assert view._ui_timer_running is False
    assert view.status_chip.label.value == "任務完成"
    assert view.cancel_button.disabled is True


def test_translation_unmount_without_task_is_harmless():
    page, view = _translation_view()
    view.session = None
    view.will_unmount()
    view.did_mount()
    assert page._tasks == []  # 沒有追蹤中的任務就不啟動輪詢


# ---------------------------------------------------------------------------
# LM view
# ---------------------------------------------------------------------------


def _lm_view(monkeypatch):
    from app.views import lm_view

    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())
    view.session = _Session()
    return page, view


def test_lm_unmount_stops_poller_and_no_more_ui_updates(monkeypatch):
    page, view = _lm_view(monkeypatch)
    syncs: list[int] = []
    monkeypatch.setattr(view, "_sync_from_session", lambda: syncs.append(1))

    view.start_ui_timer()
    view.start_ui_timer()  # 重複啟動不累積
    assert len(page._tasks) == 1

    async def scenario():
        await _run_poller_until(page, view, after_start=view.will_unmount, ticks=2)

    asyncio.run(scenario())
    stopped_at = len(syncs)
    assert stopped_at >= 1
    assert view._poller.running is False
    view.session.progress = 0.5
    assert len(syncs) == stopped_at
    view.will_unmount()  # idempotent


def test_lm_remount_resumes_single_poller_and_applies_final_state(monkeypatch):
    page, view = _lm_view(monkeypatch)
    view._set_running(True)
    view.start_ui_timer()
    view.will_unmount()
    view.session.finish()

    view.did_mount()
    assert view._poller.running is True
    handler, args = page._tasks[-1]
    asyncio.run(asyncio.wait_for(handler(*args), timeout=3))

    assert view._ui_timer_running is False
    assert view._poller.running is False
    assert view.start_button.disabled is False
    assert view.cancel_button.disabled is True


# ---------------------------------------------------------------------------
# Merge view
# ---------------------------------------------------------------------------


def _merge_view(monkeypatch):
    from app.views import merge_view
    from app.views.merge import merge_widgets

    monkeypatch.setattr(merge_view, "TaskSession", _Session)
    monkeypatch.setattr(merge_widgets, "TaskSession", _Session)
    monkeypatch.setattr(merge_view, "load_config", lambda: {"lang_merger": {}})
    monkeypatch.setattr(merge_widgets, "load_config", lambda: {"lang_merger": {}})
    page = mock_page()
    view = merge_view.MergeView(page, mock_filepicker())
    return page, view, merge_view


def test_merge_polling_is_an_event_loop_task_not_a_sleeping_thread(monkeypatch):
    page, view, _ = _merge_view(monkeypatch)
    before = threading.active_count()
    view._start_ui_poller()
    assert len(page._tasks) == 1
    assert threading.active_count() == before  # 不再另開 time.sleep 的 poll 執行緒


def test_merge_unmount_stops_poller_and_no_more_ui_updates(monkeypatch):
    page, view, _ = _merge_view(monkeypatch)
    syncs: list[int] = []
    monkeypatch.setattr(view, "_sync_ui_once", lambda: syncs.append(1))

    view._start_ui_poller()

    async def scenario():
        await _run_poller_until(page, view, after_start=view.will_unmount, ticks=2)

    asyncio.run(scenario())
    stopped_at = len(syncs)
    assert stopped_at >= 1
    assert view._poller.running is False
    assert len(syncs) == stopped_at
    view.will_unmount()
    view.will_unmount()


def test_merge_remount_resumes_only_while_merge_is_tracked(monkeypatch):
    page, view, _ = _merge_view(monkeypatch)
    view.did_mount()
    assert page._tasks == []  # 沒有合併進行中：不啟動輪詢

    view._start_ui_poller()
    view.will_unmount()
    view.did_mount()
    view.did_mount()
    assert view._poller.running is True
    assert view._poller._active_generation == view._poller._generation


def test_merge_result_finished_while_unmounted_shows_summary_once_on_remount(
    monkeypatch,
):
    page, view, _ = _merge_view(monkeypatch)
    shown = []
    monkeypatch.setattr(view, "_show_merge_summary", lambda s: shown.append(s))
    view._start_ui_poller()
    view.will_unmount()
    view.session.finish()

    view.did_mount()
    handler, args = page._tasks[-1]
    asyncio.run(asyncio.wait_for(handler(*args), timeout=3))
    view.did_mount()  # 結束後再 mount：不會重複顯示

    assert len(shown) == 1
    assert view._ui_stop.is_set()
    assert view._poller.running is False


def test_merge_worker_exception_ends_the_session_instead_of_hanging(monkeypatch):
    """背景執行緒丟例外時，session 必須轉成 ERROR；否則輪詢永遠等不到結束。"""
    _page, view, merge_view = _merge_view(monkeypatch)
    view.folder_path_field.value = "/in"
    view.output_dir_field.value = "/out"
    view.input_mode_group.value = "folder"

    def boom(**kwargs):
        raise RuntimeError("service exploded")
        yield  # pragma: no cover

    monkeypatch.setattr(merge_view, "run_merge_folder_batch_service", boom)
    threads = []

    class _SyncThread:
        def __init__(self, target=None, daemon=None, **kw):
            self.target = target
            threads.append(self)

        def start(self):
            self.target()

    monkeypatch.setattr(merge_view.threading, "Thread", _SyncThread)
    view.start_merge(None)

    assert view.session.status == "ERROR"
    assert any("service exploded" in t for t in view.session.logs)


# ---------------------------------------------------------------------------
# icon preview：阻塞工作在背景執行緒；卸載後丟棄結果
# ---------------------------------------------------------------------------


def _icon_view(tmp_path):
    from app.views import icon_preview_view as ipv

    page = mock_page()
    view = ipv.IconPreviewView(page)
    view.update = lambda *a: None
    view.source_root = tmp_path
    view.review_root = tmp_path
    return page, view


def test_icon_load_runs_all_blocking_steps_off_the_event_loop_thread(
    tmp_path, monkeypatch
):
    page, view = _icon_view(tmp_path)
    loop_thread = {}
    seen: dict[str, int] = {}

    def record(name, value):
        def inner(*a, **k):
            seen[name] = threading.get_ident()
            return value

        return inner

    monkeypatch.setattr(view, "_detect_source_mode", record("detect", "jar_directory"))
    monkeypatch.setattr(view, "_lookup_cached_entries", record("cache", None))
    monkeypatch.setattr(view, "_count_scan_steps", record("count", 3))
    monkeypatch.setattr(view, "_scan_entries", record("scan", []))
    monkeypatch.setattr(view, "_finish_load", lambda entries, mode: None)

    view._on_load_clicked(None)
    assert seen == {}  # 點擊當下（sync handler）不做任何阻塞工作

    async def scenario():
        loop_thread["id"] = threading.get_ident()
        handler, args = page._tasks[0]
        await handler(*args)

    asyncio.run(scenario())
    assert set(seen) == {"detect", "cache", "count", "scan"}
    assert all(ident != loop_thread["id"] for ident in seen.values())


def test_icon_unmount_during_scan_discards_the_result(tmp_path, monkeypatch):
    page, view = _icon_view(tmp_path)
    release = threading.Event()
    finished = []
    monkeypatch.setattr(view, "_detect_source_mode", lambda: "extracted_folder")
    monkeypatch.setattr(view, "_lookup_cached_entries", lambda mode: None)
    monkeypatch.setattr(view, "_count_scan_steps", lambda mode: 0)

    def slow_scan(mode, total):
        release.wait(timeout=5)
        return ["entry"]

    monkeypatch.setattr(view, "_scan_entries", slow_scan)
    monkeypatch.setattr(view, "_finish_load", lambda e, m: finished.append(e))

    view._on_load_clicked(None)

    async def scenario():
        handler, args = page._tasks[0]
        task = asyncio.ensure_future(handler(*args))
        await asyncio.sleep(0.1)
        view.will_unmount()  # 掃描進行中卸載
        release.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())
    assert finished == []  # 結果被丟棄，不再套用到（已卸載的）UI
    assert view._loading is False  # 旗標復位：重新掛載後可以再載入
    view.will_unmount()  # idempotent


def test_icon_cache_hit_is_applied_without_scanning(tmp_path, monkeypatch):
    page, view = _icon_view(tmp_path)
    applied = []
    monkeypatch.setattr(view, "_detect_source_mode", lambda: "jar_directory")
    monkeypatch.setattr(
        view, "_lookup_cached_entries", lambda mode: ("L2", [{"modid": "m"}])
    )
    monkeypatch.setattr(
        view, "_apply_cached_entries", lambda kind, entries, mode: applied.append(kind)
    )
    monkeypatch.setattr(
        view, "_scan_entries", lambda *a: pytest.fail("快取命中不應該掃描")
    )
    view._on_load_clicked(None)
    handler, args = page._tasks[0]
    asyncio.run(handler(*args))
    assert applied == ["L2"]
    assert view._loading is False


# ---------------------------------------------------------------------------
# open_output_folder：不等待外部程式
# ---------------------------------------------------------------------------


def test_open_output_folder_does_not_wait_for_the_external_process(
    tmp_path, monkeypatch
):
    from app.services_impl.pipelines import extract_service as es

    calls = {}

    class _Popen:
        def __init__(self, cmd, **kwargs):
            calls["cmd"] = cmd
            calls["kwargs"] = kwargs

        def wait(self, *a, **k):
            pytest.fail("不得等待外部程式結束（會凍結 event loop）")

    monkeypatch.setattr(es.os, "name", "posix")
    monkeypatch.setattr(es.subprocess, "Popen", _Popen)
    monkeypatch.setattr(
        es.subprocess, "run", lambda *a, **k: pytest.fail("不得用 subprocess.run")
    )
    assert es.open_output_folder(str(tmp_path)) is True
    assert calls["cmd"][-1] == str(tmp_path)
    assert calls["kwargs"]["start_new_session"] is True


def test_open_output_folder_reports_failures_without_raising(tmp_path, monkeypatch):
    from app.services_impl.pipelines import extract_service as es

    assert es.open_output_folder("") is False
    assert es.open_output_folder(str(tmp_path / "missing")) is False

    def no_opener(*a, **k):
        raise FileNotFoundError("xdg-open")

    monkeypatch.setattr(es.os, "name", "posix")
    monkeypatch.setattr(es.subprocess, "Popen", no_opener)
    assert (
        es.open_output_folder(str(tmp_path)) is False
    )  # 錯誤只記錄，不進入 Flet handler


def test_every_open_folder_handler_goes_through_the_non_blocking_service():
    """所有 UI handler 都走 open_output_folder；不得再有直接 os.startfile／subprocess。"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "extract_service.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"startfile"}:
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
            if (
                isinstance(node, ast.Attribute)
                and node.attr in {"run", "Popen", "call", "check_call", "check_output"}
                and isinstance(node.value, ast.Name)
                and node.value.id == "subprocess"
            ):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == [], "UI 層不得直接開外部程式／阻塞：\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# 其他長生命週期工作的 owner（盤點結果的回歸保護）
# ---------------------------------------------------------------------------


def test_views_with_debounced_work_cancel_it_on_unmount():
    from app.views import cache_view, rules_view
    from app.views.icon_preview_view import IconPreviewView

    for cls, attrs in (
        (cache_view.CacheView, ["_update_debouncer"]),
        (rules_view.RulesView, ["_search_debouncer"]),
        (IconPreviewView, ["_mod_search_debouncer", "_detail_search_debouncer"]),
    ):
        view = cls.__new__(cls)
        cancelled: list[str] = []
        for attr in attrs:
            setattr(
                view,
                attr,
                types.SimpleNamespace(cancel=functools.partial(cancelled.append, attr)),
            )
        if cls is IconPreviewView:
            view._load_generation = 0
            view._render_generation = 0
        cls.will_unmount(view)
        assert sorted(cancelled) == sorted(attrs)


# ---------------------------------------------------------------------------
# icon preview 詳情頁：圖示準備、zh_tw.json 搜尋／讀取／寫入都不在 event loop
# ---------------------------------------------------------------------------


def _entries(n):
    return [types.SimpleNamespace(key=f"k{i}", en=f"en{i}", zh_tw="") for i in range(n)]


def test_row_built_with_prepared_icon_does_no_io(monkeypatch, tmp_path):
    from app.views import icon_preview_row as rows

    def forbidden(*a, **k):
        pytest.fail("建構列時不得做圖示 I/O（應在背景執行緒的 prepare_row_icon 完成）")

    monkeypatch.setattr(rows, "resolve_icon_with_reason", forbidden)
    monkeypatch.setattr(rows, "_resolve_preview_path", forbidden)
    monkeypatch.setattr(rows, "_ensure_icon_size", forbidden)
    row = rows.LangItemRow(
        lang_key="k",
        en_text="e",
        zh_text="z",
        assets_root=tmp_path,
        preview_root=tmp_path,
        on_value_changed=lambda k, v: None,
        prepared_icon=(
            types.SimpleNamespace(reason="", risk=None, icon_path=None),
            None,
        ),
    )
    assert row.content is not None


def test_detail_page_icons_are_prepared_off_the_event_loop_thread(
    tmp_path, monkeypatch
):
    page, view = _icon_view(tmp_path)
    view.current_modid = "demo"
    view.mods = {"demo": _entries(3)}
    view._zh_data = {}
    seen = {}

    def fake_prepare(entries, context):
        seen["thread"] = threading.get_ident()
        return [
            (types.SimpleNamespace(reason="", risk=None, icon_path=None), None)
        ] * len(entries)

    monkeypatch.setattr(view, "_prepare_row_icons", fake_prepare)
    view._render_current_page()
    assert "thread" not in seen  # sync handler：沒有同步準備
    assert view.list_view.controls == []

    async def scenario():
        seen["loop"] = threading.get_ident()
        handler, args = page._tasks[-1]
        await handler(*args)

    asyncio.run(scenario())
    assert seen["thread"] != seen["loop"]
    assert len(view.list_view.controls) == 3


def test_only_the_latest_detail_render_is_applied_and_unmount_discards(
    tmp_path, monkeypatch
):
    page, view = _icon_view(tmp_path)
    view.current_modid = "demo"
    view.mods = {"demo": _entries(2)}
    view._zh_data = {}
    prepared = lambda entries, ctx: (
        [(types.SimpleNamespace(reason="", risk=None, icon_path=None), None)]
        * len(entries)
    )
    monkeypatch.setattr(view, "_prepare_row_icons", prepared)

    view._render_current_page()  # 第一次渲染
    view.mods = {"demo": _entries(3)}
    view._render_current_page()  # 第二次渲染（使用者很快翻頁）

    async def run_all():
        for handler, args in list(page._tasks):
            await handler(*args)

    asyncio.run(run_all())
    assert len(view.list_view.controls) == 3  # 只有最後一次生效（沒有被舊結果覆蓋）

    # 卸載時進行中的渲染被丟棄
    page._tasks.clear()
    view.list_view.controls.clear()
    view._render_current_page()
    view.will_unmount()
    asyncio.run(run_all())
    assert view.list_view.controls == []


def test_open_mod_detail_finds_zh_file_in_a_thread_and_discards_when_stale(
    tmp_path, monkeypatch
):
    page, view = _icon_view(tmp_path)
    view.mods = {"demo": _entries(1)}
    seen = {}
    zh = tmp_path / "demo" / "lang" / "zh_tw.json"

    def fake_find(modid):
        seen["thread"] = threading.get_ident()
        return zh, {"k0": "譯"}

    monkeypatch.setattr(view, "_find_zh_file", fake_find)
    monkeypatch.setattr(
        view,
        "_prepare_row_icons",
        lambda e, c: (
            [(types.SimpleNamespace(reason="", risk=None, icon_path=None), None)]
            * len(e)
        ),
    )
    view._open_mod_detail("demo")
    assert "thread" not in seen  # 點擊當下不做任何檔案搜尋／讀取

    async def run_all():
        seen["loop"] = threading.get_ident()
        for handler, args in list(page._tasks):
            await handler(*args)

    asyncio.run(run_all())
    assert seen["thread"] != seen["loop"]
    assert view._zh_data == {"k0": "譯"} and view._current_zh_file == zh

    # 還沒載入完就返回：結果丟棄
    page._tasks.clear()
    view._zh_data = {}
    view._open_mod_detail("demo")
    view.current_modid = None  # 使用者按返回
    asyncio.run(run_all())
    assert view._zh_data == {}


def test_find_zh_file_uses_direct_path_then_rglob_fallback(tmp_path):
    _page, view = _icon_view(tmp_path)
    direct = tmp_path / "a" / "lang"
    direct.mkdir(parents=True)
    (direct / "zh_tw.json").write_text('{"x": "1"}', encoding="utf-8")
    nested = tmp_path / "pack" / "b" / "lang"
    nested.mkdir(parents=True)
    (nested / "zh_tw.json").write_text('{"y": "2"}', encoding="utf-8")

    assert view._find_zh_file("a")[1] == {"x": "1"}
    path, data = view._find_zh_file("b")  # 直接路徑不存在 → rglob fallback
    assert data == {"y": "2"} and path == nested / "zh_tw.json"
    assert view._find_zh_file("missing") == (None, {})


def test_save_current_zh_writes_in_a_thread(tmp_path, monkeypatch):
    page, view = _icon_view(tmp_path)
    target = tmp_path / "zh_tw.json"
    view._current_zh_file = target
    view._zh_data = {"k": "譯"}
    seen = {}
    real = view._write_zh_file

    def spy(path, payload):
        seen["thread"] = threading.get_ident()
        return real(path, payload)

    monkeypatch.setattr(view, "_write_zh_file", spy)
    view._save_current_zh(None)
    assert not target.exists() and "thread" not in seen

    async def scenario():
        seen["loop"] = threading.get_ident()
        handler, args = page._tasks[-1]
        await handler(*args)

    asyncio.run(scenario())
    assert seen["thread"] != seen["loop"]
    assert "譯" in target.read_text(encoding="utf-8")
