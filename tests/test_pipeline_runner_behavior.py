"""PipelineRunner／PipelineActions／PipelineView 的行為與生命週期測試（#114）。

只透過穩定的注入縫（``PipelineServices``、``actions=``、``session_factory=``、
``launch_worker=``）驗證，不 patch module 名稱、也不替換 ``threading.Thread``。
"""

from __future__ import annotations

import asyncio
import threading
import types

import pytest

from app.tasks.operation_registry import OperationRegistry
from app.tasks.task_session import TaskSession
from app.views.pipeline import pipeline_view
from app.views.pipeline.pipeline_actions import (
    PipelineActions,
    PipelineServices,
    session_failed,
)
from app.views.pipeline.pipeline_config import PipelineConfig
from app.views.pipeline.pipeline_session import PipelineRunner
from tests.conftest import mock_filepicker, mock_page


class _LoopPage:
    """在另一條執行緒跑真正 event loop 的 page（run_task → run_coroutine_threadsafe）。"""

    width = 1200
    height = 800

    def __init__(self):
        self.overlay = []
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.loop_ident = None
        asyncio.run_coroutine_threadsafe(self._ident(), self.loop).result(timeout=3)

    async def _ident(self):
        self.loop_ident = threading.get_ident()

    def run_task(self, handler, *args):
        return asyncio.run_coroutine_threadsafe(handler(*args), self.loop)

    def update(self, *a, **k):
        pass

    def drain(self):
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), self.loop).result(
            timeout=3
        )

    def close(self):
        self.drain()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)
        self.loop.close()


class _Panel:
    def __init__(self):
        self.logs: list[str] = []
        self.steps: list[tuple] = []
        self.log_view = types.SimpleNamespace(add_many=self._add_many)
        self.finished_all = None

    def _add_many(self, items):
        self.logs.extend(text for text, _level in items)

    def set_step_running(self, num, name):
        self.steps.append(("running", num))

    def add_log(self, text, level="info"):
        self.logs.append(text)

    def finish_step(self, num, ok, cancelled=False):
        self.steps.append(("finished", num, ok, cancelled))

    def finish_all(self, success, cancelled=False):
        self.finished_all = (success, cancelled)


@pytest.fixture
def loop_page():
    page = _LoopPage()
    yield page
    page.close()


def _runner(page, panel=None, **kw):
    panel = panel or _Panel()
    kw.setdefault("session_failed", session_failed)
    runner = PipelineRunner(page, panel, lambda *a: None, **kw)
    runner.POLL_INTERVAL_SEC = 0.01
    return runner, panel


# --------------------------------------------------------------- offload


def test_step_service_runs_off_the_event_loop_thread(loop_page):
    """service 在 worker 執行緒執行，UI 更新只在 event loop 上發生。"""
    seen = {}
    runner, panel = _runner(loop_page)
    done = threading.Event()

    def service(session):
        seen["service"] = threading.get_ident()
        session.add_log("hello")

    original_add_log = panel.add_log
    ui_threads = []

    def add_log(text, level="info"):
        ui_threads.append(threading.get_ident())
        original_add_log(text, level)

    panel.add_log = add_log
    runner.start_single(1, "步驟", service, done.set)
    assert done.wait(3)
    loop_page.drain()

    assert seen["service"] not in (loop_page.loop_ident, threading.main_thread().ident)
    assert ui_threads
    assert set(ui_threads) == {loop_page.loop_ident}
    assert ">> hello" in panel.logs


# --------------------------------------------------------------- 生命週期


def test_unmount_defers_all_ui_updates_until_remount(loop_page):
    """卸載：watcher 停止、worker 照常結束，但完成後的 UI 更新不碰（已卸載的）控制項；
    重新掛載時才一次套用（最終日誌、步驟完成）。"""
    runner, panel = _runner(loop_page)
    release = threading.Event()
    started = threading.Event()
    result = {}

    def service(session):
        session.add_log("first")
        started.set()
        release.wait(3)
        session.add_log("after-unmount")

    t = threading.Thread(
        target=lambda: result.update(ok=runner.run_step(1, "步驟", service))
    )
    t.start()
    assert started.wait(3)
    loop_page.drain()
    assert runner.poller.running

    asyncio.run_coroutine_threadsafe(_call(runner.on_unmount), loop_page.loop).result(3)
    asyncio.run_coroutine_threadsafe(_call(runner.on_unmount), loop_page.loop).result(3)
    assert not runner.poller.running
    snapshot_logs = list(panel.logs)
    snapshot_steps = list(panel.steps)
    release.set()
    t.join(5)
    loop_page.drain()

    assert result["ok"] is True
    # 卸載期間：沒有任何 UI 更新（日誌、步驟完成都沒動）
    assert panel.logs == snapshot_logs
    assert panel.steps == snapshot_steps

    asyncio.run_coroutine_threadsafe(_call(runner.on_mount), loop_page.loop).result(3)
    loop_page.drain()

    assert ">> after-unmount" in panel.logs
    assert panel.logs.count(">> first") == 1
    assert ("finished", 1, True, False) in panel.steps
    assert not runner.poller.running  # 步驟已結束，不會留下 poller


def test_unmounted_sequence_defers_finish_and_button_restore(loop_page):
    runner, panel = _runner(loop_page, launch_worker=lambda target: target())
    ended = []
    asyncio.run_coroutine_threadsafe(_call(runner.on_unmount), loop_page.loop).result(3)

    runner.start_sequence([(1, "步驟", lambda s: None)], lambda: ended.append(True))
    loop_page.drain()
    assert ended == [] and panel.finished_all is None

    asyncio.run_coroutine_threadsafe(_call(runner.on_mount), loop_page.loop).result(3)
    loop_page.drain()
    assert ended == [True]
    assert panel.finished_all == (True, False)


def test_pipeline_sequence_has_one_parent_operation_across_child_steps(loop_page):
    registry = OperationRegistry()
    runner, _panel = _runner(loop_page, launch_worker=lambda target: target())
    runner.set_operation_registry(registry)
    active_counts = []
    ended = []

    def step(session):
        active_counts.append(registry.active_count())
        session.start()
        session.finish()

    runner.start_sequence(
        [(1, "第一步", step), (2, "第二步", step)],
        lambda: ended.append(True),
    )
    loop_page.drain()

    assert active_counts == [1, 1]
    assert ended == [True]
    assert registry.active() == []


def test_pipeline_admission_rejection_does_not_run_service(loop_page):
    registry = OperationRegistry()
    registry.begin_shutdown()
    runner, panel = _runner(loop_page, launch_worker=lambda target: target())
    runner.set_operation_registry(registry)
    called = []
    ended = []

    runner.start_sequence(
        [(1, "不可啟動", lambda _session: called.append(True))],
        lambda: ended.append(True),
    )
    loop_page.drain()

    assert called == []
    assert ended == [True]
    assert registry.active() == []
    assert any("未啟動新工作" in line for line in panel.logs)


@pytest.mark.parametrize("outcome", ["handled_error", "unexpected_exception", "cancel"])
def test_composite_parent_records_terminal_failure_or_cancel(
    loop_page, monkeypatch, outcome
):
    from app.shell.task_manager import STATUS_ERROR, TaskManager

    registry = OperationRegistry()
    manager = TaskManager(operation_registry=registry)
    manager.attach()
    runner, _panel = _runner(
        loop_page,
        launch_worker=lambda target: target(),
        session_factory=lambda: TaskSession(name="step", view_key="pipeline"),
    )
    runner.set_operation_registry(registry)
    later_steps = []

    def fail_step(session):
        session.start()
        if outcome == "handled_error":
            session.set_error()
        elif outcome == "cancel":
            runner.request_cancel()
        session.finish()

    if outcome == "unexpected_exception":

        def raise_from_step(*_args):
            raise RuntimeError("unexpected pipeline failure")

        monkeypatch.setattr(runner, "run_step", raise_from_step)

    try:
        runner.start_sequence(
            [
                (1, "第一步", fail_step),
                (2, "不應執行", lambda _session: later_steps.append(True)),
            ],
            lambda: None,
        )
        handle = runner.operation_handle

        assert handle is not None and handle.done_event.is_set()
        assert later_steps == []
        assert registry.active() == []
        if outcome == "cancel":
            assert handle.terminal_reason == "cancelled"
            assert handle.error is None
        else:
            assert handle.terminal_reason == "failed"
            assert any(task.status == STATUS_ERROR for task in manager.recent())
    finally:
        manager.detach()


def test_remount_resumes_a_single_poller_without_duplicate_logs(loop_page):
    runner, panel = _runner(loop_page)
    release = threading.Event()
    started = threading.Event()

    def service(session):
        session.add_log("one")
        started.set()
        release.wait(3)
        session.add_log("two")

    t = threading.Thread(target=lambda: runner.run_step(1, "步驟", service))
    t.start()
    assert started.wait(3)
    loop_page.drain()

    runner.on_unmount()
    # 重複掛載只會留下一個 poller（舊的會被新的取代）
    for _ in range(3):
        asyncio.run_coroutine_threadsafe(_call(runner.on_mount), loop_page.loop).result(
            3
        )
    assert runner.poller.running
    release.set()
    t.join(5)
    loop_page.drain()

    assert panel.logs.count(">> one") == 1
    assert panel.logs.count(">> two") == 1
    assert runner.poller.wait_idle(timeout=3)


async def _call(fn):
    fn()


def test_view_will_unmount_and_did_mount_delegate_to_the_runner():
    view = pipeline_view.PipelineView(mock_page(), mock_filepicker())
    calls = []
    view.runner.on_unmount = lambda: calls.append("unmount")
    view.runner.on_mount = lambda: calls.append("mount")

    view.will_unmount()
    view.did_mount()

    assert calls == ["unmount", "mount"]


# --------------------------------------------------------------- 取消


def test_cancel_discards_results_and_skips_following_steps(loop_page):
    runner, panel = _runner(loop_page)
    later = []

    def step1(session):
        for i in range(50):
            if i == 2:
                runner.request_cancel()
            yield {"progress": i / 50}

    assert runner.run_step(1, "第一步", step1) is False
    assert runner.run_step(2, "第二步", lambda s: later.append(1)) is False
    loop_page.drain()

    assert later == []
    assert ("finished", 1, False, True) in panel.steps  # 標示為「已取消」而非成功
    assert runner.request_cancel() is False  # 已在取消中
    runner.reset_cancel()
    assert not runner.cancel_event.is_set()


def test_failed_step_stops_the_sequence_and_reports_failure(loop_page):
    runner, panel = _runner(loop_page, launch_worker=lambda target: target())
    ran = []
    ended = []

    def boom(session):
        raise RuntimeError("step exploded")

    steps = [
        (1, "炸掉", boom),
        (2, "不應執行", lambda s: ran.append(2)),
    ]
    runner.start_sequence(steps, lambda: ended.append(True))
    loop_page.drain()

    assert ran == []
    assert panel.finished_all == (False, False)
    assert ended == [True]


# --------------------------------------------------------------- View 依賴邊界


class _RecordingActions:
    """假的 PipelineActions：只記錄 View 的委派，不呼叫任何 service。"""

    def __init__(self):
        self.calls: list[tuple] = []

    def extract(self, session, *args):
        self.calls.append(("extract", *args))

    def merge(self, session, *args, **kw):
        self.calls.append(("merge", *args, kw))

    def translate(self, session, *args, **kw):
        self.calls.append(("translate", *args, kw))

    def bundle(self, session, **kw):
        self.calls.append(("bundle", kw))
        return iter([])

    def one_click_steps(self, config, cfg, mode, lang_codes, merge_options):
        self.calls.append(("one_click", mode, tuple(lang_codes)))
        return [(1, "抽取資源", lambda s: self.calls.append(("step1",)))]


def _view_with(actions):
    return pipeline_view.PipelineView(
        mock_page(),
        mock_filepicker(),
        actions=actions,
        launch_worker=lambda target: target(),
    )


def test_view_delegates_every_step_to_injected_actions(tmp_path):
    actions = _RecordingActions()
    view = _view_with(actions)

    view._run_extraction("m", "o", "dual", ["en_us"])
    view._run_merge("src", "o", "folder", True, True, False, 0.5, 2, ["en_us"])
    view._run_translate("i", "o", True, False)
    view._run_bundle("i", str(tmp_path / "b.zip"), "d", None, None, None, [])

    kinds = [c[0] for c in actions.calls]
    assert kinds == ["extract", "merge", "translate", "bundle"]
    assert actions.calls[0] == ("extract", "m", "o", "dual", ["en_us"])
    # min/max_format 的 None 會被正規化為 0（與原行為一致）
    assert actions.calls[3][1]["min_format"] == 0
    assert actions.calls[3][1]["extra_folders"] is None


def test_one_click_runs_the_steps_the_actions_provide(tmp_path):
    actions = _RecordingActions()
    view = _view_with(actions)
    (tmp_path / "mods").mkdir()
    (tmp_path / "out").mkdir()
    view.input_path_text.value = str(tmp_path / "mods")
    view.output_path_text.value = str(tmp_path / "out")

    view._on_one_click_execute({"mode": "lang", "lang_codes": ["en_us"]})

    assert ("one_click", "lang", ("en_us",)) in actions.calls
    assert ("step1",) in actions.calls


def test_pipeline_view_does_not_depend_on_services_or_threads():
    """View 只依賴 PipelineActions／PipelineRunner 邊界：不 import service、不直接開執行緒。"""
    names = set(vars(pipeline_view))
    assert not {n for n in names if n.startswith("run_")}
    assert "threading" not in names
    assert "TaskSession" not in names


# --------------------------------------------------------------- PipelineActions


def _session():
    s = TaskSession()
    s.start()
    return s


def test_actions_merge_dispatches_by_input_mode(tmp_path):
    calls = []
    actions = PipelineActions(
        PipelineServices(
            merge_folder=lambda **kw: calls.append(("folder", kw)) or iter([]),
            merge_zip=lambda **kw: calls.append(("zip", kw)) or iter([]),
        )
    )
    common = {
        "only_lang": True,
        "process_zh_cn": True,
        "patchouli_skip": False,
        "patchouli_threshold": 0.5,
        "zh_en_threshold": 2,
    }
    list(actions.merge(_session(), "dir", str(tmp_path / "o1"), "folder", **common))
    list(actions.merge(_session(), "a.zip", str(tmp_path / "o2"), "zip", **common))

    assert [c[0] for c in calls] == ["folder", "zip"]
    assert calls[0][1]["input_dir"] == "dir"
    assert calls[1][1]["zip_paths"] == ["a.zip"]  # 單一 ZIP 會包成 list
    assert calls[0][1]["only_process_lang"] is True


def test_actions_dual_extract_skips_book_when_lang_fails(tmp_path):
    calls = []

    def lang(mods, out, session, lang_codes=None, **kw):
        calls.append("lang")
        session.set_error()

    actions = PipelineActions(
        PipelineServices(
            extract_lang=lang, extract_book=lambda *a, **k: calls.append("book")
        )
    )
    actions.extract(_session(), str(tmp_path), str(tmp_path / "o"), "dual", ["en_us"])

    assert calls == ["lang"]


def test_actions_one_click_steps_cover_the_four_stages(tmp_path):
    (tmp_path / "mods").mkdir()
    (tmp_path / "out").mkdir()
    cfg = PipelineConfig(str(tmp_path / "mods"), str(tmp_path / "out"))
    actions = PipelineActions()
    steps = actions.one_click_steps({}, cfg, "lang", ["en_us"], {})

    assert [(n, name) for n, name, _ in steps] == [
        (1, "抽取資源"),
        (2, "語系比對"),
        (3, "啟動翻譯"),
        (4, "打包資源"),
    ]


# --------------------------------------------------------------- 契約：both → dual


def test_one_click_all_modes_reach_both_extractors_and_both_merge_sources(tmp_path):
    """對話框的「全部執行」(both) → on_execute 收到 dual → lang + book 都抽取、兩個 merge 來源。"""
    from app.views.pipeline.pipeline_config import normalize_extract_mode

    assert normalize_extract_mode("both") == "dual"
    assert normalize_extract_mode("lang") == "lang"
    assert normalize_extract_mode(None) == "lang"

    calls = []
    services = PipelineServices(
        extract_lang=lambda *a, **k: calls.append("lang"),
        extract_book=lambda *a, **k: calls.append("book"),
        merge_folder=lambda **k: calls.append(("merge", k["input_dir"])) or iter([]),
        translate=lambda **k: None,
        build_staging=lambda *a, **k: {"copied": 0, "merged": 0},
    )
    view = pipeline_view.PipelineView(
        mock_page(),
        mock_filepicker(),
        actions=PipelineActions(services),
        launch_worker=lambda target: target(),
    )
    (tmp_path / "mods").mkdir()
    (tmp_path / "out").mkdir()
    view.input_path_text.value = str(tmp_path / "mods")
    view.output_path_text.value = str(tmp_path / "out")

    # 即使收到舊式的 "both"，View 邊界也會正規化
    view._on_one_click_execute({"mode": "both", "lang_codes": ["en_us"]})

    assert calls[:2] == ["lang", "book"]
    merges = [c for c in calls if isinstance(c, tuple)]
    assert len(merges) == 2
    cfg = PipelineConfig(str(tmp_path / "mods"), str(tmp_path / "out"))
    assert [m[1] for m in merges] == [
        cfg.extract_lang_output_dir,
        cfg.extract_book_output_dir,
    ]


# --------------------------------------------------------------- 契約：bundle 的 TaskSession 生命週期


def test_bundle_action_registers_progresses_and_finishes_in_task_manager(tmp_path):
    from app.shell.task_manager import TaskManager

    manager = TaskManager()
    manager.attach()
    try:
        seen = {}

        def fake_bundle(**kwargs):
            seen["active_during"] = [t.name for t in manager.active()]
            yield {"progress": 0.5, "log": "half"}

        actions = PipelineActions(PipelineServices(bundle=fake_bundle))
        session = TaskSession(name="打包", view_key="pipeline")
        list(
            actions.bundle(
                session,
                input_root_dir=str(tmp_path),
                output_zip_path=str(tmp_path / "o" / "x.zip"),
            )
        )

        assert seen["active_during"] == ["打包"]  # start() 後全域看得到
        assert manager.active() == []  # finish() 後離開 active
        assert [t.name for t in manager.recent()] == ["打包"]
        assert session.snapshot()["status"] == "DONE"
    finally:
        manager.detach()


def test_bundle_action_error_stays_error_and_cancel_does_not_leave_running(tmp_path):
    from app.shell.task_manager import TaskManager

    manager = TaskManager()
    manager.attach()
    try:
        failing = PipelineActions(
            PipelineServices(bundle=lambda **k: iter([{"error": True}]))
        )
        s1 = TaskSession(name="打包失敗", view_key="pipeline")
        list(
            failing.bundle(
                s1, input_root_dir="i", output_zip_path=str(tmp_path / "a.zip")
            )
        )
        assert s1.snapshot()["status"] == "ERROR"

        def endless(**kwargs):
            while True:
                yield {"progress": 0.1}

        cancelled = PipelineActions(PipelineServices(bundle=endless))
        s2 = TaskSession(name="打包取消", view_key="pipeline")
        gen = cancelled.bundle(
            s2, input_root_dir="i", output_zip_path=str(tmp_path / "b.zip")
        )
        next(gen)
        gen.close()  # PipelineRunner 取消時會 close generator
        assert s2.snapshot()["status"] != "RUNNING"
        assert manager.active() == []
    finally:
        manager.detach()


def test_one_click_bundle_step_manages_the_session_lifecycle(tmp_path):
    (tmp_path / "mods").mkdir()
    (tmp_path / "out").mkdir()
    cfg = PipelineConfig(str(tmp_path / "mods"), str(tmp_path / "out"))
    actions = PipelineActions(
        PipelineServices(
            build_staging=lambda *a, **k: {"copied": 1, "merged": 0},
            bundle=lambda **k: iter([{"progress": 1.0}]),
        )
    )
    step4 = actions.one_click_steps({}, cfg, "lang", ["en_us"], {})[3][2]
    session = TaskSession(name="一鍵", view_key="pipeline")
    list(step4(session))
    snap = session.snapshot()
    assert snap["status"] == "DONE"
    assert any(
        "打包暫存完成" in e.text for e in snap["logs"]
    )  # 暫存日誌沒被 start() 清掉

    empty = PipelineActions(
        PipelineServices(build_staging=lambda *a, **k: {"copied": 0, "merged": 0})
    )
    s2 = TaskSession(name="一鍵", view_key="pipeline")
    list(empty.one_click_steps({}, cfg, "lang", ["en_us"], {})[3][2](s2))
    assert s2.snapshot()["status"] == "ERROR"


# --------------------------------------------------------------- 契約：翻譯輸出路徑單一來源


def test_translate_output_path_follows_lm_translate_folder_name(monkeypatch):
    from app.views.pipeline import pipeline_config

    monkeypatch.setattr(
        pipeline_config,
        "load_config",
        lambda: {"lang_merger": {"lm_translate_folder_name": "LM翻譯後"}},
    )
    cfg = PipelineConfig("in", "out")
    assert cfg.translate_output_dir.endswith("lm_translate/LM翻譯後") or (
        cfg.translate_output_dir.replace("\\", "/").endswith("lm_translate/LM翻譯後")
    )
    assert cfg.bundle_sources[-1] == cfg.translate_output_dir
