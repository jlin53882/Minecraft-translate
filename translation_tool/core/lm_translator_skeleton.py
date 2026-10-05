"""共同的 FTB / KubeJS / MD 翻譯流程骨架。

插件只提供格式特定的 callbacks；批次、cache、flush、取消與狀態判定
統一交給 ``translate_items_with_cache_loop``。這個 façade 讓三個 plugin
使用同一個明確的流程 contract，也避免未來新增參數時只更新其中一個 caller。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.core.lm_translator_shared_cache import CacheRule
from translation_tool.core.lm_translator_shared_loop import (
    TranslateLoopResult,
    translate_items_with_cache_loop,
)
from translation_tool.utils.app_paths import get_data_root
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


def prepare_translator_items(
    items: list[dict[str, Any]],
    *,
    cache_rules: dict[str, CacheRule] | None = None,
    is_valid_hit: Callable[[str, dict[str, Any], dict[str, Any]], bool] | None = None,
    cache_provider: Callable[[str], dict[str, Any]] | None = None,
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
    )


@dataclass
class JsonCheckpointAdapter:
    """Minimal durable batch checkpoint used by each plugin adapter."""

    plugin: str
    fingerprint: str
    target: str = ""
    path: Path | None = None

    def __post_init__(self) -> None:
        if self.path is None:
            # 一律走資料根目錄（#137），不依賴執行時的工作目錄（#162）
            self.path = (
                get_data_root() / "logs" / f"translator_{self.plugin}_checkpoint.json"
            )

    def __call__(self, state: dict[str, Any]) -> None:
        """Atomically and durably persist the last completed batch boundary.

        fsync 後才 replace：打包 exe 關閉視窗不保證執行清理，不能依賴關閉流程補寫。
        """
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "plugin": self.plugin,
            "fingerprint": self.fingerprint,
            "target": self.target,
            "cache_type": state.get("cache_type"),
            "processed": int(state.get("processed") or 0),
            "total": int(state.get("total") or 0),
            "completed_calls": int(state.get("completed_calls") or 0),
            "status": state.get("status"),
        }
        temporary = self.path.with_suffix(".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(self.path)

    def clear(self) -> None:
        """Remove a completed checkpoint; failed/cancelled tasks retain it."""
        if self.path is not None and self.path.exists():
            self.path.unlink()


def make_checkpoint_adapter(
    plugin: str,
    items: list[dict[str, Any]],
    *,
    target: str = "",
) -> JsonCheckpointAdapter:
    """Create a deterministic adapter without persisting full source content."""
    digest = hashlib.sha256()
    for item in items:
        digest.update(
            "\x1f".join(
                str(item.get(field, ""))
                for field in ("file", "path", "source_text", "text")
            ).encode("utf-8")
        )
        digest.update(b"\x1e")
    return JsonCheckpointAdapter(plugin, digest.hexdigest(), target=target)


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
            cache_rules=self.cache_rules,
            sleep_seconds_between_batches=self.sleep_seconds_between_batches,
            reload_cache=self.reload_cache,
            cache_add=self.cache_add or _default_cache_add,
            cache_save=self.cache_save or _default_cache_save,
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
        hooks=hooks or TranslatorHooks(),
    ).run()
