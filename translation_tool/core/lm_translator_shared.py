"""translation_tool/core/lm_translator_shared.py 模組。

用途：作為 lm_translator shared layer 的相容 façade，集中 re-export 給既有 caller 使用。
維護注意：真正實作已拆到 lm_translator_shared_* 子模組；新功能請優先落在子模組。
"""

from translation_tool.core.common_batch_executor import (
    BatchEvent,
    BatchHandle,
    BatchMetrics,
    BatchPolicy,
    CommonBatchExecutor,
    classify_provider_error,
    format_batch_event,
)
from translation_tool.core.lm_translator_shared_cache import (
    STRICT_SRC_TYPES,
    CacheRule,
    ValidHitFn,
    _is_valid_hit,
    cache_storage_text,
    fast_split_items_by_cache,
    get_default_cache_rules,
    restore_cache_hit_text,
    safe_output_text,
)
from translation_tool.core.lm_translator_shared_loop import (
    TranslateLoopResult,
    _get_default_batch_size,
    invoke_translation_batch,
    translate_items_with_cache_loop,
)
from translation_tool.core.lm_translator_shared_preview import (
    TouchSet,
    strip_runtime_fields,
    write_cache_hit_preview,
    write_dry_run_preview,
)
from translation_tool.core.lm_translator_shared_recording import TranslationRecorder
from translation_tool.core.lm_translator_skeleton import (
    JsonCheckpointAdapter,
    TranslatorHooks,
    TranslatorSkeleton,
    format_eta,
    make_checkpoint_adapter,
    make_progress_hook,
    prepare_translator_items,
    run_translator_skeleton,
)

__all__ = [
    "STRICT_SRC_TYPES",
    "BatchEvent",
    "BatchHandle",
    "BatchMetrics",
    "BatchPolicy",
    "CacheRule",
    "CommonBatchExecutor",
    "JsonCheckpointAdapter",
    "TouchSet",
    "TranslateLoopResult",
    "TranslationRecorder",
    "TranslatorHooks",
    "TranslatorSkeleton",
    "ValidHitFn",
    "_get_default_batch_size",
    "_is_valid_hit",
    "cache_storage_text",
    "classify_provider_error",
    "fast_split_items_by_cache",
    "format_batch_event",
    "format_eta",
    "get_default_cache_rules",
    "invoke_translation_batch",
    "make_checkpoint_adapter",
    "make_progress_hook",
    "prepare_translator_items",
    "restore_cache_hit_text",
    "run_translator_skeleton",
    "safe_output_text",
    "strip_runtime_fields",
    "translate_items_with_cache_loop",
    "write_cache_hit_preview",
    "write_dry_run_preview",
]
