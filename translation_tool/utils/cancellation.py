"""translation_tool/utils/cancellation.py 模組。

用途：讓長時間任務可以在檢查點中途取消。

service 層在工作執行緒上以 cancel_scope() 註冊「是否已要求取消」的檢查函式，
深層的翻譯迴圈只需呼叫 is_cancelled() / raise_if_cancelled() /
interruptible_sleep()，不必把 session 一路傳進各個 plugin。

TaskCancelled 繼承 BaseException：翻譯流程中有許多 ``except Exception``
（例如 429 解析失敗的備援），取消訊號不能被它們吞掉；只在 service 與翻譯迴圈
這幾個明確的地方攔截。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_cancel_check: ContextVar[Callable[[], bool] | None] = ContextVar(
    "translation_cancel_check", default=None
)


class TaskCancelled(BaseException):
    """使用者要求取消任務。"""


@contextmanager
def cancel_scope(check: Callable[[], bool]) -> Iterator[None]:
    """在目前 context 註冊取消檢查函式。

    可巢狀：內層與外層任一要求取消都算取消（例如一鍵流水線的取消，
    傳到其中一個步驟 service 自己註冊的 scope 裡也有效）；離開時恢復外層。
    ContextVar 會隨 ContextThreadPoolExecutor 複製到 worker，讓平行工作中的
    cancellation checkpoint 也能觀察到同一個操作的取消狀態。
    """
    previous = _cancel_check.get()
    effective_check = (
        check if previous is None else lambda: bool(check()) or bool(previous())
    )
    token = _cancel_check.set(effective_check)
    try:
        yield
    finally:
        _cancel_check.reset(token)


def is_cancelled() -> bool:
    """目前執行緒的任務是否已被要求取消。"""
    check = _cancel_check.get()
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001 - 檢查函式出錯時視為未取消
        return False


def raise_if_cancelled() -> None:
    """已要求取消時拋出 TaskCancelled。"""
    if is_cancelled():
        raise TaskCancelled()


def interruptible_sleep(seconds: float, step: float = 0.2) -> None:
    """可被取消打斷的 sleep（例如等待 API 限流時）。"""
    seconds = max(0.0, float(seconds))
    if _cancel_check.get() is None:
        # 沒有可取消的任務：一般 sleep
        time.sleep(seconds)
        return
    end = time.monotonic() + seconds
    while True:
        raise_if_cancelled()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(step, remaining))
