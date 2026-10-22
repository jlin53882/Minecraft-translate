"""``os.replace`` 的暫時性拒絕重試：OneDrive／防毒掃描剛寫完的檔案時，Windows 會短暫回 WinError 5/32。

另提供 ``atomic_write_text``：checkpoint／續跑標記這類「被中斷時要嘛舊內容、要嘛完整新內容」的 JSON 寫入。
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from translation_tool.utils.fs_utils import fsync_directory

_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def replace_with_retry(source: str | Path, target: str | Path) -> None:
    """與 ``os.replace`` 相同，但遇到 ``PermissionError`` 會短暫退避重試，仍失敗才拋出。"""
    for delay in _RETRY_DELAYS:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(source, target)


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """原子且 durable 地寫入文字檔：同資料夾唯一暫存檔 fsync → ``replace_with_retry`` → fsync 目錄。

    - 暫存檔名唯一（``tempfile.mkstemp``），不會與其他寫入者的固定 ``.tmp`` 檔互相覆蓋。
    - 任何一步失敗都保留目的地的舊檔，並清掉自己的暫存檔後重新拋出。
    - 目的地被 Windows 暫時鎖住時由 ``replace_with_retry`` 退避重試。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    fsync_directory(target.parent)
