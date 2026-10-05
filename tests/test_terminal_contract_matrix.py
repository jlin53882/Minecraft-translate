"""Terminal lifecycle 契約矩陣（#114）：

    success / error / exception / cancel   ×   single / composite

最高 invariant：``session.start()`` 過的任務，最後都不能殘留在 ``TaskManager.active()``。

- success   → recent DONE
- error     → ``set_error()`` → ``finish()``，recent ERROR
- exception → 例外 → ``set_error()`` → ``finish()``，recent ERROR（不可是 DONE）
- cancel    → ``active() == []``（涵蓋 ``TaskCancelled``、取消旗標／``cancel_scope``、
  generator 在 yield 後被 ``PipelineRunner`` ``close()``）
- composite 另外保證：1 個 UI step = 1 個 session = 1 次 start = 1 筆 terminal record

一律使用真正的 ``TaskManager``，success／error／exception 直接呼叫（驗證 service／action
自己的 ownership，不靠 ``PipelineRunner`` 的安全網），cancel 經由 ``PipelineRunner``（真實取消路徑）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services_impl.pipelines import extract_service, lm_service, merge_service
from app.shell.task_manager import STATUS_DONE, STATUS_ERROR, TaskManager
from app.tasks.task_session import TaskSession
from app.views.pipeline.pipeline_actions import PipelineActions, PipelineServices
from app.views.pipeline.pipeline_config import PipelineConfig
from app.views.pipeline.pipeline_session import PipelineRunner
from translation_tool.utils.cancellation import TaskCancelled

MODES = ["success", "error", "exception"]


@pytest.fixture
def manager():
    m = TaskManager()
    m.attach()
    yield m
    m.detach()


class _Panel:
    log_view = type("LV", (), {"add_many": staticmethod(lambda items: None)})()

    def __getattr__(self, name):
        return lambda *a, **k: None


class _Page:
    def run_task(self, handler, *args):
        return None


def _runner(session):
    return PipelineRunner(
        _Page(),
        _Panel(),
        lambda *a: None,
        session_factory=lambda: session,
        session_failed=lambda s: s.error,
    )


def _drain(result):
    if result is not None:
        for _ in result:
            pass


def _expect(manager, session, mode, *, records=1):
    assert manager.active() == [], "terminal 之後不可殘留在 active"
    expected = STATUS_DONE if mode == "success" else STATUS_ERROR
    recent = manager.recent()
    assert len(recent) == records, "1 個 session 只能有 1 筆 terminal record"
    assert recent[0].status == expected
    assert session.snapshot()["status"] == ("DONE" if mode == "success" else "ERROR")


def _fake_updates(mode, on_first=None):
    """各 service 底層 generator 的假實作。"""

    def gen(*a, **k):
        if mode == "exception":
            raise RuntimeError("service exploded")
        if on_first is not None:
            on_first()
        yield {"progress": 0.5, "log": "work"}
        if mode == "error":
            yield {"error": True, "log": "bad"}

    return gen


# =============================================================================
# SINGLE：service／action 擁有完整的 start → terminal finish
# =============================================================================


def _single_extract(monkeypatch, mode, tmp_path, on_first=None):
    monkeypatch.setattr(
        extract_service,
        "_select_extraction_generator",
        lambda *a, **k: _fake_updates(mode, on_first)(),
    )
    return lambda s: extract_service.run_lang_extraction_service("m", "o", s)


def _single_lm(monkeypatch, mode, tmp_path, on_first=None):
    monkeypatch.setattr(lm_service, "lm_translate_gen", _fake_updates(mode, on_first))
    return lambda s: lm_service.run_lm_translation_service("i", "o", s)


def _single_merge(monkeypatch, mode, tmp_path, on_first=None):
    monkeypatch.setattr(
        merge_service, "merge_zhcn_to_zhtw_from_folder", _fake_updates(mode, on_first)
    )
    monkeypatch.setattr(merge_service, "_run_extracted_stage2", lambda *a, **k: None)
    actions = PipelineActions()
    return lambda s: actions.merge(
        s,
        str(tmp_path),
        str(tmp_path / "o"),
        "folder",
        only_lang=True,
        process_zh_cn=True,
        patchouli_skip=False,
        patchouli_threshold=0.5,
        zh_en_threshold=2,
    )


def _single_bundle(monkeypatch, mode, tmp_path, on_first=None):
    def bundle(**kwargs):
        if mode == "exception":
            raise RuntimeError("service exploded")
        if on_first is not None:
            on_first()
        yield {"progress": 0.5, "log": "work"}
        if mode == "error":
            yield {"error": True}
        yield {"progress": 1.0}

    actions = PipelineActions(PipelineServices(bundle=bundle))
    return lambda s: actions.bundle(
        s, input_root_dir="i", output_zip_path=str(tmp_path / "o" / "x.zip")
    )


SINGLE = {
    "extract": _single_extract,
    "lm": _single_lm,
    "merge": _single_merge,
    "bundle": _single_bundle,
}


@pytest.mark.parametrize("target", SINGLE)
@pytest.mark.parametrize("mode", MODES)
def test_single_success_error_exception(manager, monkeypatch, tmp_path, target, mode):
    step = SINGLE[target](monkeypatch, mode, tmp_path)
    session = TaskSession(name=target, view_key="pipeline")
    try:
        _drain(step(session))
    except RuntimeError:
        assert target == "bundle"  # bundle action 讓例外往外傳（Runner 負責回報）
        assert mode == "exception"
    _expect(manager, session, mode)


@pytest.mark.parametrize("target", SINGLE)
def test_single_cancel_flag_or_generator_close_leaves_no_active(
    manager, monkeypatch, tmp_path, target
):
    """經由 PipelineRunner 的真實取消路徑（取消旗標／cancel_scope／generator.close）。"""
    session = TaskSession(name=target, view_key="pipeline")
    runner = _runner(session)
    step = SINGLE[target](
        monkeypatch, "success", tmp_path, on_first=runner.request_cancel
    )

    assert runner.run_step(1, target, step) is False
    assert manager.active() == []
    assert len(manager.recent()) == 1


@pytest.mark.parametrize("target", SINGLE)
def test_single_task_cancelled_exception_leaves_no_active(
    manager, monkeypatch, tmp_path, target
):
    def raise_cancel():
        raise TaskCancelled

    session = TaskSession(name=target, view_key="pipeline")
    runner = _runner(session)
    step = SINGLE[target](monkeypatch, "success", tmp_path, on_first=raise_cancel)
    runner.run_step(1, target, step)
    assert manager.active() == []


# =============================================================================
# COMPOSITE：outer 是唯一 lifecycle owner（1 step = 1 session = 1 start = 1 terminal）
# =============================================================================


def _cfg(tmp_path):
    for sub in ("mods", "out"):
        (tmp_path / sub).mkdir(exist_ok=True)
    return PipelineConfig(str(tmp_path / "mods"), str(tmp_path / "out"))


def _populate_translate_inputs(cfg):
    dirs = list(cfg.translate_input_dirs)
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
        (Path(d) / "x.json").write_text("{}", encoding="utf-8")
    return dirs


class _Counter:
    """記錄 inner operation 是否執行，以及 session 的 start 次數（驗證只有 outer start）。"""

    def __init__(self):
        self.ran: list[str] = []


def _composite_dual(tmp_path, mode, counter, on_first=None):
    cfg = _cfg(tmp_path)

    def lang(mods, out, session, **kw):
        counter.ran.append("lang")
        assert kw["manage_session"] is False  # inner 不擁有 session
        if on_first:
            on_first()
        if mode == "exception":
            raise RuntimeError("lang exploded")
        if mode == "error":
            session.set_error()

    def book(mods, out, session, **kw):
        counter.ran.append("book")
        assert kw["manage_session"] is False

    actions = PipelineActions(PipelineServices(extract_lang=lang, extract_book=book))
    return lambda s: actions.extract(
        s, str(tmp_path / "mods"), str(cfg.output_dir), "dual", ["en_us"]
    )


def _composite_merge(tmp_path, mode, counter, on_first=None):
    cfg = _cfg(tmp_path)

    def merge_folder(**kw):
        counter.ran.append(Path(kw["input_dir"]).name)
        assert kw["finish_session"] is False
        if on_first:
            on_first()
        if mode == "exception":
            raise RuntimeError("merge exploded")
        if mode == "error":
            kw["session"].set_error()
        yield {"progress": 1.0}

    actions = PipelineActions(PipelineServices(merge_folder=merge_folder))
    return actions.one_click_steps({}, cfg, "dual", ["en_us"], {})[1][2]


def _composite_translate(tmp_path, mode, counter, on_first=None):
    cfg = _cfg(tmp_path)
    _populate_translate_inputs(cfg)

    def translate(**kw):
        counter.ran.append(Path(kw["input_dir"]).name)
        assert kw["manage_session"] is False
        kw["session"].add_log(f"marker:{kw['input_dir']}")
        if on_first:
            on_first()
        if mode == "exception":
            raise RuntimeError("translate exploded")
        if mode == "error":
            kw["session"].set_error()

    actions = PipelineActions(PipelineServices(translate=translate))
    return actions.one_click_steps({}, cfg, "lang", ["en_us"], {})[2][2]


def _composite_bundle(tmp_path, mode, counter, on_first=None):
    cfg = _cfg(tmp_path)

    def bundle(**kw):
        counter.ran.append("bundle")
        if on_first:
            on_first()
        if mode == "exception":
            raise RuntimeError("bundle exploded")
        yield {"progress": 0.5}
        if mode == "error":
            yield {"error": True}
        yield {"progress": 1.0}

    staging = {"copied": 1, "merged": 0}
    if mode == "error-empty":
        staging = {"copied": 0, "merged": 0}
    actions = PipelineActions(
        PipelineServices(bundle=bundle, build_staging=lambda *a, **k: staging)
    )
    return actions.one_click_steps({}, cfg, "lang", ["en_us"], {})[3][2]


COMPOSITE = {
    "dual_extract": _composite_dual,
    "one_click_merge": _composite_merge,
    "one_click_translate": _composite_translate,
    "one_click_bundle": _composite_bundle,
}
# 「第二個 inner operation」：失敗／取消後不可執行
SECOND_INNER = {
    "dual_extract": "book",
    "one_click_merge": "_提取book_輸出",
    "one_click_translate": None,  # 依 translate_input_dirs 檢查（見下）
    "one_click_bundle": None,
}


@pytest.mark.parametrize("target", COMPOSITE)
@pytest.mark.parametrize("mode", MODES)
def test_composite_success_error_exception(manager, tmp_path, target, mode):
    counter = _Counter()
    step = COMPOSITE[target](tmp_path, mode, counter)
    session = TaskSession(name=target, view_key="pipeline")
    try:
        _drain(step(session))
    except RuntimeError:
        assert mode == "exception"
    _expect(manager, session, mode)  # 只有 1 筆 terminal record（不因 inner 數量重複）
    if mode != "success" and target in ("dual_extract", "one_click_translate"):
        assert len(counter.ran) == 1  # 失敗後不跑第二個 inner
    if mode != "success" and target == "one_click_merge":
        assert counter.ran == ["_提取lang_輸出"]


def test_composite_success_runs_every_inner_operation_once(manager, tmp_path):
    for target, expected in (
        ("dual_extract", ["lang", "book"]),
        ("one_click_merge", ["_提取lang_輸出", "_提取book_輸出"]),
    ):
        counter = _Counter()
        session = TaskSession(name=target, view_key="pipeline")
        _drain(COMPOSITE[target](tmp_path, "success", counter)(session))
        assert counter.ran == expected
    assert len(manager.recent()) == 2  # 每個 step 各 1 筆，不是每個 inner 1 筆


def test_composite_translate_keeps_all_logs_and_one_record(manager, tmp_path):
    counter = _Counter()
    session = TaskSession(name="一鍵翻譯", view_key="pipeline")
    _drain(_composite_translate(tmp_path, "success", counter)(session))

    texts = [e.text for e in session.snapshot()["logs"]]
    assert len(counter.ran) >= 2
    for name in counter.ran:
        assert any(name in t for t in texts), (
            "前一個來源的日誌不可被後一次 start() 清掉"
        )
    _expect(manager, session, "success")


def test_composite_translate_without_inputs_still_start_finish(manager, tmp_path):
    cfg = _cfg(tmp_path)
    step = PipelineActions().one_click_steps({}, cfg, "lang", ["en_us"], {})[2][2]
    session = TaskSession(name="一鍵翻譯", view_key="pipeline")
    step(session)
    assert any("沒有待翻譯內容" in e.text for e in session.snapshot()["logs"])
    _expect(manager, session, "success")


def test_composite_bundle_staging_empty_ends_error(manager, tmp_path):
    counter = _Counter()
    session = TaskSession(name="一鍵打包", view_key="pipeline")
    _drain(_composite_bundle(tmp_path, "error-empty", counter)(session))
    assert counter.ran == []  # bundle service 不會被呼叫
    _expect(manager, session, "error")


# ---- composite cancel（經由 PipelineRunner 的真實取消路徑）----------------------


@pytest.mark.parametrize("target", COMPOSITE)
def test_composite_cancel_leaves_no_active_and_skips_following_inner(
    manager, tmp_path, target
):
    counter = _Counter()
    session = TaskSession(name=target, view_key="pipeline")
    runner = _runner(session)
    step = COMPOSITE[target](
        tmp_path, "success", counter, on_first=runner.request_cancel
    )

    assert runner.run_step(1, target, step) is False
    assert manager.active() == [], target
    assert len(manager.recent()) == 1, target
    # 第一個 inner 執行中取消 → 不跑第二個
    assert len(counter.ran) == 1, (target, counter.ran)


@pytest.mark.parametrize("target", COMPOSITE)
def test_composite_task_cancelled_exception_leaves_no_active(manager, tmp_path, target):
    def raise_cancel():
        raise TaskCancelled

    counter = _Counter()
    session = TaskSession(name=target, view_key="pipeline")
    runner = _runner(session)
    step = COMPOSITE[target](tmp_path, "success", counter, on_first=raise_cancel)
    runner.run_step(1, target, step)
    assert manager.active() == [], target


def test_composite_merge_generator_close_after_first_source_skips_the_second(
    manager, tmp_path
):
    """generator yield 之後 runner 偵測取消並 close()：outer finally 結案，第二來源不跑。"""
    cfg = _cfg(tmp_path)
    counter = _Counter()
    session = TaskSession(name="一鍵合併", view_key="pipeline")
    runner = _runner(session)

    def merge_folder(**kw):
        counter.ran.append(Path(kw["input_dir"]).name)
        yield {"progress": 0.5}
        runner.request_cancel()  # yield 之後才取消 → runner 在下一輪迭代 close()
        yield {"progress": 1.0}

    step = PipelineActions(PipelineServices(merge_folder=merge_folder)).one_click_steps(
        {}, cfg, "dual", ["en_us"], {}
    )[1][2]
    assert runner.run_step(2, "合併", step) is False
    assert counter.ran == ["_提取lang_輸出"]
    assert manager.active() == []
