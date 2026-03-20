"""tests/test_logging_core.py

PR1：Logging Core Foundation — TaskSession 單元測試。
"""

from __future__ import annotations

import pytest
from app.logging.task_session import TaskSession
from app.logging.log_entry import LogEntry


class TestTaskSessionLogEntry:
    """驗證 TaskSession 產出 LogEntry。"""

    def test_add_log_produces_log_entry(self):
        """add_log 後 snapshot 的 logs 內應為 LogEntry。"""
        s = TaskSession(max_logs=100)
        s.add_log("hello", "info", "test")
        snap = s.snapshot()
        assert len(snap["logs"]) == 1
        assert isinstance(snap["logs"][0], LogEntry)

    def test_add_log_auto_seq(self):
        """每次 add_log 序號應遞增。"""
        s = TaskSession(max_logs=100)
        s.add_log("a")
        s.add_log("b")
        s.add_log("c")
        snap = s.snapshot()
        seqs = [e.seq for e in snap["logs"]]
        assert seqs == [0, 1, 2]

    def test_add_log_level_source(self):
        """add_log 可傳入 level 與 source。"""
        s = TaskSession()
        s.add_log("warn msg", "warning", "merge")
        snap = s.snapshot()
        entry = snap["logs"][0]
        assert entry.level == "warning"
        assert entry.source == "merge"

    def test_add_log_backward_compat(self):
        """只傳 text 時可正常運作（level=info, source=ui）。"""
        s = TaskSession()
        s.add_log("plain text")
        snap = s.snapshot()
        entry = snap["logs"][0]
        assert entry.level == "info"
        assert entry.source == "ui"

    def test_deque_maxlen_respected(self):
        """deque 達到上限後自動淘汰最舊的。"""
        s = TaskSession(max_logs=3)
        s.add_log("a")
        s.add_log("b")
        s.add_log("c")
        s.add_log("d")  # 觸發淘汰
        snap = s.snapshot()
        seqs = [e.seq for e in snap["logs"]]
        # 最舊的 seq=0 應被淘汰
        assert 0 not in seqs
        assert seqs == [1, 2, 3]

    def test_start_clears_and_resets_seq(self):
        """start() 應清空 logs 並重置序號。"""
        s = TaskSession(max_logs=10)
        s.add_log("a")
        s.add_log("b")
        s.start()
        snap = s.snapshot()
        assert len(snap["logs"]) == 0
        # 新增應該從 seq=0 重新開始
        s.add_log("c")
        snap = s.snapshot()
        assert snap["logs"][0].seq == 0

    def test_snapshot_returns_copy(self):
        """snapshot() 回傳的是新 list，不是內部 deque 引用。"""
        s = TaskSession()
        s.add_log("a")
        snap1 = s.snapshot()
        snap2 = s.snapshot()
        assert snap1 is not snap2
        assert snap1["logs"] is not snap2["logs"]

    def test_progress_clamp(self):
        """set_progress 超界時自動 clamp。"""
        s = TaskSession()
        s.set_progress(1.5)
        assert s.progress == 1.0
        s.set_progress(-0.5)
        assert s.progress == 0.0
