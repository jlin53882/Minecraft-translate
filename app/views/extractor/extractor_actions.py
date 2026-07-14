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

# 🐛 2026-07-14 user review: 物理刪除 update_stats_from_log 函式
# 4 層 grep 確認:production code 零 caller(Phase 3 partial 刪除
# _update_stats_from_log wrapper 後,_extraction_stats 從未被 production 寫入)。
# view._extraction_stats 沒有 lang/book sub-dict(只有 4 個頂層 key),
# phase="lang"/"book" 永遠走 "else stats" branch(line 31),實際不寫 sub-dict。
# 對應測試 test_extractor_dual_mode.py TestUpdateStatsFromLog 4 條
# 跟 test_extractor_view_characterization.py 2 條 一併物理刪除。