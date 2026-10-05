"""重開後續跑的 service 層（#151）：View／外殼只經由這裡取用引擎的續跑判斷。

函式都不呼叫翻譯 API；``check_resume`` 會讀取輸入資料夾，請在背景執行緒呼叫。
"""

from __future__ import annotations

from translation_tool.core.lm_resume import (
    InterruptedTask,
    ResumeCheck,
    check_resume_feasibility,
    discard_interrupted_task,
    peek_interrupted_task,
)

__all__ = [
    "InterruptedTask",
    "ResumeCheck",
    "check_resume",
    "discard_interrupted",
    "find_interrupted_task",
]


def find_interrupted_task() -> InterruptedTask | None:
    """上次未完成的機器翻譯（沒有則 None）。只讀一個小 JSON，可在 UI 執行緒呼叫。"""
    return peek_interrupted_task()


def check_resume(task: InterruptedTask) -> ResumeCheck:
    """確認輸入與上次相同、可續跑。會掃描並讀取輸入檔案，請在背景執行緒呼叫。"""
    return check_resume_feasibility(task)


def discard_interrupted() -> None:
    """放棄上次的任務（清除標記，不續跑）。"""
    discard_interrupted_task()
