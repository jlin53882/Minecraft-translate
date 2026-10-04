"""translation_tool/core/lm_translator.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# lm_translator.py
import hashlib
import json as json_std
import os
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import orjson as json

import translation_tool.utils.cache_manager as _cache_manager
from translation_tool.core.lm_config_rules import (
    validate_api_keys,
    value_fully_translated,
)
from translation_tool.core.lm_translator_main import (
    DEFAULT_DRY_RUN,
    DEFAULT_EXPORT_CACHE_ONLY,
    translate_batch_smart,
)
from translation_tool.core.lm_translator_scan import (
    extract_items_parallel,
    scan_translatable_files,
)
from translation_tool.core.lm_translator_shared import (
    CacheRule,
    TranslatorHooks,
    prepare_translator_items,
    run_translator_skeleton,
)
from translation_tool.core.translation_path_writer import (
    map_lang_output_path,
    set_by_path,
)
from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.cache_manager import (
    get_cache_dict_ref,
    reload_translation_cache,
)
from translation_tool.utils.cancellation import TaskCancelled, is_cancelled
from translation_tool.utils.config_manager import (
    get_batch_write_interval,
    load_config,
)
from translation_tool.utils.log_unit import log_debug, log_info, log_warning

# Keep historical module attributes patchable while the shared loop owns writes.
add_to_cache = _cache_manager.add_to_cache
save_translation_cache = _cache_manager.save_translation_cache


# ============================================================
# B-3: 快取寫入頻率優化（每 N 個批次才寫一次硬碟）
# ============================================================
def _get_batch_write_interval() -> int:
    """讀取 lm_translator.batch_write_interval（與 UI 共用 config_manager 的實作）。"""
    return get_batch_write_interval()


# ============================================================
# B-4: 斷點續傳機制
# ============================================================
CHECKPOINT_FILE = str(get_data_root() / "logs" / "translation_checkpoint.json")


def compute_checkpoint_fingerprint(input_dir: str, items: list) -> str:
    """計算待翻譯內容的指紋，確保 checkpoint 只會用在同一批資料上。

    指紋涵蓋輸入資料夾與每一筆項目的 (file, path, text)，任何來源不同、
    內容或順序改變都會得到不同指紋。
    """
    digest = hashlib.sha256(os.path.abspath(input_dir).encode("utf-8"))
    for item in items:
        for field in ("file", "path", "text"):
            digest.update(b"\x1f")
            digest.update(str(item.get(field, "")).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def save_checkpoint(
    batch_index: int,
    completed_count: int,
    total: int,
    remaining: list,
    output_dir: str,
    *,
    input_dir: str | None = None,
    fingerprint: str | None = None,
):
    """寫入 checkpoint（每批次完成後）。

    Args:
        batch_index: 目前處理的批次編號
        completed_count: 已完成的項目數量（用於恢復時計算正確的剩餘切片起點）
        total: 總項目數量
        remaining: 剩餘待翻譯項目清單（用於恢復時取樣比对）
        output_dir: 輸出目錄路徑
        input_dir: 輸入資料夾（僅供診斷）
        fingerprint: compute_checkpoint_fingerprint() 的結果，恢復時必須相符
    """
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json_std.dump(
            {
                "batch_index": batch_index,
                "completed_count": completed_count,
                "total": total,
                "remaining_sample": remaining[:3]
                if remaining
                else [],  # 只保留前三筆範例，不存完整清單
                "output_dir": output_dir,
                "input_dir": input_dir,
                "fingerprint": fingerprint,
            },
            f,
            ensure_ascii=False,
        )


def load_checkpoint() -> dict | None:
    """讀取 checkpoint，若不存在或讀取失敗回傳 None。

    Returns:
        checkpoint 字典，若無 checkpoint 則回傳 None
    """
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json_std.load(f)
    except Exception:  # noqa: BLE001
        return None


def clear_checkpoint():
    """清除 checkpoint 檔案（恢复成功后调用）。"""
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)


def get_formatted_duration(start_tick: float) -> str:
    """將開始時間轉換為人類可讀的格式。

    Args:
        start_tick: 開始時間（由 time.perf_counter() 取得）

    Returns:
        人類可讀的時間字串，如 "1 小時 30 分 45 秒" 或 "30 分 45 秒"
    """
    # 使用 perf_counter 計算目前時間（高精度、單調遞增）
    current_tick = time.perf_counter()

    # 計算經過的秒數（轉為整數秒）
    duration = int(current_tick - start_tick)

    # 拆解為 小時 / 分 / 秒
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)

    # 超過 1 小時才顯示「小時」欄位
    if hours > 0:
        return f"{hours} 小時 {minutes} 分 {seconds} 秒"
    else:
        return f"{minutes} 分 {seconds} 秒"


# 剩餘時間
def format_duration_seconds(seconds: int) -> str:
    """
    將「秒數」格式化為人類可讀的時間字串。

    用途：
    - ETA（預計剩餘時間）
    - 批次處理剩餘時間顯示
    - 任意以秒為單位的時間估算輸出

    範例：
    - 75        -> "1 分 15 秒"
    - 3661      -> "1 小時 1 分 1 秒"
    - 59        -> "0 分 59 秒"

    設計原則：
    - 不依賴系統時間（僅處理純秒數）
    - 自動處理負值或非整數輸入
    - 小於 1 小時時不顯示「小時」欄位，保持輸出簡潔
    """

    # 安全防護：確保秒數為非負整數
    seconds = max(0, int(seconds))

    # 拆解為 小時 / 分 / 秒
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # 超過 1 小時才顯示「小時」欄位，避免 UI 冗長
    if hours > 0:
        return f"{hours} 小時 {minutes} 分 {seconds} 秒"
    else:
        return f"{minutes} 分 {seconds} 秒"


# ============================================================
# 對外唯一入口（UI / CLI 共用）
# ============================================================


def _directory_cache_rules() -> dict[str, CacheRule]:
    """回傳目錄翻譯使用的 shared cache split contract。"""
    return {
        "lang": CacheRule("path"),
        "patchouli": CacheRule("path|source_text"),
    }


def _split_directory_items(
    all_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """由 shared cache layer 統一拆分 hit/miss，避免 generator 自行重複比對。"""

    def is_valid_hit(dst: str, entry: dict[str, Any], item: dict[str, Any]) -> bool:
        if not value_fully_translated(dst):
            return False
        if item.get("cache_type") == "lang":
            source = item.get("source_text") or item.get("text") or ""
            return bool(source) and entry.get("src") == source
        return True

    return prepare_translator_items(
        all_items,
        cache_rules=_directory_cache_rules(),
        is_valid_hit=is_valid_hit,
        cache_provider=get_cache_dict_ref,
    )


def _write_directory_outputs(
    file_cache: dict[str, dict],
    touched_files: set[str],
    root: Path,
    out_root: Path,
    export_lang: bool,
) -> None:
    """寫出一個或多個已完成 batch 觸及的檔案。"""
    for file_name in touched_files:
        src = Path(file_name)
        rel = map_lang_output_path(src.relative_to(root))
        if export_lang and "lang" in src.parts:
            dst = (out_root / rel).with_suffix(".lang")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(
                "\n".join(
                    f"{key}={value}" for key, value in file_cache[file_name].items()
                ),
                encoding="utf-8",
            )
            continue

        dst = out_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(
            json.dumps(
                file_cache[file_name],
                option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS,
            )
        )


def _restore_directory_checkpoint(
    input_dir: str,
    items_to_translate: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, str]:
    """載入並驗證目錄翻譯 checkpoint，回傳剩餘項目、已完成數與 fingerprint。"""
    fingerprint = compute_checkpoint_fingerprint(input_dir, items_to_translate)
    checkpoint = load_checkpoint()
    if not checkpoint:
        return items_to_translate, 0, fingerprint

    completed = checkpoint.get("completed_count", 0)
    total = checkpoint.get("total", 0)
    if checkpoint.get("fingerprint") != fingerprint:
        log_warning("⚠️ checkpoint 屬於其他資料（來源或內容不同），忽略並重新開始")
        clear_checkpoint()
        return items_to_translate, 0, fingerprint
    if completed > len(items_to_translate):
        log_warning("⚠️ checkpoint 數量異常，忽略並重新開始")
        clear_checkpoint()
        return items_to_translate, 0, fingerprint
    if total != len(items_to_translate):
        log_warning("⚠️ checkpoint 與目前總數不一致，忽略並重新開始")
        clear_checkpoint()
        return items_to_translate, 0, fingerprint

    log_info(f"🔄 偵測到 checkpoint，已完成 {completed}/{len(items_to_translate)} 筆")
    return items_to_translate[completed:], completed, fingerprint


def _extract_directory_items(
    files: list[str],
    *,
    export_lang: bool,
    work_thread: int,
) -> tuple[dict[str, dict], list[dict[str, Any]], list[dict[str, Any]]]:
    """抽取目錄項目，並把抽取進度轉成入口可轉送的事件。"""
    file_cache: dict[str, dict] = {}
    all_items: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    previous_progress = 0.0

    for file_cache, all_items in extract_items_parallel(
        files=files,
        export_lang=export_lang,
        work_thread=work_thread,
    ):
        extract_progress = len(file_cache) / max(len(files), 1)
        if extract_progress - previous_progress >= 0.05 or extract_progress >= 1.0:
            previous_progress = extract_progress
            events.append(
                {
                    "progress": 0.2 * extract_progress,
                    "log": f"✂️ 抽取中... ({len(file_cache)}/{len(files)} 檔)",
                }
            )

    return file_cache, all_items, events


def _apply_cached_directory_items(
    cached_items: list[dict[str, Any]],
    file_cache: dict[str, dict],
    *,
    dry_run: bool,
    root: Path,
    out_root: Path,
    export_lang: bool,
) -> set[str]:
    """套用 cache 命中項目並寫出受影響檔案；dry-run 僅產生預覽。"""
    if dry_run:
        return set()

    touched_files: set[str] = set()
    for item in cached_items:
        file_name = item.get("file")
        if file_name not in file_cache:
            continue
        set_by_path(file_cache[file_name], item["path"], item["text"])
        touched_files.add(file_name)

    _write_directory_outputs(file_cache, touched_files, root, out_root, export_lang)
    return touched_files


def _write_directory_previews(
    items_to_translate: list[dict[str, Any]],
    cached_items: list[dict[str, Any]],
    out_root: Path,
) -> None:
    """輸出 dry-run 的待翻譯與 cache 命中預覽。"""
    preview_path = out_root / "_dry_run_preview.json"
    cache_hit_preview_path = out_root / "_dry_run_cache_hit_preview.json"
    preview_path.write_bytes(
        json.dumps(
            items_to_translate,
            option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS,
        )
    )
    cache_hit_preview_path.write_bytes(
        json.dumps(
            [
                {
                    "file": item.get("file"),
                    "path": item.get("path"),
                    "text": item.get("text"),
                    "source_text": item.get("source_text"),
                    "cache_type": item.get("cache_type"),
                }
                for item in cached_items
            ],
            option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS,
        )
    )
    log_info(
        f"\\n🚧 DRY-RUN 完成，預覽檔已產生\\n"
        f"📄 待翻譯預覽：{preview_path}\\n"
        f"🎯 Cache 命中預覽：{cache_hit_preview_path}"
    )


def _run_directory_translation(
    *,
    remaining: list[dict[str, Any]],
    file_cache: dict[str, dict],
    root: Path,
    out_root: Path,
    export_lang: bool,
    should_cancel: Callable[[], bool] | None,
    write_new_cache: bool,
    input_dir: str,
    items_to_translate: list[dict[str, Any]],
    completed_before: int,
    checkpoint_fingerprint: str,
    total: int,
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]], int]:
    """執行目錄翻譯 phase，集中 callback、輸出、checkpoint 與終態契約。"""
    pending_events: list[dict[str, Any]] = []
    touched_files: set[str] = set()
    translation_log: list[dict[str, Any]] = []
    checkpoint_batch_index = 0

    def on_translated_item(item: dict[str, Any]) -> None:
        file_name = item.get("file")
        if file_name not in file_cache:
            return
        set_by_path(file_cache[file_name], item["path"], item["text"])
        touched_files.add(file_name)
        source_text = item.get("source_text") or item.get("text") or ""
        if source_text:
            translation_log.append(
                {
                    "file": file_name,
                    "path": item["path"],
                    "cache_type": item.get("cache_type"),
                    "source": source_text,
                    "translated": item["text"],
                }
            )

    def on_batch_flushed() -> None:
        _write_directory_outputs(file_cache, touched_files, root, out_root, export_lang)
        touched_files.clear()

    def on_batch_checkpoint(state: dict[str, Any]) -> None:
        nonlocal checkpoint_batch_index
        checkpoint_batch_index += 1
        processed = completed_before + int(state.get("processed") or 0)
        save_checkpoint(
            checkpoint_batch_index,
            processed,
            total,
            items_to_translate[processed:],
            str(out_root),
            input_dir=input_dir,
            fingerprint=checkpoint_fingerprint,
        )

    def on_progress(progress: float, message: str, _eta_sec: float) -> None:
        absolute_progress = min(
            0.2 + 0.8 * (completed_before / total + progress * len(remaining) / total),
            1.0,
        )
        pending_events.append({"progress": absolute_progress, "log": message})

    def translate_batch(batch: list[dict[str, Any]], batch_total: int | None):
        if (should_cancel is not None and should_cancel()) or is_cancelled():
            raise TaskCancelled()
        return translate_batch_smart(batch, total=batch_total)

    result = run_translator_skeleton(
        remaining,
        total_for_smart=total,
        translate_batch_smart=translate_batch,
        write_new_cache=write_new_cache,
        cache_rules=_directory_cache_rules(),
        reload_cache=False,
        cache_add=add_to_cache,
        cache_save=save_translation_cache,
        hooks=TranslatorHooks(
            on_translated_item=on_translated_item,
            on_batch_flushed=on_batch_flushed,
            on_batch_checkpoint=on_batch_checkpoint,
            on_progress=on_progress,
        ),
    )
    if touched_files:
        on_batch_flushed()

    processed = completed_before + int(result.processed or 0)
    if result.status == "DONE" and processed >= total:
        clear_checkpoint()

    if translation_log:
        table_path = out_root / "translation_map.json"
        table_path.write_bytes(
            json.dumps(
                translation_log,
                option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS,
            )
        )

    return result, pending_events, translation_log, processed


def _directory_final_message(status: str, processed: int, total: int) -> str:
    """把 shared loop 終態轉成目錄翻譯對使用者的訊息。"""
    if status == "CANCELLED":
        return f"⏹ 翻譯已取消，完成 {processed}/{total} 筆"
    if status in {"FAILED", "ALL_KEYS_EXHAUSTED"}:
        return f"⚠️ 翻譯中斷，完成 {processed}/{total} 筆，狀態={status}"
    if status == "DONE" and processed >= total:
        return f"🎉 翻譯完全完成，完成 {processed}/{total} 筆"
    return f"⚠️ 翻譯未完成，完成 {processed}/{total} 筆，狀態={status}"


def translate_directory_generator(
    input_dir: str,
    output_dir: str,
    *,
    dry_run: bool | None = None,
    export_lang: bool = False,
    write_new_cache: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """編排目錄翻譯各 phase；實際工作由 phase helper 負責。"""
    dry_run = DEFAULT_DRY_RUN if dry_run is None else dry_run
    validate_api_keys()
    reload_translation_cache()

    root = Path(input_dir).resolve()
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    log_debug(f"DEBUG [3. Translator Gen]: export_lang={export_lang}")
    log_info(f"\\n📂 輸入資料夾：{root}\\n📤 輸出資料夾：{out_root}")
    yield {"progress": 0.0}

    try:
        patchouli_files, lang_files, files = scan_translatable_files(root)
    except Exception as error:  # noqa: BLE001
        log_warning(f"⚠️ 掃描可翻譯檔案失敗，已跳過本次掃描：{error}")
        patchouli_files, lang_files, files = [], [], []

    log_info(f"🔍 掃描完成：Patchouli={len(patchouli_files)}，Lang={len(lang_files)}")
    yield {"progress": 0.0}
    if not files:
        log_info("⚠️ 未找到任何可翻譯 JSON 檔案")
        yield {"progress": 1.0}
        return

    work_thread = (
        load_config().get("translator", {}).get("parallel_execution_workers", 4)
    )
    file_cache, all_items, extract_events = _extract_directory_items(
        files,
        export_lang=export_lang,
        work_thread=work_thread,
    )
    yield from extract_events
    log_info(f"✂️ 抽取完成：共 {len(all_items)} 段文字")
    yield {"progress": 0.2}

    cached_items, items_to_translate = _split_directory_items(all_items)
    log_info(
        f"🧠 Cache 命中 {len(cached_items)} 筆，需翻譯 {len(items_to_translate)} 筆"
    )
    yield {"progress": 0.2}

    cached_files = _apply_cached_directory_items(
        cached_items,
        file_cache,
        dry_run=dry_run,
        root=root,
        out_root=out_root,
        export_lang=export_lang,
    )
    if cached_files:
        yield {"progress": 0.2}

    if DEFAULT_EXPORT_CACHE_ONLY and not items_to_translate and not dry_run:
        clear_checkpoint()
        yield {"progress": 1.0}
        return

    if dry_run:
        _write_directory_previews(items_to_translate, cached_items, out_root)
        yield {"progress": 1.0}
        return

    total = len(items_to_translate)
    if total == 0:
        clear_checkpoint()
        log_info("🎉 所有項目皆已從 Cache 恢復，無需翻譯。")
        yield {"progress": 1.0, "log": "🎉 所有項目皆已從 Cache 恢復，無需翻譯。"}
        return

    remaining, completed_before, checkpoint_fingerprint = _restore_directory_checkpoint(
        input_dir, items_to_translate
    )
    result, pending_events, _translation_log, processed = _run_directory_translation(
        remaining=remaining,
        file_cache=file_cache,
        root=root,
        out_root=out_root,
        export_lang=export_lang,
        should_cancel=should_cancel,
        write_new_cache=write_new_cache,
        input_dir=input_dir,
        items_to_translate=items_to_translate,
        completed_before=completed_before,
        checkpoint_fingerprint=checkpoint_fingerprint,
        total=total,
    )
    if pending_events:
        yield from pending_events

    final_message = _directory_final_message(result.status, processed, total)
    log_info(final_message)
    yield {
        "progress": 1.0 if processed >= total else processed / total,
        "log": final_message,
    }
