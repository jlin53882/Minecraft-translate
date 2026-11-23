"""取消功能：任務翻譯（FTB/KubeJS/MD 共用迴圈）與一鍵流水線。"""

from __future__ import annotations

import threading
import time

import pytest

from translation_tool.core import lm_translator_shared_loop as loop_mod
from translation_tool.utils import cancellation as c
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor

# ---------- cancellation 模組 ----------


def test_nested_scopes_combine_outer_and_inner():
    outer = {"v": False}
    inner = {"v": False}
    with c.cancel_scope(lambda: outer["v"]):
        with c.cancel_scope(lambda: inner["v"]):
            assert c.is_cancelled() is False
            outer["v"] = True  # 外層（例如流水線）取消也要傳到內層
            assert c.is_cancelled() is True
        assert c.is_cancelled() is True
    assert c.is_cancelled() is False


def test_cancel_scope_propagates_to_context_thread_pool_worker():
    cancel = threading.Event()
    worker_started = threading.Event()
    release_worker = threading.Event()

    def worker():
        worker_started.set()
        assert release_worker.wait(timeout=2)
        return c.is_cancelled()

    with (
        c.cancel_scope(cancel.is_set),
        ContextThreadPoolExecutor(max_workers=1) as executor,
    ):
        future = executor.submit(worker)
        assert worker_started.wait(timeout=2)
        cancel.set()
        release_worker.set()
        assert future.result(timeout=2) is True


def test_interruptible_sleep_stops_early_when_cancelled():
    flag = threading.Event()
    threading.Timer(0.1, flag.set).start()
    t0 = time.monotonic()
    with c.cancel_scope(flag.is_set), pytest.raises(c.TaskCancelled):
        c.interruptible_sleep(30)
    assert time.monotonic() - t0 < 2


def test_task_cancelled_is_not_swallowed_by_except_exception():
    def deep():
        try:
            raise c.TaskCancelled()
        except Exception:  # noqa: BLE001 - 模擬翻譯流程中常見的寬鬆攔截
            return "swallowed"

    with pytest.raises(c.TaskCancelled):
        deep()


# ---------- 共用翻譯迴圈 ----------


def _patch_loop(monkeypatch):
    monkeypatch.setattr(loop_mod, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(loop_mod, "add_to_cache", lambda *a, **k: None)
    monkeypatch.setattr(loop_mod, "save_translation_cache", lambda *a, **k: None)


def _items(n):
    return [
        {
            "path": f"k{i}",
            "text": f"t{i}",
            "source_text": f"t{i}",
            "cache_type": "kubejs",
        }
        for i in range(n)
    ]


def test_shared_loop_stops_between_batches(monkeypatch):
    _patch_loop(monkeypatch)
    cancel = threading.Event()
    calls = []

    def fake_translate(batch, total):
        calls.append(len(batch))
        cancel.set()  # 第一批送出後使用者按下取消
        return [dict(it, text="譯") for it in batch], "AUTO"

    with c.cancel_scope(cancel.is_set):
        res = loop_mod.translate_items_with_cache_loop(
            _items(50),
            translate_batch_smart=fake_translate,
            batch_size_by_type={"kubejs": 10},
            sleep_seconds_between_batches=0,
        )

    assert res.status == "CANCELLED"
    assert calls == [10]
    assert res.processed == 10


def test_shared_loop_cancelled_while_waiting_for_rate_limit(monkeypatch):
    _patch_loop(monkeypatch)

    def fake_translate(batch, total):
        raise c.TaskCancelled()  # interruptible_sleep 等待 429 時被取消

    with c.cancel_scope(lambda: True):
        res = loop_mod.translate_items_with_cache_loop(
            _items(5),
            translate_batch_smart=fake_translate,
            batch_size_by_type={"kubejs": 10},
            sleep_seconds_between_batches=0,
        )
    assert res.status == "CANCELLED"
