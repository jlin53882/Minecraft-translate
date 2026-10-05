"""AppShell 的 Flet 1.0 UI 排程與生命週期（teardown）契約。

背景：
- ``TaskManager`` 與 ``config_store`` 的訂閱者可能在**任何執行緒**被呼叫（TaskSession 的 worker、
  存設定的執行緒）。Flet 1.0 是單執行緒 async UI 模型：Control 的修改與 ``page.update()`` 必須
  在 page 的 event loop 上，也就是透過 ``page.run_task()``。
- AppShell 在 mount 時註冊的全域 observer / subscriber / 背景輪詢，要在 session 結束
  （``page.on_close``）或手動 ``dispose()`` 時全部移除，避免舊 Page 被全域登錄表留住。

測試用的 FakePage 只記錄 ``run_task``，由測試自己決定何時「在 UI 執行緒」執行排程的工作，
所以不需要真的 event loop，也不用 ``sleep()``。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace

import flet as ft
import pytest

from app import view_registry as vr
from app.shell import app_shell
from app.shell.app_shell import AppShell
from app.shell.resume_prompt import ResumePrompt
from app.shell.task_manager import TaskManager
from app.tasks import task_session as task_session_module
from app.tasks.task_session import TaskSession
from translation_tool.core.lm_key_health import STATUS_OK, KeyHealth


def _key(index: int) -> KeyHealth:
    return KeyHealth(
        index=index,
        masked="AIza••••abcd",
        status=STATUS_OK,
        reason=None,
        seconds_remaining=0.0,
        failures=0,
    )


class FakeFuture:
    """concurrent.futures.Future 的最小替身：只記錄 cancel。"""

    def __init__(self) -> None:
        self.cancel_calls = 0

    def cancel(self) -> bool:
        self.cancel_calls += 1
        return True


class FakePage:
    """外殼需要的 Page 介面；``run_task`` 只記錄，``drain()`` 才在呼叫端執行緒執行。"""

    def __init__(self) -> None:
        self.overlay: list = []
        self.controls: list = []
        self.title = ""
        self.theme_mode = ft.ThemeMode.LIGHT
        self.bgcolor = None
        self.theme = None
        self.dark_theme = None
        self.padding = None
        self.width = 1440
        self.window = FakeWindow()
        self.on_keyboard_event = None
        self.on_resize = None
        self.on_close = None
        self.on_disconnect = None
        self.dialogs: list = []
        self.update_threads: list[int] = []
        self.scheduled: list[tuple] = []  # (handler, future)
        self.run_task_calls = 0
        self.fail_run_task = False

    @property
    def updated(self) -> int:
        return len(self.update_threads)

    def update(self, *_a, **_k) -> None:
        self.update_threads.append(threading.get_ident())

    def add(self, *controls) -> None:
        self.controls.extend(controls)

    def run_task(self, handler, *args):
        self.run_task_calls += 1
        if self.fail_run_task:
            raise RuntimeError("run_task unavailable")
        future = FakeFuture()
        self.scheduled.append((handler, future, args))
        return future

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self, *_args) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def drain(self, only=None) -> int:
        """在目前執行緒（扮演 UI 執行緒）依序執行已排程的工作，回傳執行了幾個。"""
        ran = 0
        while self.scheduled:
            handler, _future, args = self.scheduled.pop(0)
            if only is not None and handler.__name__ not in only:
                continue
            asyncio.run(handler(*args))
            ran += 1
        return ran

    def scheduled_names(self) -> list[str]:
        return [handler.__name__ for handler, _f, _a in self.scheduled]


class FakeWindow(SimpleNamespace):
    """最小的桌面視窗事件替身。"""

    def __init__(self) -> None:
        super().__init__(
            width=0,
            height=0,
            min_width=0,
            min_height=0,
            prevent_close=False,
            on_event=None,
        )
        self.destroy_calls = 0

    async def destroy(self) -> None:
        self.destroy_calls += 1


class Env:
    """設定異動訂閱與預設資料來源的替身。重複取消訂閱會丟例外，用來抓 double-remove。"""

    def __init__(self) -> None:
        self.listeners: list = []
        self.snapshot = [_key(0)]
        self.config = {
            "lm_translator": {"models": {"gemini-x": {"enabled": True}}},
            "translator": {"cache_directory": "快取資料"},
        }

    def subscribe(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)  # 第二次呼叫會 ValueError

    def fire_config_saved(self) -> None:
        for callback in list(self.listeners):
            callback()


class FakeTime:
    """可控時鐘 + 記錄 sleep 的替身（取代 monotonic / asyncio.sleep，測試不真的等）。"""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(app_shell, "_monotonic", fake.monotonic, raising=False)
    monkeypatch.setattr(app_shell, "_async_sleep", fake.sleep, raising=False)
    return fake


@pytest.fixture(autouse=True)
def placeholder_views(monkeypatch):
    monkeypatch.setattr(
        vr, "_lazy_import_view", lambda key, page, file_picker: ft.Text(f"view:{key}")
    )


@pytest.fixture
def env():
    return Env()


@pytest.fixture
def shell(env, clock):
    shell = _make_shell(env)
    shell.mount()
    shell.page.scheduled.clear()  # mount 自己排的（Key 輪詢）不是這些測試的重點
    shell.page.update_threads.clear()
    yield shell
    shell.dispose() if hasattr(shell, "dispose") else shell.tasks.detach()


def _make_shell(env: Env, *, flush_before_close=None) -> AppShell:
    return AppShell(
        FakePage(),
        file_picker=SimpleNamespace(),
        key_snapshot=lambda: env.snapshot,
        config_loader=lambda: env.config,
        task_manager=TaskManager(),
        initial_mode="dark",
        mode_saver=lambda _m: None,
        subscribe_config=env.subscribe,
        flush_before_close=flush_before_close,
        find_interrupted_tasks=list,  # 不讀真實的 checkpoint 檔
    )


def _in_thread(fn) -> None:
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()


# -- Medium 1：背景執行緒的 callback 只能「排程」，不能直接動 UI ------------------------


def test_task_events_from_a_worker_thread_only_schedule_ui_work(shell):
    page = shell.page
    main_thread = threading.get_ident()

    sessions: list[
        TaskSession
    ] = []  # session 被回收會被 TaskManager 視為中斷，所以要留著

    def worker():
        session = TaskSession(name="機器翻譯", view_key="lm")
        sessions.append(session)
        session.start()
        session.set_progress(0.5)

    _in_thread(worker)

    # worker 執行緒沒有直接 page.update()，也沒有直接改 Control
    assert page.update_threads == []
    assert shell.topbar.task_pill.visible is False
    assert page.scheduled, "應該透過 page.run_task 安排 UI 更新"

    page.drain()  # 之後才在「UI 執行緒」套用
    assert shell.topbar.task_pill.visible is True
    assert page.update_threads and set(page.update_threads) == {main_thread}


def test_config_saved_from_a_worker_thread_only_schedules_ui_work(shell, env):
    page = shell.page
    main_thread = threading.get_ident()
    env.snapshot = [_key(0), _key(1), _key(2)]

    _in_thread(env.fire_config_saved)

    assert page.update_threads == []  # callback 本身只排程
    assert shell.topbar.api_pill.count_text.value == "1/1 Key"  # UI 還沒被動到
    assert len(page.scheduled) == 1

    page.drain()
    assert shell.topbar.api_pill.count_text.value == "3/3 Key"
    assert set(page.update_threads) == {main_thread}


def test_a_burst_of_task_events_is_coalesced_into_one_ui_task(shell, clock):
    page = shell.page

    sessions: list[TaskSession] = []

    def worker():
        session = TaskSession(name="翻譯", view_key="lm")
        sessions.append(session)
        session.start()
        for i in range(1, 50):  # 高頻 progress 事件
            session.set_progress(i / 50)

    _in_thread(worker)

    assert len(page.scheduled) == 1  # 50 個事件只排一個 UI 工作
    page.drain()
    assert page.updated == 1  # 也只更新一次畫面


def test_events_after_a_refresh_schedule_a_new_throttled_task(shell, clock):
    page = shell.page
    session = TaskSession(name="翻譯", view_key="lm")

    session.start()
    page.drain()
    first_sleeps = len(clock.sleeps)

    session.set_progress(0.3)  # 剛更新完又來事件：要排新的，而且要等節流間隔
    assert len(page.scheduled) == 1
    page.drain()

    assert len(clock.sleeps) == first_sleeps + 1
    assert 0 < clock.sleeps[-1] <= app_shell.REFRESH_INTERVAL_SEC
    assert page.updated == 2


def test_throttle_interval_is_not_waited_when_the_last_refresh_is_old(shell, clock):
    page = shell.page
    session = TaskSession(name="翻譯", view_key="lm")
    session.start()
    page.drain()
    clock.now += 60  # 很久以後才有新事件

    session.set_progress(0.5)
    sleeps_before = len(clock.sleeps)
    page.drain()

    assert len(clock.sleeps) == sleeps_before  # 不必再等


def test_refresh_never_uses_a_raw_timer_thread(shell):
    assert not hasattr(shell, "_refresh_timer")


# -- Medium 2：dispose / teardown ------------------------------------------------------


@pytest.fixture
def mounted(env, clock):
    shell = _make_shell(env)
    shell.mount()
    yield shell
    shell.dispose()  # 冪等；確保全域 observer 不會漏到別的測試


def _observer_registered(manager: TaskManager) -> bool:
    return manager._on_session_event in task_session_module._observers


def test_mount_registers_everything_and_dispose_detaches_the_global_observer(
    mounted, env
):
    assert _observer_registered(mounted.tasks)
    assert len(env.listeners) == 1

    mounted.dispose()

    assert not _observer_registered(mounted.tasks)
    # dispose 之後新建的 TaskSession 不會被舊 shell 的 TaskManager 收到
    TaskSession(name="新任務").start()
    assert mounted.tasks.active() == []


def test_dispose_unsubscribes_config(mounted, env):
    mounted.page.scheduled.clear()
    mounted.dispose()
    assert env.listeners == []

    env.fire_config_saved()
    assert mounted.page.scheduled == []  # 舊 shell 不會再被排程


def test_dispose_unsubscribes_task_manager_subscriber(mounted):
    mounted.page.scheduled.clear()
    mounted.dispose()

    mounted.tasks._emit()  # 直接觸發 TaskManager 事件

    assert mounted.page.scheduled == []


def test_dispose_cancels_the_key_polling_task(mounted):
    polls = [
        future
        for handler, future, _a in mounted.page.scheduled
        if handler.__name__ == "_poll_keys"
    ]
    assert len(polls) == 1 and polls[0].cancel_calls == 0

    mounted.dispose()

    assert polls[0].cancel_calls == 1


def test_dispose_cancels_a_pending_refresh_task(mounted):
    TaskSession(name="翻譯", view_key="lm").start()
    pending = [
        future
        for handler, future, _a in mounted.page.scheduled
        if handler.__name__ != "_poll_keys"
    ]
    assert pending, "應該有排程中的 refresh 工作"

    mounted.dispose()

    assert all(future.cancel_calls == 1 for future in pending)


def test_a_late_scheduled_callback_does_not_touch_the_ui_after_dispose(mounted):
    TaskSession(name="翻譯", view_key="lm").start()
    mounted.page.update_threads.clear()
    handlers = list(mounted.page.scheduled)

    mounted.dispose()
    for handler, _future, args in handlers:  # 晚到的已排程工作（取消沒趕上）
        if handler.__name__ != "_poll_keys":
            asyncio.run(handler(*args))

    assert mounted.page.update_threads == []
    assert mounted.topbar.task_pill.visible is False


def test_dispose_is_idempotent(mounted, env):
    mounted.dispose()
    mounted.dispose()  # 不可例外；env 的 unsubscribe 第二次會 ValueError，抓 double-remove
    assert env.listeners == []


def test_page_on_close_runs_the_teardown(mounted, env):
    assert callable(mounted.page.on_close)

    asyncio.run(mounted.page.on_close(None))

    assert not _observer_registered(mounted.tasks)
    assert env.listeners == []


def test_page_on_close_preserves_an_existing_handler(env, clock):
    seen: list[object] = []
    shell = _make_shell(env)
    shell.page.on_close = seen.append  # 別人先掛的 on_close

    shell.mount()
    asyncio.run(shell.page.on_close("event"))

    assert seen == ["event"]  # 原本的 handler 仍被呼叫
    assert not _observer_registered(shell.tasks)


def test_page_on_close_awaits_an_async_existing_handler_without_ui_scheduler(
    env, clock
):
    seen: list[object] = []

    async def previous(event) -> None:
        seen.append(event)

    shell = _make_shell(env)
    shell.page.on_close = previous
    shell.mount()
    shell.page.scheduled.clear()
    calls_before_close = shell.page.run_task_calls
    shell.page.fail_run_task = True

    asyncio.run(shell.page.on_close("event"))

    assert seen == ["event"]
    assert shell.page.run_task_calls == calls_before_close
    shell.dispose()


def test_page_on_close_awaits_sync_handler_returning_awaitable(env, clock):
    seen: list[object] = []

    def previous(event):
        async def inner():
            seen.append(event)

        return inner()

    shell = _make_shell(env)
    shell.page.on_close = previous
    shell.mount()
    asyncio.run(shell.page.on_close("event"))

    assert seen == ["event"]
    shell.dispose()


def test_page_on_close_prior_handler_failure_does_not_break_dispose(env, clock, caplog):
    async def previous(_event):
        raise RuntimeError("prior close failed")

    shell = _make_shell(env)
    shell.page.on_close = previous
    shell.mount()

    with caplog.at_level(logging.DEBUG, logger="main_app"):
        asyncio.run(shell.page.on_close("event"))

    assert not _observer_registered(shell.tasks)
    assert any(
        "既有 async page handler 失敗" in record.message for record in caplog.records
    )
    shell.dispose()


def test_mount_wires_desktop_window_close_to_idempotent_teardown(env, clock):
    shell = _make_shell(env)
    shell.mount()
    shell.page.scheduled.clear()

    assert shell.page.window.prevent_close is True
    assert callable(shell.page.window.on_event)
    window_handler = shell.page.window.on_event

    event = SimpleNamespace(type=ft.WindowEventType.CLOSE)
    asyncio.run(window_handler(event))

    assert shell.page.window.destroy_calls == 1
    assert not _observer_registered(shell.tasks)
    assert env.listeners == []

    # A second close event must not repeat teardown or destroy.
    asyncio.run(window_handler(event))
    assert shell.page.window.destroy_calls == 1


def test_desktop_close_with_active_task_requires_confirmation(env, clock):
    shell = _make_shell(env)
    shell.mount()
    shell.page.scheduled.clear()
    session = TaskSession(name="長任務")
    session.start()
    shell.page.scheduled.clear()

    window_handler = shell.page.window.on_event
    event = SimpleNamespace(type=ft.WindowEventType.CLOSE)
    asyncio.run(window_handler(event))

    assert shell.page.window.destroy_calls == 0
    assert len(shell.page.dialogs) == 1
    dialog = shell.page.dialogs[0]
    assert [action.content for action in dialog.actions] == ["繼續執行", "仍要關閉"]

    dialog.actions[1].on_click(None)
    assert session.cancel_requested is True
    session.finish()
    shell.page.drain()
    assert shell.page.window.destroy_calls == 1


def test_desktop_close_zero_active_stops_accepting_before_async_flush(env, clock):
    late_sessions: list[TaskSession] = []

    async def flush_before_close():
        late = TaskSession(name="flush 中啟動的晚到任務")
        late_sessions.append(late)
        late.start()
        assert shell.tasks.active() == []
        await asyncio.sleep(0)
        assert shell.tasks.active() == []
        return True

    shell = _make_shell(env, flush_before_close=flush_before_close)
    shell.mount()
    shell.page.scheduled.clear()

    asyncio.run(
        shell.page.window.on_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
    )

    assert len(late_sessions) == 1
    assert shell.page.window.destroy_calls == 1
    assert shell.tasks.active() == []
    late_sessions[0].finish()


def test_desktop_close_rejection_keeps_task_running(env, clock):
    shell = _make_shell(env)
    shell.mount()
    shell.page.scheduled.clear()
    session = TaskSession(name="長任務")
    session.start()

    asyncio.run(
        shell.page.window.on_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
    )
    dialog = shell.page.dialogs[0]
    dialog.actions[0].on_click(None)

    assert session.cancel_requested is False
    assert shell.page.window.destroy_calls == 0
    assert shell.page.dialogs == []
    session.finish()
    shell.dispose()


def test_desktop_close_keeps_window_when_final_flush_fails(env, clock):
    shell = _make_shell(env, flush_before_close=lambda: False)
    shell.mount()
    shell.page.scheduled.clear()

    asyncio.run(
        shell.page.window.on_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
    )

    assert shell.page.window.destroy_calls == 0
    assert shell._disposed is False
    assert shell.page.dialogs
    assert shell.page.dialogs[-1].title.value == "無法安全關閉"
    session = TaskSession(name="flush failure 後的任務")
    session.start()
    assert any(task.id == id(session) for task in shell.tasks.active())
    session.finish()
    shell.dispose()


def test_desktop_close_keeps_window_when_final_flush_raises(env, clock):
    def flush_before_close():
        raise RuntimeError("flush failed")

    shell = _make_shell(env, flush_before_close=flush_before_close)
    shell.mount()
    shell.page.scheduled.clear()

    asyncio.run(
        shell.page.window.on_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
    )

    assert shell.page.window.destroy_calls == 0
    assert shell._disposed is False
    session = TaskSession(name="flush raise 後的任務")
    session.start()
    assert any(task.id == id(session) for task in shell.tasks.active())
    session.finish()
    shell.dispose()


def test_desktop_close_drain_timeout_restores_task_acceptance(env, clock):
    shell = _make_shell(env)
    shell.mount()
    shell.page.scheduled.clear()
    first = TaskSession(name="逾時任務")
    first.start()

    asyncio.run(
        shell.page.window.on_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
    )
    shell.page.dialogs[0].actions[1].on_click(None)
    shell.page.drain()

    assert shell.page.window.destroy_calls == 0
    second = TaskSession(name="逾時後的新任務")
    second.start()
    assert any(task.id == id(second) for task in shell.tasks.active())
    second.finish()
    assert all(task.id != id(second) for task in shell.tasks.active())
    first.finish()
    shell.dispose()


def test_desktop_close_schedule_failure_restores_task_acceptance(env, clock):
    shell = _make_shell(env)
    shell.mount()
    shell.page.scheduled.clear()
    first = TaskSession(name="排程失敗任務")
    first.start()
    shell.page.fail_run_task = True

    asyncio.run(
        shell.page.window.on_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
    )
    shell.page.dialogs[0].actions[1].on_click(None)

    assert shell.page.window.destroy_calls == 0
    second = TaskSession(name="排程失敗後的新任務")
    second.start()
    assert any(task.id == id(second) for task in shell.tasks.active())
    second.finish()
    assert all(task.id != id(second) for task in shell.tasks.active())
    first.finish()
    shell.dispose()


def test_disconnect_does_not_tear_down_so_web_reconnect_keeps_working(mounted, env):
    """web client 可能只是暫時斷線再重連：不可把永久 teardown 綁在 on_disconnect。"""
    assert mounted.page.on_disconnect is None
    assert _observer_registered(mounted.tasks)


# -- #151：啟動時偵測上次被中斷的機器翻譯 ---------------------------------------------


def _interrupted_task(kind: str = "lm_directory"):
    return SimpleNamespace(
        kind=kind,
        label=kind,
        input_dir="C:/assets",
        output_dir="C:/out",
        export_lang=False,
        write_new_cache=True,
        options={},
        completed=3,
        total=9,
        updated_at="",
    )


def _buttons(control, label: str) -> list:
    """遞迴找出對話框內容樹中指定文字的 TextButton。"""
    found = []
    if isinstance(control, ft.TextButton) and control.content == label:
        found.append(control)
    for child in getattr(control, "controls", None) or []:
        found.extend(_buttons(child, label))
    inner = getattr(control, "content", None)
    if inner is not None and not isinstance(inner, str):
        found.extend(_buttons(inner, label))
    for action in getattr(control, "actions", None) or []:
        found.extend(_buttons(action, label))
    return found


def _shell_with_interrupted_tasks(env: Env, tasks) -> AppShell:
    shell = _make_shell(env)
    shell._resume_prompt = ResumePrompt(
        shell.page,
        find_tasks=lambda: tasks,
        check_resume=lambda _t: SimpleNamespace(ok=True, reason=""),
        discard=lambda _t: None,
        on_resume=shell._resume_interrupted_task,
        run_background=lambda work, done: done(work()),
    )
    return shell


def test_mount_prompts_for_an_interrupted_task_without_starting_anything(env):
    shell = _shell_with_interrupted_tasks(env, [_interrupted_task()])

    shell.mount()

    assert len(shell.page.dialogs) == 1, "啟動時要詢問使用者"
    assert shell.tasks.active() == [], "使用者確認前不得啟動任何任務"
    shell.dispose()


def test_mount_without_an_interrupted_task_shows_no_dialog(env):
    shell = _make_shell(env)

    shell.mount()

    assert shell.page.dialogs == []
    shell.dispose()


def test_a_failing_resume_prompt_never_breaks_mount(env, caplog):
    shell = _make_shell(env)

    class Broken:
        def show_if_needed(self):
            raise RuntimeError("prompt exploded")

    shell._resume_prompt = Broken()

    shell.mount()  # 不得拋出例外

    assert shell.page.controls, "外殼仍然完成掛載"
    shell.dispose()


def _install_fake_view(shell: AppShell, key: str, received: list):
    class FakeView(ft.Column):
        def resume_interrupted(self, t):
            received.append(t)

    item = next(i for i in shell.registry if i["key"] == key)
    # 真實的頁面被 wrap_view 包在容器裡：外殼要能穿過容器找到頁面
    dict.__setitem__(item, "view", ft.Container(content=FakeView()))


def test_confirming_resume_opens_the_lm_page_and_hands_over_the_task(env):
    task = _interrupted_task("lm_directory")
    shell = _shell_with_interrupted_tasks(env, [task])
    received: list = []
    _install_fake_view(shell, "lm", received)
    shell.mount()

    (resume_button,) = _buttons(shell.page.dialogs[-1], "續跑")
    assert resume_button.disabled is False
    resume_button.on_click(None)

    assert received == [task]
    assert shell.current_key == "lm"
    shell.dispose()


@pytest.mark.parametrize("kind", ["ftbquests", "kubejs", "md"])
def test_plugin_tasks_resume_in_the_translation_page(env, kind):
    task = _interrupted_task(kind)
    shell = _shell_with_interrupted_tasks(env, [task])
    received: list = []
    _install_fake_view(shell, "translation", received)
    shell.mount()

    (resume_button,) = _buttons(shell.page.dialogs[-1], "續跑")
    resume_button.on_click(None)

    assert received == [task]
    assert shell.current_key == "translation"
    shell.dispose()


def test_several_interrupted_tasks_share_one_dialog_and_resume_only_the_chosen_one(
    env,
):
    lm, md = _interrupted_task("lm_directory"), _interrupted_task("md")
    shell = _shell_with_interrupted_tasks(env, [lm, md])
    lm_received: list = []
    translation_received: list = []
    _install_fake_view(shell, "lm", lm_received)
    _install_fake_view(shell, "translation", translation_received)
    shell.mount()

    assert len(shell.page.dialogs) == 1, "多個任務共用一個對話框"
    resume_buttons = _buttons(shell.page.dialogs[-1], "續跑")
    assert len(resume_buttons) == 2
    resume_buttons[1].on_click(None)  # 只續跑 MD

    assert translation_received == [md]
    assert lm_received == [], "沒有被選擇的任務不得啟動"
    assert shell.current_key == "translation"
    shell.dispose()
