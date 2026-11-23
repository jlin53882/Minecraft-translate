"""共同的 FTB / KubeJS / MD 翻譯流程骨架。

插件只提供格式特定的 callbacks；批次、cache、flush、取消與狀態判定
統一交給 ``translate_items_with_cache_loop``。這個 façade 讓三個 plugin
使用同一個明確的流程 contract，也避免未來新增參數時只更新其中一個 caller。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from translation_tool.core.lm_translator_shared_cache import CacheRule
from translation_tool.core.lm_translator_shared_loop import (
    TranslateLoopResult,
    translate_items_with_cache_loop,
)
from translation_tool.utils.cache_manager import (
    add_to_cache as _default_cache_add,
)
from translation_tool.utils.cache_manager import (
    save_translation_cache as _default_cache_save,
)


@dataclass(frozen=True)
class TranslatorHooks:
    """格式特定的輸出、flush、checkpoint 與進度 callback。"""

    on_translated_item: Callable[[dict[str, Any]], None] | None = None
    on_batch_flushed: Callable[[], None] | None = None
    on_batch_checkpoint: Callable[[dict[str, Any]], None] | None = None
    on_progress: Callable[[float, str, float], None] | None = None
    # Structured events are emitted once by the shared execution layer.  The
    # service/UI decides how to render them; plugin callers may leave this off.
    on_event: Callable[[dict[str, Any]], None] | None = None
    task_id: str | None = None
    run_id: str | None = None
    operation: str = "translate"
    provider: str = ""
    model: str = ""
    batch_total: int = 0
    initial_timings: dict[str, float] = field(default_factory=dict)


def prepare_translator_items(
    items: list[dict[str, Any]],
    *,
    cache_rules: dict[str, CacheRule] | None = None,
    is_valid_hit: Callable[[str, dict[str, Any], dict[str, Any]], bool] | None = None,
    cache_provider: Callable[[str], dict[str, Any]] | None = None,
    cache_fallbacks: dict[str, tuple[str, ...]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Shared cache split seam used before the common batch loop."""
    from translation_tool.core.lm_translator_shared_cache import (
        fast_split_items_by_cache,
    )

    return fast_split_items_by_cache(
        items,
        cache_rules=cache_rules,
        is_valid_hit=is_valid_hit,
        cache_provider=cache_provider,
        cache_fallbacks=cache_fallbacks,
    )


def format_eta(seconds: float) -> str:
    """Format the shared-loop ETA consistently for plugin progress callbacks."""
    if seconds <= 0:
        return ""
    minutes, remainder = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{remainder:02d}s"
    if minutes:
        return f"{minutes}m{remainder:02d}s"
    return f"{remainder}s"


def make_progress_hook(
    report_progress: Callable[[float], None],
    log_message: Callable[[str], None],
    *,
    eta_formatter: Callable[[float], str] | None = None,
    message_formatter: Callable[[str, str], str] | None = None,
) -> Callable[[float, str, float], None]:
    """Build the shared progress adapter while leaving UI/log wording to callers."""

    def on_progress(progress: float, message: str, eta_seconds: float) -> None:
        eta_text = (
            eta_formatter(eta_seconds)
            if eta_formatter is not None
            else format_eta(eta_seconds)
        )
        rendered = (
            message_formatter(message, eta_text)
            if message_formatter is not None
            else f"{message}{f' | ETA ≈ {eta_text}' if eta_text else ''}"
        )
        log_message(rendered)
        report_progress(progress)

    return on_progress


@dataclass(frozen=True)
class TranslatorSkeleton:
    """三種 translator 共用的 orchestration contract。"""

    items: list[dict[str, Any]]
    translate_batch_smart: Callable[
        [list[dict[str, Any]], int | None],
        tuple[list[dict[str, Any]] | None, str],
    ]
    total_for_smart: int | None = None
    write_new_cache: bool = True
    cache_rules: dict[str, CacheRule] | None = None
    batch_size_by_type: dict[str, int] | None = None
    sleep_seconds_between_batches: float | None = None
    reload_cache: bool = True
    cache_add: Callable[..., bool] | None = None
    cache_save: Callable[..., bool] | None = None
    fixed_input_tokens_for_batch: Callable[[list[dict[str, Any]]], float] | None = None
    hooks: TranslatorHooks = field(default_factory=TranslatorHooks)

    def run(self) -> TranslateLoopResult:
        """執行共同 loop；plugin callback 不得改變 loop 的狀態語意。"""
        return translate_items_with_cache_loop(
            self.items,
            total_for_smart=self.total_for_smart,
            translate_batch_smart=self.translate_batch_smart,
            batch_size_by_type=self.batch_size_by_type,
            write_new_cache=self.write_new_cache,
            on_translated_item=self.hooks.on_translated_item,
            on_batch_flushed=self.hooks.on_batch_flushed,
            on_batch_checkpoint=self.hooks.on_batch_checkpoint,
            on_progress=self.hooks.on_progress,
            event_sink=self.hooks.on_event,
            event_task_id=self.hooks.task_id,
            event_run_id=self.hooks.run_id,
            event_operation=self.hooks.operation,
            event_provider=self.hooks.provider,
            event_model=self.hooks.model,
            event_batch_total=self.hooks.batch_total,
            initial_timings=self.hooks.initial_timings,
            cache_rules=self.cache_rules,
            sleep_seconds_between_batches=self.sleep_seconds_between_batches,
            reload_cache=self.reload_cache,
            cache_add=self.cache_add or _default_cache_add,
            cache_save=self.cache_save or _default_cache_save,
            fixed_input_tokens_for_batch=self.fixed_input_tokens_for_batch,
        )


def run_translator_skeleton(
    items: list[dict[str, Any]],
    *,
    translate_batch_smart: Callable[
        [list[dict[str, Any]], int | None],
        tuple[list[dict[str, Any]] | None, str],
    ],
    total_for_smart: int | None = None,
    write_new_cache: bool = True,
    cache_rules: dict[str, CacheRule] | None = None,
    batch_size_by_type: dict[str, int] | None = None,
    sleep_seconds_between_batches: float | None = None,
    reload_cache: bool = True,
    cache_add: Callable[..., bool] | None = None,
    cache_save: Callable[..., bool] | None = None,
    fixed_input_tokens_for_batch: Callable[[list[dict[str, Any]]], float] | None = None,
    hooks: TranslatorHooks | None = None,
) -> TranslateLoopResult:
    """以函式介面執行 skeleton，供 plugin 保持簡潔且容易 mock。"""
    return TranslatorSkeleton(
        items=items,
        translate_batch_smart=translate_batch_smart,
        total_for_smart=total_for_smart,
        write_new_cache=write_new_cache,
        cache_rules=cache_rules,
        batch_size_by_type=batch_size_by_type,
        sleep_seconds_between_batches=sleep_seconds_between_batches,
        reload_cache=reload_cache,
        cache_add=cache_add,
        cache_save=cache_save,
        fixed_input_tokens_for_batch=fixed_input_tokens_for_batch,
        hooks=hooks or TranslatorHooks(),
    ).run()
