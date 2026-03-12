"""translation_tool/core/lm_translator_shared.py 模組。

用途：作為 lm_translator shared layer 的相容 façade，集中 re-export 給既有 caller 使用。
維護注意：真正實作已拆到 lm_translator_shared_* 子模組；新功能請優先落在子模組。
"""

from translation_tool.core.lm_translator_shared_cache import (
    CacheRule,
    STRICT_SRC_TYPES,
    ValidHitFn,
    _is_valid_hit,
    fast_split_items_by_cache,
    get_default_cache_rules,
)
from translation_tool.core.lm_translator_shared_loop import (
    TranslateLoopResult,
    _get_default_batch_size,
    translate_items_with_cache_loop,
)
from translation_tool.core.lm_translator_shared_preview import (
    TouchSet,
    write_cache_hit_preview,
    write_dry_run_preview,
)
from translation_tool.core.lm_translator_shared_recording import TranslationRecorder

__all__ = [
    "CacheRule",
    "STRICT_SRC_TYPES",
    "ValidHitFn",
    "_is_valid_hit",
    "fast_split_items_by_cache",
    "get_default_cache_rules",
    "TouchSet",
    "write_dry_run_preview",
    "write_cache_hit_preview",
    "TranslationRecorder",
    "TranslateLoopResult",
    "_get_default_batch_size",
    "translate_items_with_cache_loop",
]
