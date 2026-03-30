"""tests/test_logging_presenter.py

PR1：Logging Core Foundation — LogPresenter 單元測試。
"""

from __future__ import annotations

from app.logging.log_presenter import LogPresenter
from app.logging.task_session import TaskSession


class MockListView:
    def __init__(self):
        self.controls = []
    def scroll_to(self, **kw):
        pass


class TestLogPresenterAppendMode:
    """Append 模式：每次只渲染新增的 entries。"""

    def test_append_mode_renders_all_first_time(self):
        """首次 sync（_last_seq=-1）應渲染所有 entries，含 seq=0。"""
        s = TaskSession(max_logs=100)
        p = LogPresenter(mode="append")
        p.reset()  # reset() 將 _last_seq 設為 -1
        s.add_log("line0")
        s.add_log("line1")
        s.add_log("line2")
        lv = MockListView()
        new = p.sync(lv, s.snapshot()["logs"])
        # _last_seq=-1，所以 seq=[0,1,2] 全部大於-1 → 3條全部渲染
        assert len(new) == 3
        assert len(lv.controls) == 3

    def test_append_mode_skips_existing(self):
        """同一 session 的第二次 sync 只渲染新 entries。"""
        s = TaskSession(max_logs=100)
        p = LogPresenter(mode="append")
        p.reset()  # 确保干净状态
        s.add_log("line0")
        s.add_log("line1")
        lv = MockListView()
        p.sync(lv, s.snapshot()["logs"])

        s.add_log("line2")
        s.add_log("line3")
        new = p.sync(lv, s.snapshot()["logs"])
        assert len(new) == 2  # 只新增2條
        assert len(lv.controls) == 4  # 累積4條

    def test_append_mode_truncate_preserves_controls(self):
        """append 到超過 max_ui_lines 時， presenter._truncate 截斷最舊的。"""
        s = TaskSession(max_logs=1000)
        p = LogPresenter(mode="append", max_ui_lines=3)
        p.reset()
        for i in range(5):
            s.add_log(f"line{i}")
        lv = MockListView()
        p.sync(lv, s.snapshot()["logs"])
        # presenter._truncate 截斷到 max_ui_lines=3
        assert len(lv.controls) == 3

    def test_append_mode_reset_clears_seq(self):
        """reset() 後再 sync 會重新渲染所有 entries。"""
        s = TaskSession(max_logs=100)
        p = LogPresenter(mode="append")
        p.reset()
        s.add_log("line0")
        lv = MockListView()
        p.sync(lv, s.snapshot()["logs"])
        assert len(lv.controls) == 1

        p.reset()
        new = p.sync(lv, s.snapshot()["logs"])
        assert len(new) == 1  # reset 後再 sync，seq 追蹤重置，視為全新


class TestLogPresenterTailMode:
    """Tail 模式：每次只渲染最後 N 筆。"""

    def test_tail_mode_shows_last_n(self):
        """tail 模式只渲染最後 tail_lines 筆。"""
        s = TaskSession(max_logs=100)
        p = LogPresenter(mode="tail", tail_lines=3)
        for i in range(10):
            s.add_log(f"line{i}")
        lv = MockListView()
        p.sync(lv, s.snapshot()["logs"])
        assert len(lv.controls) == 3
        # 最後3條應是 line7, line8, line9
        texts = [c.value for c in lv.controls]
        assert texts == ["line7", "line8", "line9"]

    def test_tail_mode_replaces_all_each_sync(self):
        """tail 模式每次 sync 全量替換 controls。"""
        s = TaskSession(max_logs=100)
        p = LogPresenter(mode="tail", tail_lines=3)
        for i in range(5):
            s.add_log(f"line{i}")
        lv = MockListView()
        p.sync(lv, s.snapshot()["logs"])
        assert len(lv.controls) == 3

        # 再加2條，tail 應替換成 line5, line6, line7
        s.add_log("line5")
        s.add_log("line6")
        s.add_log("line7")
        p.sync(lv, s.snapshot()["logs"])
        texts = [c.value for c in lv.controls]
        assert texts == ["line5", "line6", "line7"]


class TestLogPresenterLevelFilter:
    """Level 過濾：根據 show_levels 過濾。"""

    def test_show_levels_filter(self):
        """show_levels 只允許設定的等級通過。"""
        s = TaskSession(max_logs=100)
        p = LogPresenter(mode="append", show_levels=["error"])
        s.add_log("info msg", "info")
        s.add_log("error msg", "error")
        s.add_log("warn msg", "warning")
        lv = MockListView()
        new = p.sync(lv, s.snapshot()["logs"])
        assert len(new) == 1
        assert lv.controls[0].value == "error msg"
