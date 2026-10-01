"""app/shell：App 外殼（側欄、頂列、狀態列、快速跳轉、任務管理）。

外殼只負責「frame」：導覽、全域狀態與快捷鍵；各頁面的內容由 ``app.views`` 提供。
"""

from app.shell.app_shell import AppShell
from app.shell.task_manager import TaskManager

__all__ = ["AppShell", "TaskManager"]
