"""app.tasks.task_session 單元測試

測試目標：TaskSession 類別的執行緒安全與狀態管理功能。
"""

import threading

from app.tasks.task_session import TaskSession


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
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(session.logs) > 0


def test_finish_keeps_error_status():
    """set_error() 之後的 finish()（常見於 finally）不可把狀態蓋成 DONE。"""
    from app.tasks.task_session import TaskSession

    session = TaskSession()
    session.start()
    session.set_error()
    session.finish()

    assert session.status == "ERROR"
    assert session.progress == 1.0


def test_start_logs_survive_start_and_keep_their_order():
    """開頭訊息（add_start_log）立刻可見，``start()`` 清空日誌後會放回且排在最前面。"""
    from app.tasks.task_session import TaskSession

    session = TaskSession()
    session.add_start_log("提示", "info")
    assert [e.text for e in session.snapshot()["logs"]] == ["提示"]

    session.start()
    session.add_log("work")
    texts = [e.text for e in session.snapshot()["logs"]]
    assert texts == ["提示", "work"]
    assert [e.seq for e in session.snapshot()["logs"]] == [0, 1]


def test_pre_start_log_and_lifecycle_records_share_one_execution_id(caplog):
    """add_start_log 在 start() 前寫入：後台記錄必須掛在「這次執行」的 task_id，不是舊的。"""
    import logging

    from app.tasks.task_session import TaskSession
    from translation_tool.utils import ui_mirror

    ui_mirror.install_task_record_factory()
    session = TaskSession(name="機器翻譯")
    id_before_start = session.task_id
    with caplog.at_level(logging.INFO):
        session.add_start_log("[資訊] 未指定輸出，使用預設")
        session.start()
        session.finish()

    mine = [r for r in caplog.records if getattr(r, "task_name", None) == "機器翻譯"]
    by_text = {r.getMessage(): r.task_id for r in mine}
    assert session.task_id != id_before_start  # start() 確實換了識別
    assert by_text["[資訊] 未指定輸出，使用預設"] == session.task_id
    assert by_text["任務開始"] == session.task_id
    assert {r.task_id for r in mine} == {session.task_id}  # 沒有任何記錄掛在舊識別上
    # 後台只出現一次
    assert [r.getMessage() for r in mine].count("[資訊] 未指定輸出，使用預設") == 1


def test_start_log_added_while_running_is_mirrored_with_the_current_id(caplog):
    import logging

    from app.tasks.task_session import TaskSession
    from translation_tool.utils import ui_mirror

    ui_mirror.install_task_record_factory()
    session = TaskSession(name="執行中")
    session.start()
    with caplog.at_level(logging.INFO):
        session.add_start_log("執行中才加入的開頭訊息")

    rec = [r for r in caplog.records if "執行中才加入" in r.getMessage()]
    assert len(rec) == 1 and rec[0].task_id == session.task_id


def test_start_clears_the_previous_runs_summary():
    """重用同一個 session：第二次執行（很早就取消／例外）不能帶著第一次的摘要。"""
    from app.tasks.task_session import TaskSession

    session = TaskSession(name="合併")
    session.start()
    session.set_summary({"success_folders": 1})
    session.finish()
    assert session.snapshot()["summary"] == {"success_folders": 1}

    session.start()  # 第二次：還沒 set_summary 就結束

    assert session.snapshot()["summary"] is None


def test_second_run_lifecycle_log_does_not_repeat_the_first_runs_summary(caplog):
    import logging

    from app.tasks.task_session import TaskSession
    from translation_tool.utils import ui_mirror

    ui_mirror.install_task_record_factory()
    session = TaskSession(name="合併")
    session.start()
    session.set_summary({"marker": "第一次的結果"})
    session.finish()

    with caplog.at_level(logging.INFO):
        session.start()
        session.finish()  # 第二次沒有任何摘要

    ends = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("任務結束")
    ]
    assert ends and "第一次的結果" not in ends[-1]


def test_new_session_has_no_summary():
    from app.tasks.task_session import TaskSession

    assert TaskSession().summary is None
