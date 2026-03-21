"""translation_tool/core/jar_processor_discovery.py 模組。

用途：發現與掃描 Mod JAR 檔案的功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import os
import logging
from typing import List

log = logging.getLogger(__name__)

def find_jar_files(folder_path: str) -> List[str]:
    """遞迴找出資料夾下所有 .jar 檔案。

    Args:
        folder_path: 要掃描的資料夾路徑。

    Returns:
        所有找到的 .jar 檔案之絕對路徑列表。
    """
    jar_files: List[str] = []
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith('.jar'):
                jar_files.append(os.path.join(root, file))
    log.info("在 '%s' 中找到 %s 個 .jar 檔案。", folder_path, len(jar_files))
    return jar_files
