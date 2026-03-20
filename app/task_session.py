"""app/task_session.py

TaskSession 是 UI 與背景工作執行緒之間的「任務狀態容器」。

維護注意：
- 本檔案保留作為 backward compatibility shim。
- 請優先使用 app.logging.task_session.TaskSession（新板）。
"""

from app.logging.task_session import TaskSession

__all__ = ["TaskSession"]
