import threading

from translation_tool.utils.log_unit import log_warning, log_debug
from pathlib import Path
from typing import Any

import flet as ft

from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.core.jar_processor import (
        extract_lang_files_generator,
        extract_book_files_generator,
        extract_dual_files_generator,
    )
from app.services_impl.pipelines.extract_service import (
    _select_extraction_generator,
    prepare_preview_paths,
)
from app.views.extractor.extractor_state import PreviewState

def update_stats_from_log(view, line: str, phase: str = None):
    """解析日誌行更新提取統計。

    規則：
    - 只在明確的「最終摘要」日誌上覆蓋 success / warnings / total_files
    - dual mode 時寫入對應 phase 的 sub-dict
    """
    stats = view._extraction_stats
    # 只有 phase 存在於 stats 時才 sub-dict 寫入；否則直接寫頂層 stats。
    # 避免 phase 名稱拼錯時意外覆寫頂層 stats 整個 dict。
    target = stats[phase] if phase is not None and phase in stats else stats
    try:
        import re

        final_match = re.search(
            r'已檢查\s+(\d+)/(\d+)\s+個\s+JAR\s+檔案。\s*\n\s*-\s*新提取或更新的檔案:\s*(\d+)\s+個\s*\n\s*-\s*因內容相同而跳過的檔案:\s*(\d+)\s+個',
            line,
            re.MULTILINE,
        )
        if final_match:
            target['success'] = int(final_match.group(1))
            target['warnings'] = int(final_match.group(4))
            target['failures'] = 0
            target['total_files'] = int(final_match.group(3))
            return

        error_match = re.search(r'提取\s+(.+?)\s+時產生例外', line)
        if error_match or '[ERROR]' in line or '[致命錯誤]' in line:
            target['failures'] = target.get('failures', 0) + 1
    except Exception as e:
        log_warning(f'解析統計數字失敗: {e}')

