"""``_BackendSeenTracker`` 的 occurrence 記帳、時間窗，以及 fatal exception 後台只寫一次。"""

from __future__ import annotations

import logging

import pytest

from app.services_impl.pipelines import extract_service, merge_service
from app.tasks.task_session import TaskSession, add_log_unmirrored
from translation_tool.utils import ui_mirror
from translation_tool.utils.ui_mirror import _BackendSeenTracker


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _tracker(limit=1000, window=5.0):
    clock = _Clock()
    return _BackendSeenTracker(limit=limit, window_sec=window, clock=clock), clock


def test_consumed_old_entry_eviction_does_not_forget_a_newer_same_text_entry():
    """consume 之後同文字又出現新記錄；舊的（已抵銷）記錄被淘汰時，新記錄必須仍然有效。"""
    tracker, _ = _tracker(limit=3)
    tracker.remember("A")
    assert tracker.consume("A") is True  # 舊 A 已抵銷（仍留在淘汰佇列）
    tracker.remember("A")  # 新 A：還沒被抵銷
    tracker.remember("x")
    tracker.remember("y")  # 容量 3 → 舊 A 被淘汰

    assert tracker.consume("A") is True  # 新 A 仍在
    assert tracker.consume("A") is False  # 只能抵銷一次


def test_rollover_beyond_the_limit_keeps_counts_consistent():
    tracker, _ = _tracker(limit=50)
    for i in range(500):
        tracker.remember(f"line {i}")
        if i % 2 == 0:
            assert tracker.consume(f"line {i}") is True
    # 最近 50 筆內、尚未抵銷的奇數行仍可抵銷，較舊的已被淘汰
    assert tracker.consume("line 499") is True
    assert tracker.consume("line 497") is True
    assert tracker.consume("line 1") is False


def test_unconsumed_entry_is_forgotten_when_evicted():
    tracker, _ = _tracker(limit=2)
    tracker.remember("old")
    tracker.remember("a")
    tracker.remember("b")
    assert tracker.consume("old") is False


def test_each_backend_record_excuses_exactly_one_ui_line():
    tracker, _ = _tracker()
    tracker.remember("完成")
    tracker.remember("完成")
    assert [tracker.consume("完成") for _ in range(3)] == [True, True, False]


def test_entries_older_than_the_window_cannot_excuse_a_ui_line():
    """上一個任務留下的同文字後台記錄，不能抵銷這個任務真正只有 UI 的訊息。"""
    tracker, clock = _tracker(window=5.0)
    tracker.remember("開始處理：foo.jar")
    clock.now = 4.9
    assert tracker.consume("開始處理：foo.jar") is True

    tracker.remember("開始處理：foo.jar")
    clock.now = 10.0  # 超過時間窗
    assert tracker.consume("開始處理：foo.jar") is False


def test_expired_entries_do_not_break_later_matching():
    tracker, clock = _tracker(window=5.0)
    tracker.remember("A")
    clock.now = 6.0
    tracker.remember("A")  # 新的，仍在窗內
    assert tracker.consume("A") is True
    assert tracker.consume("A") is False


def test_add_log_unmirrored_does_not_swallow_a_real_type_error():
    calls = []

    class _Session:
        def add_log(self, text, level="info", mirror=True):
            calls.append(text)
            raise TypeError("內部 bug")

    with pytest.raises(TypeError, match="內部 bug"):
        add_log_unmirrored(_Session(), "x", "error")
    assert calls == ["x"]  # 沒有因為 TypeError 被重複呼叫


def test_add_log_unmirrored_adapts_to_legacy_signatures():
    seen = []

    class _Legacy:
        def add_log(self, text):
            seen.append(("legacy", text))

    class _LevelOnly:
        def add_log(self, text, level="info"):
            seen.append((level, text))

    add_log_unmirrored(_Legacy(), "a", "error")
    add_log_unmirrored(_LevelOnly(), "b", "error")
    assert seen == [("legacy", "a"), ("error", "b")]


@pytest.fixture(autouse=True)
def _reset_global_tracker():
    ui_mirror.BACKEND_SEEN_TRACKER.clear()
    yield
    ui_mirror.BACKEND_SEEN_TRACKER.clear()


def test_lang_extraction_fatal_error_is_written_to_backend_once(monkeypatch, caplog):
    def boom(*_a, **_k):
        raise RuntimeError("提取炸了")

    monkeypatch.setattr(extract_service, "_select_extraction_generator", boom)
    session = TaskSession(name="提取")

    with caplog.at_level(logging.ERROR):
        extract_service.run_lang_extraction_service("mods", "out", session)

    fatal = [r for r in caplog.records if "Lang 檔案提取失敗" in r.getMessage()]
    assert len(fatal) == 1
    assert "RuntimeError('提取炸了')" in fatal[0].getMessage()
    assert "Traceback" in fatal[0].getMessage()
    ui = [e.text for e in session.snapshot()["logs"] if "Lang 檔案提取失敗" in e.text]
    assert len(ui) == 1


def test_fatal_folder_merge_error_is_written_to_backend_once(caplog):
    session = TaskSession(name="合併")
    stats = {
        "total_folders": 1,
        "success_folders": 0,
        "failed_folders": 0,
        "errored_files": 0,
        "failed_folders_list": [],
    }

    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError("資料夾炸了")
        except ValueError as exc:
            merge_service._fatal_folder_error(session, stats, "out", exc)

    fatal = [r for r in caplog.records if "資料夾合併失敗" in r.getMessage()]
    assert len(fatal) == 1
