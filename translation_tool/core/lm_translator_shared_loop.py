"""translation_tool/core/lm_translator_shared_loop.py 模組。

用途：翻譯迴圈的控制與流程管理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from translation_tool.core.lm_translator_shared_cache import (
    CacheRule,
    get_default_cache_rules,
)
from translation_tool.utils.cache_manager import (
    add_to_cache,
    reload_translation_cache,
    save_translation_cache,
)
from translation_tool.utils.cancellation import TaskCancelled, is_cancelled
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_info


@dataclass
class TranslateLoopResult:
    """翻譯任務結束後的統計資料。"""

    status: str
    processed: int
    total: int
    completed_calls: int
    elapsed_sec: float
    exhausted: bool
    last_error: str | None = None


def _get_default_batch_size(
    cache_type: str, batch_size_by_type: dict[str, int] | None
) -> int:
    """根據 cache type 從設定檔查詢對應的批次大小，若未設定則回傳該類型的預設值。"""
    if batch_size_by_type and cache_type in batch_size_by_type:
        return int(batch_size_by_type[cache_type])

    cfg = load_config()
    lm_cfg = (cfg or {}).get("lm_translator", {}) if isinstance(cfg, dict) else {}

    if cache_type == "ftbquests":
        return int(lm_cfg.get("initial_batch_size_ftb", 100) or 100)
    if cache_type == "kubejs":
        return int(lm_cfg.get("initial_batch_size_kubejs", 200) or 200)
    if cache_type == "patchouli":
        return int(lm_cfg.get("initial_batch_size_patchouli", 100) or 100)
    if cache_type == "md":
        return int(lm_cfg.get("initial_batch_size_md", 100) or 100)
    return int(lm_cfg.get("initial_batch_size_lang", 300) or 300)


def translate_items_with_cache_loop(
    items_to_translate: list[dict[str, Any]],
    *,
    total_for_smart: int | None = None,
    translate_batch_smart: Callable[
        [list[dict[str, Any]], int | None],
        tuple[list[dict[str, Any]] | None, str],
    ],
    batch_size_by_type: dict[str, int] | None = None,
    write_new_cache: bool = True,
    on_translated_item: Callable[[dict[str, Any]], None] | None = None,
    on_batch_flushed: Callable[[], None] | None = None,
    on_progress: Callable[[float, str, float], None] | None = None,
    cache_rules: dict[str, CacheRule] | None = None,
    sleep_seconds_between_batches: float | None = None,
) -> TranslateLoopResult:
    """執行翻譯主迴圈，分批呼叫翻譯 API、寫入快取、回報進度與 ETA，支援中斷與額度耗盡處理。"""
    if cache_rules is None:
        cache_rules = get_default_cache_rules()

    # 如果未指定 sleep_seconds_between_batches，從 config 讀取
    if sleep_seconds_between_batches is None:
        sleep_seconds_between_batches = (
            load_config()
            .get("lm_translator", {})
            .get("rate_limit", {})
            .get("sleep_seconds_between_batches", 0.0)
        )

    reload_translation_cache()
    log_info("[Translator Gen]: 重新載入快取完成")
    start_time = time.time()

    total = (
        int(total_for_smart)
        if isinstance(total_for_smart, int) and total_for_smart > 0
        else len(items_to_translate)
    )
    remaining: list[dict[str, Any]] = list(items_to_translate)
    processed = 0
    completed_calls = 0
    last_error: str | None = None
    exhausted = False

    def emit_progress(msg: str) -> None:
        if on_progress is None:
            return
        try:
            progress = min(processed / max(total, 1), 1.0)
            elapsed = time.time() - start_time
            if processed > 0 and elapsed > 0:
                speed = processed / elapsed
                eta_sec = (total - processed) / speed
            else:
                eta_sec = 0.0
            on_progress(progress, msg, eta_sec)
        except Exception as e:  # noqa: BLE001
            log_info(f"[SharedLM] 進度回報失敗: {e}")

    emit_progress("🚀 [SharedLM] 準備開始翻譯工作...")

    def cancelled_result() -> TranslateLoopResult:
        emit_progress(
            f"⏹ 已取消翻譯，剩餘 {len(remaining)} 筆未翻譯（已完成的批次已寫入快取）"
        )
        return TranslateLoopResult(
            status="CANCELLED",
            processed=processed,
            total=total,
            completed_calls=completed_calls,
            elapsed_sec=time.time() - start_time,
            exhausted=False,
            last_error=None,
        )

    while remaining:
        # 取消檢查點：在批次之間停止，已完成的批次照常保留
        if is_cancelled():
            return cancelled_result()
        cache_type = str(remaining[0].get("cache_type") or "lang")
        batch_size = _get_default_batch_size(cache_type, batch_size_by_type)
        if batch_size <= 0:
            batch_size = 50

        batch = remaining[:batch_size]

        try:
            translated, status = translate_batch_smart(batch, total_for_smart)
        except TaskCancelled:
            # 等待 API 限流時被取消
            return cancelled_result()
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            emit_progress(f"❌ [SharedLM] 翻譯發生異常: {e}")
            return TranslateLoopResult(
                status="FAILED",
                processed=processed,
                total=total,
                completed_calls=completed_calls,
                elapsed_sec=time.time() - start_time,
                exhausted=False,
                last_error=last_error,
            )

        completed_calls += 1
        safe_translated = translated or []
        actual_processed_in_this_batch = 0
        untranslated_fallback = 0

        for it in safe_translated:
            if not isinstance(it, dict):
                continue

            pth = it.get("path")
            txt = it.get("text")
            src = it.get("source_text")
            ctype = str(it.get("cache_type") or cache_type)

            if not (
                isinstance(pth, str) and isinstance(txt, str) and isinstance(src, str)
            ):
                continue

            actual_processed_in_this_batch += 1
            processed += 1

            if on_translated_item is not None:
                try:
                    on_translated_item(it)
                except Exception as e:  # noqa: BLE001
                    log_info(f"[SharedLM] 處理翻譯結果失敗: {e}")

            if it.get("_untranslated"):
                # 批次縮到極限後回填的原文：保留在輸出，但不寫入快取
                untranslated_fallback += 1
                continue

            rule = cache_rules.get(ctype) or CacheRule("path|source_text")
            cache_key = rule.make_key({"path": pth, "source_text": src})
            try:
                add_to_cache(ctype, cache_key, src, txt)
            except Exception as e:  # noqa: BLE001
                log_info(f"[SharedLM] 新增快取失敗: {e}")

        if untranslated_fallback:
            log_info(
                f"[SharedLM] {untranslated_fallback} 筆因批次縮至極限而回填原文，未寫入快取"
            )

        # translate_batch_smart 依輸入順序回傳，未完成時只回傳前綴。
        # 以「實際回傳筆數」切片：格式無效被略過的項目也算已消耗，
        # 否則會錯把批次尾端尚未處理／已處理的項目留下造成重翻或漏翻。
        consumed = len(safe_translated)
        consumed = min(consumed, len(batch))
        if actual_processed_in_this_batch < consumed:
            log_info(
                f"[SharedLM] {consumed - actual_processed_in_this_batch} 筆回傳結果"
                "格式無效，已略過"
            )
        remaining = remaining[consumed:]

        try:
            save_translation_cache(cache_type, write_new_shard=write_new_cache)
        except Exception as e:  # noqa: BLE001
            log_info(f"[SharedLM] 儲存快取失敗: {e}")

        if on_batch_flushed is not None:
            try:
                on_batch_flushed()
            except Exception as e:  # noqa: BLE001
                log_info(f"[SharedLM] 批次刷新回調失敗: {e}")

        emit_progress(
            f"✅ 批次完成 ({cache_type}) | 成功: {actual_processed_in_this_batch} | 總進度: {processed}/{total}"
        )

        st = (status or "").upper()
        if st == "ALL_KEYS_EXHAUSTED":
            exhausted = True
            emit_progress("⚠️ [SharedLM] API 額度用盡，停止工作。")
            break

        if st in ("FAILED", "FATAL", "ERROR"):
            last_error = f"API 回傳失敗狀態: {status}"
            emit_progress(f"❌ [SharedLM] 終止: {last_error}")
            return TranslateLoopResult(
                status="FAILED",
                processed=processed,
                total=total,
                completed_calls=completed_calls,
                elapsed_sec=time.time() - start_time,
                exhausted=False,
                last_error=last_error,
            )

        if sleep_seconds_between_batches > 0:
            time.sleep(sleep_seconds_between_batches)

        if actual_processed_in_this_batch == 0:
            last_error = f"此批次未能翻譯任何內容 (Status: {status})"
            emit_progress(f"❌ [SharedLM] {last_error}")
            return TranslateLoopResult(
                status="FAILED",
                processed=processed,
                total=total,
                completed_calls=completed_calls,
                elapsed_sec=time.time() - start_time,
                exhausted=False,
                last_error=last_error,
            )

    final_status = "ALL_KEYS_EXHAUSTED" if exhausted else "DONE"
    emit_progress(f"🏁 任務結束 | 狀態: {final_status}")

    return TranslateLoopResult(
        status=final_status,
        processed=processed,
        total=total,
        completed_calls=completed_calls,
        elapsed_sec=time.time() - start_time,
        exhausted=exhausted,
        last_error=last_error,
    )
