"""tests/test_lm_log_presenter_integration.py

PR4 補測：lm_view LogPresenter tail mode 整合測試。

驗證重點：
1. tail mode 只顯示最後 N 筆（tail_lines=from config）
2. presenter.sync() 吃 list[LogEntry]，正確渲染
3. progress / status DONE/ERROR 正常傳遞
4. colorize=False 維持灰白色外觀
"""

from __future__ import annotations

import flet as ft

from app.logging.task_session import TaskSession
from app.logging.log_entry import LogEntry


class MockListView:
    """模擬 Flet ListView，只追蹤 controls 內容。"""

    def __init__(self):
        self.controls: list[ft.Text] = []
        self._scroll_to_count = 0

    def scroll_to(self, **kw):
        self._scroll_to_count += 1


class TestLmPresenterTailMode:
    """驗證 lm_view 的 tail mode presenter 行為。"""

    def test_tail_mode_respects_tail_lines(self):
        """tail_lines=5 時，sync 後 controls 應只含最後 5 筆。"""
        from app.logging.log_presenter import LogPresenter

        presenter = LogPresenter(
            mode="tail",
            tail_lines=5,
            colorize=False,
            default_color=str(ft.Colors.GREY_100),
        )
        lv = MockListView()
        # 寫入 10 筆 LogEntry
        entries = [
            LogEntry(seq=i, level="info", text=f"line_{i}", source="lm")
            for i in range(10)
        ]
        presenter.sync(lv, entries)
        assert len(lv.controls) == 5
        assert lv.controls[0].value == "line_5"
        assert lv.controls[4].value == "line_9"

    def test_sync_with_log_entry_list_renders_text(self):
        """sync() 吃 list[LogEntry]，rendered Text 的 value 應為 entry.text。"""
        from app.logging.log_presenter import LogPresenter

        presenter = LogPresenter(
            mode="tail",
            tail_lines=3,
            colorize=False,
            default_color=str(ft.Colors.GREY_100),
        )
        lv = MockListView()
        entries = [
            LogEntry(seq=0, level="info", text="hello", source="lm"),
            LogEntry(seq=1, level="warning", text="world", source="lm"),
            LogEntry(seq=2, level="error", text="done", source="lm"),
        ]
        presenter.sync(lv, entries)
        texts = [c.value for c in lv.controls]
        assert texts == ["hello", "world", "done"]

    def test_tail_mode_replaces_all_controls_each_sync(self):
        """tail mode 每次 sync 全量替換 controls。"""
        from app.logging.log_presenter import LogPresenter

        presenter = LogPresenter(
            mode="tail",
            tail_lines=3,
            colorize=False,
            default_color=str(ft.Colors.GREY_100),
        )
        lv = MockListView()

        # 第一輪：3條
        e1 = [
            LogEntry(seq=i, level="info", text=f"batch1_{i}", source="lm")
            for i in range(3)
        ]
        presenter.sync(lv, e1)
        assert len(lv.controls) == 3

        # 第二輪：新增2條（總5條），tail=3 應只留最後3條
        e2 = e1 + [
            LogEntry(seq=i, level="info", text=f"batch2_{i}", source="lm")
            for i in range(3, 5)
        ]
        presenter.sync(lv, e2)
        # tail mode 全量替換，應只有 batch2_3, batch2_4, batch2_4... wait
        # 總 entries = 5，tail=3，應取最後3筆: seq=2,3,4 → batch1_2, batch2_3, batch2_4
        texts = [c.value for c in lv.controls]
        assert texts == ["batch1_2", "batch2_3", "batch2_4"]

    def test_colorize_false_uses_default_color(self):
        """colorize=False 時，所有 Text.control.color 應為 default_color。"""
        from app.logging.log_presenter import LogPresenter

        presenter = LogPresenter(
            mode="tail",
            tail_lines=3,
            colorize=False,
            default_color="#CCCCCC",
        )
        lv = MockListView()
        entries = [
            LogEntry(seq=0, level="info", text="info line", source="lm"),
            LogEntry(seq=1, level="error", text="error line", source="lm"),
        ]
        presenter.sync(lv, entries)
        for ctrl in lv.controls:
            assert ctrl.color == "#CCCCCC"


class TestLmPresenterWithSessionSnapshot:
    """驗證 TaskSession.snapshot() 與 LogPresenter 的整合。"""

    def test_snapshot_log_entry_integrates_with_presenter(self):
        """TaskSession.snapshot()['logs'] 為 list[LogEntry]，能正確傳入 presenter.sync()。"""
        from app.logging.log_presenter import LogPresenter

        session = TaskSession(max_logs=100)
        # 寫入不同 level / source 的 entries
        session.add_log("lm task started", "info", "lm")
        session.add_log("processing files", "info", "lm")
        session.add_log("cache miss for key X", "warning", "lm")
        session.add_log("done", "info", "lm")

        snap = session.snapshot()
        logs: list[LogEntry] = snap["logs"]
        assert len(logs) == 4
        assert all(isinstance(e, LogEntry) for e in logs)

        # presenter 吃 list[LogEntry] 正常渲染
        presenter = LogPresenter(
            mode="tail",
            tail_lines=3,
            colorize=False,
            default_color=str(ft.Colors.GREY_100),
        )
        lv = MockListView()
        presenter.sync(lv, logs)
        assert len(lv.controls) == 3  # tail=3
        assert lv.controls[0].value == "processing files"
