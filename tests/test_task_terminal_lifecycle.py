"""TaskSession 的 terminal lifecycle 契約（#114）：

呼叫過 ``session.start()`` 的工作，任何結束路徑（成功／service 回報錯誤／例外／取消）都必須
送出一次 ``finish()``，失敗時順序一定是 ``set_error()`` → ``finish()``。``TaskManager`` 不把
``set_error()`` 當結案事件，所以漏掉 ``finish()`` 會讓 session 永遠留在 ``active()``
（影響頂列任務、``CacheRootReloader`` 的 busy 判斷、Windows 安全關閉的 drain）。

這裡用**真正的 TaskManager** 驗證，而不是檢查 session 自己的旗標。
"""

from __future__ import annotations

import pytest

from app.services_impl.pipelines import extract_service, lm_service, merge_service
from app.shell.task_manager import STATUS_DONE, STATUS_ERROR, TaskManager
from app.tasks.task_session import TaskSession
from app.views.pipeline.pipeline_actions import PipelineActions, PipelineServices
from app.views.pipeline.pipeline_config import PipelineConfig
from app.views.pipeline.pipeline_session import PipelineRunner
from translation_tool.utils.cancellation import TaskCancelled


@pytest.fixture
def manager():
    m = TaskManager()
    m.attach()
    yield m
    m.detach()


def _session(name="任務"):
    return TaskSession(name=name, view_key="pipeline")


def _assert_terminal(manager, session, expected):
    assert manager.active() == [], "terminal 之後不可殘留在 active"
    assert [t.status for t in manager.recent()][:1] == [expected]
    assert session.snapshot()["status"] == (
        "ERROR" if expected == STATUS_ERROR else "DONE"
    )


def _run(gen_or_none):
    if gen_or_none is not None:
        list(gen_or_none)


# ------------------------------------------------------------ extract services


def _updates(*items):
    def gen():
        yield from items

    return gen()


def test_extract_success_error_and_cancel_all_finish(manager, monkeypatch):
    ok = _session()
    ok.start()
    extract_service._run_extraction_with_session(
        _updates({"progress": 1.0}), ok, "Lang"
    )
    _assert_terminal(manager, ok, STATUS_DONE)

    failed = _session()
    failed.start()
    extract_service._run_extraction_with_session(
        _updates({"error": "boom"}), failed, "Lang"
    )
    _assert_terminal(manager, failed, STATUS_ERROR)

    cancelled = _session()
    cancelled.start()
    monkeypatch.setattr(extract_service, "is_cancelled", lambda: True)
    extract_service._run_extraction_with_session(
        _updates({"progress": 0.1}), cancelled, "Lang"
    )
    assert manager.active() == []  # 取消不可殘留 active


@pytest.mark.parametrize("which", ["lang", "book", "dual"])
def test_extract_service_exception_ends_error_then_finish(manager, monkeypatch, which):
    def boom(*a, **k):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(extract_service, "_select_extraction_generator", boom)
    service = {
        "lang": extract_service.run_lang_extraction_service,
        "book": extract_service.run_book_extraction_service,
        "dual": extract_service.run_dual_extraction_service,
    }[which]
    session = _session()
    service("mods", "out", session)
    _assert_terminal(manager, session, STATUS_ERROR)


def test_extract_unmanaged_session_is_left_to_the_owner(manager):
    """manage_session=False（外層擁有生命週期）時，service 不 finish。"""
    session = _session()
    session.start()
    extract_service._run_extraction_with_session(
        _updates({"error": "boom"}), session, "Lang", finish_session=False
    )
    assert [t.name for t in manager.active()] == ["任務"]
    session.finish()
    _assert_terminal(manager, session, STATUS_ERROR)


# ------------------------------------------------------------ LM service


@pytest.mark.parametrize("mode", ["ok", "error", "raises"])
def test_lm_service_always_ends_with_one_finish(manager, monkeypatch, mode):
    def gen(*a, **k):
        if mode == "raises":
            raise RuntimeError("api exploded")
        yield {"progress": 0.5}
        if mode == "error":
            yield {"error": True, "log": "bad"}

    monkeypatch.setattr(lm_service, "lm_translate_gen", gen)
    session = _session()
    lm_service.run_lm_translation_service("in", "out", session)
    _assert_terminal(manager, session, STATUS_DONE if mode == "ok" else STATUS_ERROR)


# ------------------------------------------------------------ merge services


@pytest.mark.parametrize("mode", ["ok", "error", "raises"])
def test_merge_folder_service_terminal_paths(manager, monkeypatch, tmp_path, mode):
    def gen(*a, **k):
        if mode == "raises":
            raise RuntimeError("merge exploded")
        yield {"progress": 0.5}
        if mode == "error":
            yield {"error": True, "log": "bad"}

    monkeypatch.setattr(merge_service, "merge_zhcn_to_zhtw_from_folder", gen)
    monkeypatch.setattr(merge_service, "_run_extracted_stage2", lambda *a, **k: None)
    session = _session()
    session.start()
    _run(
        merge_service.run_merge_folder_batch_service(
            str(tmp_path), str(tmp_path / "o"), session, True
        )
    )
    _assert_terminal(manager, session, STATUS_DONE if mode == "ok" else STATUS_ERROR)


def test_merge_folder_service_finish_session_false_leaves_it_to_the_caller(
    manager, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        merge_service, "merge_zhcn_to_zhtw_from_folder", lambda *a, **k: iter([])
    )
    monkeypatch.setattr(merge_service, "_run_extracted_stage2", lambda *a, **k: None)
    session = _session()
    session.start()
    _run(
        merge_service.run_merge_folder_batch_service(
            str(tmp_path), str(tmp_path / "o"), session, True, finish_session=False
        )
    )
    assert len(manager.active()) == 1


def test_merge_zip_service_all_failed_and_exception_finish(manager, monkeypatch):
    monkeypatch.setattr(merge_service, "_merge_one_zip", lambda *a, **k: ["bad zip"])
    all_failed = _session()
    all_failed.start()
    _run(merge_service.run_merge_zip_batch_service(["a.zip"], "out", all_failed, True))
    _assert_terminal(manager, all_failed, STATUS_ERROR)

    def boom(*a, **k):
        raise RuntimeError("zip exploded")

    monkeypatch.setattr(merge_service, "_merge_one_zip", boom)
    raised = _session()
    raised.start()
    _run(merge_service.run_merge_zip_batch_service(["a.zip"], "out", raised, True))
    _assert_terminal(manager, raised, STATUS_ERROR)


# ------------------------------------------------------------ PipelineActions（composite owner）


def test_dual_extract_failure_and_exception_finish_the_shared_session(
    manager, tmp_path
):
    def lang_fails(mods, out, session, **k):
        session.set_error()

    actions = PipelineActions(
        PipelineServices(extract_lang=lang_fails, extract_book=lambda *a, **k: None)
    )
    failed = _session()
    actions.extract(failed, str(tmp_path), str(tmp_path / "o"), "dual", ["en_us"])
    _assert_terminal(manager, failed, STATUS_ERROR)

    def lang_raises(*a, **k):
        raise RuntimeError("lang exploded")

    raising = PipelineActions(PipelineServices(extract_lang=lang_raises))
    s2 = _session()
    with pytest.raises(RuntimeError):
        raising.extract(s2, str(tmp_path), str(tmp_path / "o"), "dual", ["en_us"])
    _assert_terminal(manager, s2, STATUS_ERROR)

    ok = PipelineActions(
        PipelineServices(
            extract_lang=lambda *a, **k: None, extract_book=lambda *a, **k: None
        )
    )
    s3 = _session()
    ok.extract(s3, str(tmp_path), str(tmp_path / "o"), "dual", ["en_us"])
    _assert_terminal(manager, s3, STATUS_DONE)


def _cfg(tmp_path):
    (tmp_path / "mods").mkdir(exist_ok=True)
    (tmp_path / "out").mkdir(exist_ok=True)
    return PipelineConfig(str(tmp_path / "mods"), str(tmp_path / "out"))


def test_one_click_merge_step_failure_exception_and_success_all_finish(
    manager, tmp_path
):
    cfg = _cfg(tmp_path)

    def merge_step(actions):
        return actions.one_click_steps({}, cfg, "dual", ["en_us"], {})[1][2]

    def failing_source(**kwargs):
        kwargs["session"].set_error()
        yield {"progress": 1.0}

    s1 = _session()
    _run(merge_step(PipelineActions(PipelineServices(merge_folder=failing_source)))(s1))
    _assert_terminal(manager, s1, STATUS_ERROR)

    def raising_source(**kwargs):
        raise RuntimeError("merge exploded")
        yield  # pragma: no cover

    s2 = _session()
    with pytest.raises(RuntimeError):
        _run(
            merge_step(PipelineActions(PipelineServices(merge_folder=raising_source)))(
                s2
            )
        )
    _assert_terminal(manager, s2, STATUS_ERROR)

    s3 = _session()
    _run(
        merge_step(
            PipelineActions(PipelineServices(merge_folder=lambda **k: iter([])))
        )(s3)
    )
    _assert_terminal(manager, s3, STATUS_DONE)


def test_bundle_service_exception_is_recorded_as_error_not_done(manager, tmp_path):
    """service 直接 raise：必須是 set_error() → finish()，TaskManager 記為 ERROR（不是 DONE）。"""

    def raising(**kwargs):
        raise RuntimeError("bundle exploded")
        yield  # pragma: no cover

    actions = PipelineActions(PipelineServices(bundle=raising))
    session = _session()
    with pytest.raises(RuntimeError):
        list(
            actions.bundle(
                session,
                input_root_dir="i",
                output_zip_path=str(tmp_path / "o" / "x.zip"),
            )
        )
    _assert_terminal(manager, session, STATUS_ERROR)

    cfg = _cfg(tmp_path)
    step4 = PipelineActions(
        PipelineServices(
            build_staging=lambda *a, **k: {"copied": 1, "merged": 0}, bundle=raising
        )
    ).one_click_steps({}, cfg, "lang", ["en_us"], {})[3][2]
    s2 = _session()
    with pytest.raises(RuntimeError):
        list(step4(s2))
    _assert_terminal(manager, s2, STATUS_ERROR)


# ------------------------------------------------------------ PipelineRunner 安全網


class _Panel:
    def __getattr__(self, name):
        return lambda *a, **k: None

    log_view = type("LV", (), {"add_many": staticmethod(lambda items: None)})()


class _Page:
    def run_task(self, handler, *args):
        return None


def test_runner_exception_and_cancellation_leave_no_active_session(manager):
    session = _session()
    session.start()
    runner = PipelineRunner(
        _Page(),
        _Panel(),
        lambda *a: None,
        session_factory=lambda: session,
        session_failed=lambda s: s.error,
    )

    def raises(s):
        raise RuntimeError("step exploded")

    assert runner.run_step(1, "炸掉", raises) is False
    _assert_terminal(manager, session, STATUS_ERROR)

    cancelled = _session()
    cancelled.start()
    runner2 = PipelineRunner(
        _Page(),
        _Panel(),
        lambda *a: None,
        session_factory=lambda: cancelled,
        session_failed=lambda s: s.error,
    )

    def cancels(s):
        raise TaskCancelled

    runner2.run_step(1, "取消", cancels)
    assert manager.active() == []
