"""translation_tool/core/lm_translator_shared_loop.py 模組。

用途：翻譯迴圈的控制與流程管理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from translation_tool.core.lm_batch_budget import (
    profile_for_cache_type,
    select_batch_size,
)
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
from translation_tool.utils.redaction import redact_text


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


def _is_valid_result(it: Any) -> bool:
    """結果需為 dict 且含字串型別的 path / text / source_text。"""
    return (
        isinstance(it, dict)
        and isinstance(it.get("path"), str)
        and isinstance(it.get("text"), str)
        and isinstance(it.get("source_text"), str)
    )


def _lm_config() -> dict[str, Any]:
    """目前的 lm_translator 設定（設定不是 dict 時回傳空 dict）。"""
    cfg = load_config()
    lm_cfg = (cfg or {}).get("lm_translator", {}) if isinstance(cfg, dict) else {}
    return lm_cfg if isinstance(lm_cfg, dict) else {}


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
            log_info(f"[SharedLM] 進度回報失敗: {redact_text(e)}")

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

        # 項目數上限之外，再依 token 預算取前綴（與 lm_translator_main 共用同一個估算與學到的預算）
        fit_count = select_batch_size(
            remaining,
            profile_for_cache_type(cache_type),
            batch_size,
            _lm_config(),
        )
        batch = remaining[:fit_count]

        try:
            translated, status = translate_batch_smart(batch, total_for_smart)
        except TaskCancelled:
            # 等待 API 限流時被取消
            return cancelled_result()
        except Exception as e:  # noqa: BLE001
            last_error = redact_text(e)
            emit_progress(f"❌ [SharedLM] 翻譯發生異常: {last_error}")
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
        safe_translated = list(translated or [])[: len(batch)]
        actual_processed_in_this_batch = 0
        untranslated_fallback = 0
        malformed_fallback = 0
        cache_write_failed = False

        # 對應契約：translate_batch_smart 依輸入順序回傳，第 i 筆結果對應 batch[i]；
        # 未完成時只回傳前綴（剩下的留在 remaining 下一輪重送）。
        # 格式無效的結果不可靜默丟棄：以 batch[i] 原文回填並標記 _untranslated，
        # 輸出仍完整、不寫入快取，且計入 processed，讓 DONE 時 processed == total。
        for position, it in enumerate(safe_translated):
            original = batch[position]
            if not _is_valid_result(it):
                fallback_src = original.get("source_text", original.get("text"))
                it = {
                    **original,
                    "text": fallback_src if isinstance(fallback_src, str) else "",
                    "_untranslated": True,
                }
                if not _is_valid_result(it):
                    # 原始項目本身缺欄位：無法輸出也無法快取，不能算完成
                    last_error = f"批次第 {position} 筆缺少 path/source_text，無法回填"
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
                malformed_fallback += 1

            pth = it["path"]
            txt = it["text"]
            src = it["source_text"]
            ctype = str(it.get("cache_type") or cache_type)

            actual_processed_in_this_batch += 1
            processed += 1

            if on_translated_item is not None:
                try:
                    on_translated_item(it)
                except Exception as e:  # noqa: BLE001
                    log_info(f"[SharedLM] 處理翻譯結果失敗: {redact_text(e)}")

            if it.get("_untranslated"):
                # 批次縮到極限回填的原文 / 格式無效回填的原文：保留在輸出，但不寫入快取
                untranslated_fallback += 1
                continue

            rule = cache_rules.get(ctype) or CacheRule("path|source_text")
            cache_key = rule.make_key({"path": pth, "source_text": src})
            try:
                cache_written = add_to_cache(ctype, cache_key, src, txt)
                if cache_written is False:
                    cache_write_failed = True
                    last_error = f"快取寫入被拒絕：{ctype}:{cache_key}"
                    log_info(f"[SharedLM] {last_error}")
            except Exception as e:  # noqa: BLE001
                cache_write_failed = True
                last_error = f"新增快取失敗: {redact_text(e)}"
                log_info(f"[SharedLM] {last_error}")

        if untranslated_fallback:
            log_info(
                f"[SharedLM] {untranslated_fallback} 筆回填原文（批次縮至極限或回傳格式無效），未寫入快取"
            )
        if malformed_fallback:
            last_error = f"{malformed_fallback} 筆回傳格式無效，已回填原文且未翻譯"

        # 切片位置 == 已處理的前綴長度（每筆回傳都已處理或回填），不會錯位。
        remaining = remaining[len(safe_translated) :]

        save_ok = True
        try:
            save_result = save_translation_cache(
                cache_type, write_new_shard=write_new_cache
            )
            save_ok = save_result is not False
        except Exception as e:  # noqa: BLE001
            save_ok = False
            last_error = f"儲存快取失敗: {redact_text(e)}"
            log_info(f"[SharedLM] {last_error}")

        if not save_ok:
            cache_write_failed = True
            last_error = last_error or f"快取 {cache_type} 儲存失敗"

        if on_batch_flushed is not None:
            try:
                on_batch_flushed()
            except Exception as e:  # noqa: BLE001
                log_info(f"[SharedLM] 批次刷新回調失敗: {redact_text(e)}")

        emit_progress(
            f"✅ 批次完成 ({cache_type}) | 成功: {actual_processed_in_this_batch} | 總進度: {processed}/{total}"
        )

        if cache_write_failed:
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
