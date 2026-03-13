from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Any
import time

from translation_tool.utils.log_unit import log_info
from translation_tool.utils.cache_manager import add_to_cache, save_translation_cache, reload_translation_cache
from translation_tool.utils.config_manager import load_config
from translation_tool.core.lm_translator_shared_cache import CacheRule, get_default_cache_rules

@dataclass
class TranslateLoopResult:
    """翻譯任務結束後的統計資料。"""

    status: str
    processed: int
    total: int
    completed_calls: int
    elapsed_sec: float
    exhausted: bool
    last_error: Optional[str] = None

def _get_default_batch_size(
    cache_type: str, batch_size_by_type: Optional[Dict[str, int]]
) -> int:
    """依 cache type 與設定檔決定批次大小。"""
    if batch_size_by_type and cache_type in batch_size_by_type:
        return int(batch_size_by_type[cache_type])

    cfg = load_config()
    lm_cfg = (cfg or {}).get("lm_translator", {}) if isinstance(cfg, dict) else {}

    if cache_type == "ftbquests":
        return int(lm_cfg.get("initial_batch_size_ftb", 100) or 100)
    if cache_type == "kubejs":
        return int(lm_cfg.get("initial_batch_size_kubejs", 200) or 200)
    if cache_type == "patchouli":
        return int(lm_cfg.get("iniital_batch_size_patchouli", 100) or 100)
    if cache_type == "md":
        return int(lm_cfg.get("iniital_batch_size_md", 100) or 100)
    return int(lm_cfg.get("iniital_batch_size_lang", 300) or 300)

def translate_items_with_cache_loop(
    items_to_translate: List[Dict[str, Any]],
    *,
    total_for_smart: Optional[int] = None,
    translate_batch_smart: Callable[
        [List[Dict[str, Any]], Optional[int]],
        Tuple[Optional[List[Dict[str, Any]]], str],
    ],
    batch_size_by_type: Optional[Dict[str, int]] = None,
    write_new_cache: bool = True,
    on_translated_item: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_batch_flushed: Optional[Callable[[], None]] = None,
    on_progress: Optional[Callable[[float, str, float], None]] = None,
    cache_rules: Optional[Dict[str, CacheRule]] = None,
    sleep_seconds_between_batches: float = 0.0,
) -> TranslateLoopResult:
    """帶 cache 與 ETA 的翻譯主迴圈。"""
    if cache_rules is None:
        cache_rules = get_default_cache_rules()

    reload_translation_cache()
    log_info("[Translator Gen]: 重新載入快取完成")
    start_time = time.time()

    total = (
        int(total_for_smart)
        if isinstance(total_for_smart, int) and total_for_smart > 0
        else len(items_to_translate)
    )
    remaining: List[Dict[str, Any]] = list(items_to_translate)
    processed = 0
    completed_calls = 0
    last_error: Optional[str] = None
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
        except Exception:
            pass

    emit_progress("🚀 [SharedLM] 準備開始翻譯工作...")

    while remaining:
        cache_type = str(remaining[0].get("cache_type") or "lang")
        batch_size = _get_default_batch_size(cache_type, batch_size_by_type)
        if batch_size <= 0:
            batch_size = 50

        batch = remaining[:batch_size]

        try:
            translated, status = translate_batch_smart(batch, total_for_smart)
        except Exception as e:
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
                except Exception:
                    pass

            rule = cache_rules.get(ctype) or CacheRule("path|source_text")
            cache_key = rule.make_key({"path": pth, "source_text": src})
            try:
                add_to_cache(ctype, cache_key, src, txt)
            except Exception:
                pass

        remaining = remaining[actual_processed_in_this_batch:]

        try:
            save_translation_cache(cache_type, write_new_shard=write_new_cache)
        except Exception:
            pass

        if on_batch_flushed is not None:
            try:
                on_batch_flushed()
            except Exception:
                pass

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
