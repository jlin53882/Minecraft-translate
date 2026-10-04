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


@dataclass(frozen=True)
class TranslatorHooks:
    """格式特定的輸出、flush 與進度 callback。"""

    on_translated_item: Callable[[dict[str, Any]], None] | None = None
    on_batch_flushed: Callable[[], None] | None = None
    on_progress: Callable[[float, str, float], None] | None = None


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
            on_progress=self.hooks.on_progress,
            cache_rules=self.cache_rules,
            sleep_seconds_between_batches=self.sleep_seconds_between_batches,
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
        hooks=hooks or TranslatorHooks(),
    ).run()
