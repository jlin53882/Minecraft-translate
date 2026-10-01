"""app/tasks：背景任務的中立資料模型（不依賴 Flet / UI）。

- ``TaskSession``：長任務的狀態容器（進度、日誌、取消旗標），UI 與背景執行緒靠它溝通。
- ``LogEntry`` / ``LogLevel``：結構化日誌。

業務層（``services_impl``）與 UI 都從這裡 import；``LogView`` 等控制項留在 ``app.views._log``。
"""

from app.tasks.log_entry import LogEntry, LogLevel
from app.tasks.task_session import TaskSession, tag_session

__all__ = ["LogEntry", "LogLevel", "TaskSession", "tag_session"]
