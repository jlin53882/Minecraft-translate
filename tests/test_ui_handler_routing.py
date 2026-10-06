"""``UISessionLogHandler``：後台記錄依「寫出它的任務」送進該任務自己的畫面。"""

from __future__ import annotations

import json
import logging
import threading

import pytest

from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service
from app.tasks.task_session import TaskSession
from translation_tool.utils import ui_mirror
from translation_tool.utils.ui_logging_handler import UISessionLogHandler
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor, task_scope


@pytest.fixture
def routed():
    """獨立的 handler 掛在專用 logger 上（不碰全域 UI_LOG_HANDLER）。"""
    handler = UISessionLogHandler()
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("test.ui_handler_routing")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    yield handler, logger
    logger.removeHandler(handler)
    handler.clear()


def _texts(session):
    return [e.text for e in session.snapshot()["logs"]]


def test_records_go_to_the_session_of_the_thread_that_wrote_them(routed):
    handler, logger = routed
    a, b = TaskSession(name="A"), TaskSession(name="B")
    bound = threading.Barrier(2)
    done = threading.Barrier(2)

    def worker(session, message):
        handler.set_session(session)  # 等同服務入口
        bound.wait(timeout=5)  # 兩個任務都綁定之後才寫 log
        logger.info(message)
        done.wait(timeout=5)
        handler.set_session(None)

    threads = [
        threading.Thread(target=worker, args=(a, "來自 A")),
        threading.Thread(target=worker, args=(b, "來自 B")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert _texts(a) == ["來自 A"]
    assert _texts(b) == ["來自 B"]


def test_pool_worker_records_go_to_the_submitting_task(routed):
    handler, logger = routed
    a, b = TaskSession(name="A"), TaskSession(name="B")
    handler.set_session(a)  # 主執行緒歸屬 A
    with ContextThreadPoolExecutor(2) as pool:
        pool.submit(logger.info, "池內 A").result()
    # 另一個任務也綁定後，A 的池內記錄仍屬於 A
    results = {}

    def other():
        handler.set_session(b)
        with ContextThreadPoolExecutor(2) as pool:
            pool.submit(logger.info, "池內 B").result()
        results["done"] = True

    t = threading.Thread(target=other)
    t.start()
    t.join()
    with ContextThreadPoolExecutor(2) as pool:
        pool.submit(logger.info, "池內 A 之二").result()

    assert _texts(a) == ["池內 A", "池內 A 之二"]
    assert _texts(b) == ["池內 B"]


def test_record_without_a_task_falls_back_to_the_latest_bound_session(routed):
    handler, logger = routed
    first, second = TaskSession(name="1"), TaskSession(name="2")
    handler.set_session(first)
    handler.set_session(second)
    ui_mirror.set_current_task(None)  # UI 執行緒／第三方執行緒：沒有任務歸屬

    logger.info("沒有歸屬")

    assert _texts(second) == ["沒有歸屬"]
    assert _texts(first) == []


def test_record_of_a_known_but_unregistered_task_is_not_delivered(routed):
    handler, logger = routed
    bound = TaskSession(name="bound")
    handler.set_session(bound)
    with task_scope("ui-worker-without-session"):
        logger.info("不相干的任務")

    assert _texts(bound) == []


def test_unbinding_removes_only_the_current_task_and_restores_the_previous_latest(
    routed,
):
    handler, logger = routed
    a, b = TaskSession(name="A"), TaskSession(name="B")
    results = {}

    def run_b():
        handler.set_session(b)
        logger.info("B 執行中")
        handler.set_session(None)  # B 結束：只解除 B
        results["unbound"] = True

    handler.set_session(a)
    t = threading.Thread(target=run_b)
    t.start()
    t.join()
    ui_mirror.set_current_task(None)
    logger.info("B 結束後的無歸屬記錄")  # 退回最近綁定且仍存在的 A

    assert _texts(b) == ["B 執行中"]
    assert _texts(a) == ["B 結束後的無歸屬記錄"]


def test_unbinding_from_a_taskless_thread_only_prunes_finished_sessions(routed):
    handler, _ = routed
    running, finished = TaskSession(name="run"), TaskSession(name="fin")
    running.start()
    finished.start()
    finished.finish()
    handler.set_session(running)
    handler.set_session(finished)

    ui_mirror.set_current_task(None)  # 例如 generator 在別條執行緒被關閉
    handler.set_session(None)

    assert running.task_id in handler._sessions
    assert finished.task_id not in handler._sessions
    assert handler._session is running


def test_mirrored_records_are_still_skipped(routed):
    handler, logger = routed
    session = TaskSession(name="S")
    handler.set_session(session)
    logger.info("鏡像", extra={"ui_mirrored": True})
    assert _texts(session) == []


def test_two_real_merges_in_parallel_keep_their_ui_logs_separate(tmp_path):
    """真實服務：兩個合併同時跑（共用全域 UI_LOG_HANDLER），各自畫面只出現自己的模組。"""
    sessions = {}
    errors = []

    def make_input(root, prefix):
        for i in range(6):
            lang = root / "assets" / f"{prefix}{i}" / "lang"
            lang.mkdir(parents=True)
            (lang / "zh_cn.json").write_text(
                json.dumps({"k": "值"}, ensure_ascii=False), encoding="utf-8"
            )
            (lang / "en_us.json").write_text(json.dumps({"k": "v"}), encoding="utf-8")
        return root

    gate = threading.Barrier(2)

    def run(name, prefix):
        try:
            inp = make_input(tmp_path / name / "in", prefix)
            session = TaskSession(name=name)
            session.start()
            sessions[name] = session
            gate.wait(timeout=10)  # 兩個任務同時開始
            for _ in run_merge_folder_batch_service(
                str(inp), str(tmp_path / name / "out"), session, only_process_lang=True
            ):
                pass
        except Exception as exc:  # noqa: BLE001 - 收集後在主執行緒斷言
            errors.append(exc)

    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    try:
        threads = [
            threading.Thread(target=run, args=("甲", "alpha")),
            threading.Thread(target=run, args=("乙", "beta")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        root.setLevel(previous)

    assert not errors, errors
    texts_a = "\n".join(_texts(sessions["甲"]))
    texts_b = "\n".join(_texts(sessions["乙"]))
    assert "alpha" in texts_a and "beta" not in texts_a
    assert "beta" in texts_b and "alpha" not in texts_b
