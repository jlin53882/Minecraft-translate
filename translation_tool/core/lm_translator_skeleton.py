"""共同的 FTB / KubeJS / MD 翻譯流程骨架。

插件只提供格式特定的 callbacks；批次、cache、flush、取消與狀態判定
統一交給 ``translate_items_with_cache_loop``。這個 façade 讓三個 plugin
使用同一個明確的流程 contract，也避免未來新增參數時只更新其中一個 caller。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.core.lm_translator_shared_cache import CacheRule
from translation_tool.core.lm_translator_shared_loop import (
    TranslateLoopResult,
    translate_items_with_cache_loop,
)
from translation_tool.core.plugin_resume import active_context, marker_path
from translation_tool.utils.cache_manager import (
    add_to_cache as _default_cache_add,
)
from translation_tool.utils.cache_manager import (
    save_translation_cache as _default_cache_save,
)
from translation_tool.utils.fs_utils import fsync_directory


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
    """每批翻譯完成後的 durable checkpoint；續跑任務進行中時寫出可續跑的標記（#164）。

    - **沒有續跑任務**（CLI／直接呼叫）：寫入原本的最小資訊（舊格式，沒有人讀取），
      ``clear()`` 在迴圈完成時移除檔案。
    - **有續跑任務**（服務層以 ``plugin_resume.resume_task`` 包住整個任務）：寫出版本 2 標記
      （``kind``、輸入／輸出、選項、涵蓋全部來源檔案的指紋、已保存進度），可由 ``lm_resume``
      偵測並續跑。標記在**整個任務完成**時才由 ``resume_task`` 清除——FTB 是逐檔案翻譯，
      不能在第一個檔案完成時就清掉；這裡的 ``clear()`` 只回報「這個迴圈完成」。
    """

    plugin: str
    fingerprint: str
    target: str = ""
    path: Path | None = None
    _context: Any = field(default=None, init=False, repr=False)
    _last_processed: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.path is None:
            # 一律走資料根目錄（#137），不依賴執行時的工作目錄（#162）
            self.path = marker_path(self.plugin)
            # 只有使用預設位置時才參與續跑任務（測試或呼叫端明確指定 path 時維持原行為）
            self._context = active_context(self.plugin)
            if self._context is not None:
                self._context.loop_started()

    def _payload(self, state: dict[str, Any]) -> dict[str, Any]:
        processed = int(state.get("processed") or 0)
        total = int(state.get("total") or 0)
        payload: dict[str, Any] = {
            "plugin": self.plugin,
            "fingerprint": self.fingerprint,
            "target": self.target,
            "cache_type": state.get("cache_type"),
            "processed": processed,
            "total": total,
            "completed_calls": int(state.get("completed_calls") or 0),
            "status": state.get("status"),
        }
        ctx = self._context
        if ctx is not None:
            payload.update(
                {
                    "version": 2,
                    "kind": ctx.kind,
                    "input_dir": ctx.input_dir,
                    "output_dir": ctx.output_dir or "",
                    "options": ctx.options,
                    # 任務層級的來源指紋；單一迴圈的項目指紋改放 items_fingerprint
                    "fingerprint": ctx.fingerprint,
                    "items_fingerprint": self.fingerprint,
                    "completed_count": ctx.completed_base + processed,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            )
        return payload

    def __call__(self, state: dict[str, Any]) -> None:
        """Atomically and durably persist the last completed batch boundary.

        暫存檔 fsync → replace → fsync 目錄：後者讓 rename 本身也持久化。
        打包 exe 關閉視窗不保證執行清理，不能依賴關閉流程補寫。
        """
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._payload(state)
        self._last_processed = int(state.get("processed") or 0)
        temporary = self.path.with_suffix(".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(self.path)
        fsync_directory(self.path.parent)

    def clear(self) -> None:
        """迴圈完成（DONE）。

        有續跑任務時只回報完成（標記由任務結束時一併清除）；否則移除檔案。失敗／取消的迴圈
        不會呼叫這裡，所以標記保留。
        """
        if self._context is not None:
            self._context.loop_completed(self._last_processed)
            return
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
