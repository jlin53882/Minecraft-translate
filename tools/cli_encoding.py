"""統一命令列工具的標準輸出編碼。"""

from __future__ import annotations

import sys


def configure_utf8_stdio() -> None:
    """讓管線與 Windows 主控台都以 UTF-8 輸出，避免中文說明被誤解碼。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # 已被外部 runner 取代的 stream 可能不支援重新設定編碼。
            continue
