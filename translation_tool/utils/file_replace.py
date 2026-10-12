"""``os.replace`` 的暫時性拒絕重試：OneDrive／防毒掃描剛寫完的檔案時，Windows 會短暫回 WinError 5/32。"""

from __future__ import annotations

import os
import time
from pathlib import Path

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
