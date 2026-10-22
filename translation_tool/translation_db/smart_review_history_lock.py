"""智慧校對歷史目錄的程序內鎖與跨程序檔案鎖。

``HistoryLock`` 負責「誰能同時改這個歷史目錄」：同一程序內每個目錄共用一把
``RLock``，跨程序以 ``.smart_review.lock`` 檔案鎖互斥；同一執行緒巢狀取得時
重用已持有的檔案鎖，取得逾時會丟出 ``TimeoutError``。它不知道歷史檔案的內容。
"""

from __future__ import annotations

import errno
import os
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import ClassVar

_FILE_LOCK_TIMEOUT_SECONDS = 120.0
_FILE_LOCK_POLL_SECONDS = 0.1


@contextmanager
def _exclusive_file_lock(
    path: Path,
    *,
    blocking: bool = True,
    timeout_seconds: float = _FILE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """以具明確等待上限的跨程序檔案鎖保護歷史檔案更新。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        locked = False
        try:
            deadline = (
                time.monotonic() + max(0.0, float(timeout_seconds))
                if blocking
                else None
            )
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        flags = fcntl.LOCK_EX | fcntl.LOCK_NB
                        fcntl.flock(handle.fileno(), flags)
                except OSError as exc:
                    if not blocking or not _is_lock_contention_error(exc):
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"取得智慧校對歷史檔案鎖逾時：{path}"
                        ) from exc
                    time.sleep(_FILE_LOCK_POLL_SECONDS)
                    continue
                break
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_lock_contention_error(exc: OSError) -> bool:
    """判斷例外是否代表鎖被其他程序占用，而非真正的 I/O 錯誤。"""
    return getattr(exc, "errno", None) in {
        errno.EACCES,
        errno.EAGAIN,
        errno.EDEADLK,
    } or getattr(exc, "winerror", None) in {32, 33, 1224}


class HistoryLock:
    """同一歷史目錄的程序內鎖與跨程序檔案鎖。"""

    guard = threading.Lock()
    # 歷史目錄 -> 本程序內共用的 RLock
    process_locks: ClassVar[dict[Path, threading.RLock]] = {}
    # 同一執行緒內巢狀呼叫寫入方法時，共用已取得的跨程序鎖。
    interprocess_state: ClassVar[threading.local] = threading.local()

    def __init__(self, directory: Path) -> None:
        """綁定歷史目錄，並取得該目錄在本程序共用的 ``RLock``。"""
        self.directory = directory
        with self.guard:
            self.process_lock = self.process_locks.setdefault(
                directory, threading.RLock()
            )

    @contextmanager
    def interprocess(self) -> Iterator[None]:
        """鎖定歷史目錄，並允許同一執行緒內的巢狀寫入重用鎖。"""
        lock_path = self.directory / ".smart_review.lock"
        active_locks = getattr(self.interprocess_state, "locks", None)
        if active_locks is None:
            active_locks = {}
            self.interprocess_state.locks = active_locks
        if lock_path in active_locks:
            active_locks[lock_path] += 1
            try:
                yield
            finally:
                depth = active_locks[lock_path] - 1
                if depth:
                    active_locks[lock_path] = depth
                else:
                    del active_locks[lock_path]
            return

        with _exclusive_file_lock(lock_path):
            active_locks[lock_path] = 1
            try:
                yield
            finally:
                active_locks.pop(lock_path, None)

    @contextmanager
    def write(self) -> Iterator[None]:
        """同時鎖定程序內與跨程序寫入，保護完整 read-modify-write 交易。"""
        with self.process_lock, self.interprocess():
            yield

    @contextmanager
    def try_interprocess(self) -> Iterator[bool]:
        """嘗試取得非阻塞歷史目錄鎖，供一般讀取路徑略過復原工作。"""
        stack = ExitStack()
        try:
            try:
                stack.enter_context(
                    _exclusive_file_lock(
                        self.directory / ".smart_review.lock", blocking=False
                    )
                )
            except OSError:
                yield False
                return
            yield True
        finally:
            stack.close()

    @contextmanager
    def recovery_interprocess(self, *, force: bool) -> Iterator[bool]:
        """依復原模式取得必要或非阻塞的跨程序鎖。"""
        if force:
            with self.interprocess():
                yield True
            return
        with self.try_interprocess() as acquired:
            yield acquired
