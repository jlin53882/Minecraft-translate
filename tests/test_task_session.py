"""app.task_session 單元測試

測試目標：TaskSession 類別的執行緒安全與狀態管理功能。
"""

import threading
from app.task_session import TaskSession


class TestTaskSession:
    """TaskSession 測試案例"""

    def test_init_default(self):
        """測試預設初始化"""
        session = TaskSession()
        assert session.progress == 0.0
        assert session.status == "IDLE"
        assert session.error is False
        assert len(session.logs) == 0

    def test_init_custom_max_logs(self):
        """測試自訂 max_logs"""
        session = TaskSession(max_logs=100)
        assert session.logs.maxlen == 100

    def test_set_progress_valid(self):
        """測試設定有效範圍的 progress"""
        session = TaskSession()
        session.set_progress(0.5)
        assert session.progress == 0.5

    def test_set_progress_clamp_above_max(self):
        """測試 progress 超過 1.0 會被 clamp"""
        session = TaskSession()
        session.set_progress(1.5)
        assert session.progress == 1.0

    def test_set_progress_clamp_below_min(self):
        """測試 progress 低於 0.0 會被 clamp"""
        session = TaskSession()
        session.set_progress(-0.5)
        assert session.progress == 0.0

    def test_add_log(self):
        """測試新增日誌"""
        session = TaskSession()
        session.add_log("Test log message")
        assert len(session.logs) == 1
        assert session.logs[0].text == "Test log message"

    def test_add_log_empty_ignored(self):
        """測試空日誌被忽略"""
        session = TaskSession()
        session.add_log("")
        assert len(session.logs) == 0

    def test_set_error(self):
        """測試設定錯誤狀態"""
        session = TaskSession()
        session.set_error()
        assert session.error is True
        assert session.status == "ERROR"

    def test_finish(self):
        """測試完成任務"""
        session = TaskSession()
        session.set_progress(0.8)
        session.finish()
        assert session.progress == 1.0
        assert session.status == "DONE"

    def test_start(self):
        """測試開始任務"""
        session = TaskSession()
        session.status = "DONE"
        session.error = True
        session.logs.append("Old log")

        session.start()

        assert session.progress == 0.0
        assert session.status == "RUNNING"
        assert session.error is False
        assert len(session.logs) == 0

    def test_snapshot(self):
        """測試快照回傳"""
        session = TaskSession()
        session.set_progress(0.7)
        session.add_log("Log 1")
        session.add_log("Log 2")

        snapshot = session.snapshot()

        assert snapshot["progress"] == 0.7
        assert snapshot["log_texts"] == ["Log 1", "Log 2"]
        assert snapshot["status"] == "IDLE"
        assert snapshot["error"] is False

    def test_snapshot_immutable(self):
        """測試快照是不可變的"""
        session = TaskSession()
        session.add_log("Original")

        snapshot = session.snapshot()
        snapshot["logs"].append("Modified")  # 不會影響原始

        assert len(session.logs) == 1

    def test_max_logs_limit(self):
        """測試日誌數量上限"""
        session = TaskSession(max_logs=2)
        session.add_log("Log 1")
        session.add_log("Log 2")
        session.add_log("Log 3")  # 超過上限

        assert len(session.logs) == 2
        assert "Log 1" not in session.logs  # 最早的會被移除

    def test_thread_safety(self):
        """測試執行緒安全"""
        session = TaskSession()
        errors = []

        def worker():
            try:
                for i in range(100):
                    session.set_progress(i / 100)
                    session.add_log(f"Log {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(session.logs) > 0
