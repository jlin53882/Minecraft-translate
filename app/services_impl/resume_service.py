"""重開後續跑的 service 層（#151）：View／外殼只經由這裡取用引擎的續跑判斷。

函式都不呼叫翻譯 API；``check_resume`` 會讀取輸入資料夾，請在背景執行緒呼叫。
"""

from __future__ import annotations

from translation_tool.core.lm_resume import (
    InterruptedTask,
    ResumeCheck,
    check_resume_feasibility,
    discard_interrupted_task,
    peek_interrupted_tasks,
    purge_legacy_plugin_markers,
)

__all__ = [
    "InterruptedTask",
    "ResumeCheck",
    "check_resume",
    "discard_interrupted",
    "find_interrupted_tasks",
]


def find_interrupted_tasks() -> list[InterruptedTask]:
    """上次未完成的翻譯任務（機器翻譯、Markdown；沒有則空）。

    啟動時先清除無法安全續跑的舊格式標記，之後只讀幾個小 JSON。
    """
    purge_legacy_plugin_markers()
    return peek_interrupted_tasks()


def check_resume(task: InterruptedTask) -> ResumeCheck:
    """確認輸入與上次相同、可續跑。會掃描並讀取輸入檔案，請在背景執行緒呼叫。"""
    return check_resume_feasibility(task)


def discard_interrupted(task: InterruptedTask | None = None) -> None:
    """放棄上次的任務（清除標記，不續跑）。"""
    discard_interrupted_task(task)
