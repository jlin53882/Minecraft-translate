"""檔案系統的小型共用工具（durable write 用）。"""

from __future__ import annotations

import os
from pathlib import Path


def fsync_directory(path: str | os.PathLike[str]) -> None:
    """同步目錄項目，讓 ``os.replace`` / rename 造成的目錄更新也持久化。

    只 fsync 暫存檔只保證檔案內容落盤；rename 本身若沒有同步目錄，掉電後仍可能回到
    替換前的狀態。Windows 與不支援 ``O_DIRECTORY`` 的平台沒有對應操作，直接略過。
    """
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(Path(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
