from __future__ import annotations

import os
import logging
from typing import List

log = logging.getLogger(__name__)

def find_jar_files(folder_path: str) -> List[str]:
    """遞迴找出資料夾下所有 .jar 檔案。"""
    jar_files: List[str] = []
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith('.jar'):
                jar_files.append(os.path.join(root, file))
    log.info("在 '%s' 中找到 %s 個 .jar 檔案。", folder_path, len(jar_files))
    return jar_files
