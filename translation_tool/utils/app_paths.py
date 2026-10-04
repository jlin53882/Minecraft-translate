"""可寫資料根目錄的單一決定點（#137）。

規則（依序）：
1. 環境變數 `MCT_DATA_DIR`：明確覆蓋，供測試與特殊部署使用。
2. 打包後的執行檔（PyInstaller 的 `sys.frozen`，或 Nuitka 的 `__compiled__`）：
   執行檔（exe）所在的資料夾，讓 config.json、快取、logs 等放在 exe 旁邊。
3. 原始碼執行：專案根目錄（行為與過去相同）。

不要用 `Path(__file__)` 推算可寫資料位置：打包後 `__file__` 可能指向
`_internal/` 或暫存解壓目錄，不等於使用者看到的 exe 資料夾。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DATA_DIR_ENV = "MCT_DATA_DIR"


def _is_pyinstaller() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_nuitka() -> bool:
    # Nuitka 會在每個編譯後模組注入 `__compiled__` 全域變數。
    return "__compiled__" in globals()


def is_frozen() -> bool:
    """是否為打包後的執行檔。"""
    return _is_pyinstaller() or _is_nuitka()


def _source_root() -> Path:
    # translation_tool/utils/app_paths.py → parents[2] = 專案根目錄
    return Path(__file__).resolve().parents[2]


def get_data_root() -> Path:
    """回傳可寫資料的根目錄（config.json、快取、logs 等的基準）。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).resolve()
    if _is_pyinstaller():
        return Path(sys.executable).resolve().parent
    if _is_nuitka():
        # onefile 時 __file__ 在暫存目錄；argv[0] 才是使用者啟動的 exe。
        return Path(sys.argv[0]).resolve().parent
    return _source_root()
