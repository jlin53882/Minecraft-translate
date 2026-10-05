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
from translation_tool.core.lm_translator_db import (
    DirectoryDbContext,
    directory_db,
    flush_write_back,
    resolve_db_choice,
    split_db_hits,
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
from translation_tool.utils.fs_utils import fsync_directory
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


CHECKPOINT_VERSION = 2


def _atomic_write_text(path: str, text: str) -> None:
    """暫存檔 fsync → ``os.replace`` → fsync 目錄：被中斷時要嘛是舊內容、要嘛是完整新內容。

    打包成 exe 後關閉視窗不保證執行任何清理，所以不能依賴關閉流程補寫（#151）。
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    fsync_directory(directory)


def save_checkpoint(
    batch_index: int,
    completed_count: int,
    total: int,
    remaining: list,
    output_dir: str,
    *,
    input_dir: str | None = None,
    fingerprint: str | None = None,
    export_lang: bool | None = None,
    write_new_cache: bool | None = None,
    translation_db: dict[str, Any] | None = None,
):
    """寫入「任務尚未完成」的標記（每批次完成、快取落盤後呼叫）。

    checkpoint 不是翻譯結果的備份：已完成批次的譯文在翻譯快取（已 fsync），重開後由快取還原，
    所以續跑不會依「位置」跳過項目，也就不可能因為快取與 checkpoint 不同步而漏翻。
    checkpoint 的用途是讓下次啟動偵測到「上次沒做完」並詢問使用者（見 ``lm_resume``）。

    Args:
        batch_index: 目前處理的批次編號
        completed_count: 已 durable 的進度（快取命中加上本次已落盤的翻譯；只有快取存檔成功的批次
            才會寫 checkpoint，所以它是「可恢復的進度」，不等於本次行程已處理的數量；僅供顯示）
        total: 可翻譯項目總數（抽取結果的筆數，不受快取進度影響）
        remaining: 剩餘待翻譯項目清單（只留前三筆作診斷）
        output_dir: 輸出目錄路徑
        input_dir: 輸入資料夾
        fingerprint: compute_checkpoint_fingerprint() 的結果（涵蓋全部抽取項目），
            續跑前必須相符
        export_lang / write_new_cache: 這次任務的選項，續跑時沿用
        translation_db: 這次任務**實際生效**的 Mod 資料庫選項 ``{"enabled": bool, "version": str}``，
            續跑時必須沿用（否則設定改變後，剩餘項目會寫回不同版本）
    """
    payload = {
        "version": CHECKPOINT_VERSION,
        "batch_index": batch_index,
        "completed_count": completed_count,
        "total": total,
        "remaining_sample": remaining[:3] if remaining else [],
        "output_dir": output_dir,
        "input_dir": input_dir,
        "fingerprint": fingerprint,
        "export_lang": export_lang,
        "write_new_cache": write_new_cache,
        "translation_db": translation_db,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _atomic_write_text(CHECKPOINT_FILE, json_std.dumps(payload, ensure_ascii=False))


def _quarantine_corrupt_checkpoint() -> str | None:
    """把無法使用的 checkpoint 改名為 ``.corrupt``（保留供診斷），回傳新路徑；失敗回傳 None。

    只記錄警告但留著原檔，每次啟動都會再讀到、再警告。改名後下一次就是「沒有 checkpoint」；
    下一個損毀的檔案會覆蓋舊的 ``.corrupt``（只保留最近一份）。
    """
    quarantined = f"{CHECKPOINT_FILE}.corrupt"
    try:
        os.replace(CHECKPOINT_FILE, quarantined)
    except OSError as exc:
        log_warning(f"無法隔離損毀的 checkpoint（{CHECKPOINT_FILE}）：{exc!r}")
        return None
    return quarantined


def load_checkpoint() -> dict | None:
    """讀取 checkpoint；不存在、損毀或格式不是物件時回傳 None。

    損毀（無法解析或不是 JSON 物件）的檔案會記錄警告並隔離成 ``.corrupt``，不會殘留在原位。

    Returns:
        checkpoint 字典，若無可用的 checkpoint 則回傳 None
    """
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json_std.load(f)
        if not isinstance(data, dict):
            raise TypeError(
                f"checkpoint 必須是 JSON 物件，實際為 {type(data).__name__}"
            )
        return data
    except Exception as exc:  # noqa: BLE001 - 損毀的 checkpoint 視為沒有，但要留下紀錄
        quarantined = _quarantine_corrupt_checkpoint()
        where = f"，已隔離為 {quarantined}" if quarantined else ""
        log_warning(f"讀取 checkpoint 失敗，視為沒有 checkpoint{where}：{exc!r}")
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


def _cache_saving_enabled() -> bool:
    """快取儲存開著才有「重開後由快取還原」的前提；關閉時不寫 checkpoint。"""
    return bool(load_config().get("translator", {}).get("enable_cache_saving", True))


def _note_directory_checkpoint(
    fingerprint: str,
    *,
    cache_saving: bool = True,
    db_choice: tuple[bool, str] = (False, ""),
) -> None:
    """啟動翻譯時處理上次遺留的 checkpoint（只記錄，不依位置跳過任何項目）。

    已完成批次的譯文在翻譯快取裡：這次的快取分流自然會把它們當成命中並寫回輸出，
    只剩未完成的項目需要翻譯。checkpoint 與目前輸入相符就是「接續上次」；
    不符（輸入內容或來源不同、舊格式）時明確告知並捨棄，不能靜默忽略。
    """
    checkpoint = load_checkpoint()
    if not checkpoint:
        return
    if not cache_saving:
        log_warning("⚠️ 快取儲存已停用，無法接續上次中斷的任務，已捨棄該標記")
        clear_checkpoint()
        return
    if (
        checkpoint.get("version") == CHECKPOINT_VERSION
        and checkpoint.get("fingerprint") == fingerprint
    ):
        log_info(
            f"🔄 接續上次中斷的任務（上次已保存進度 {checkpoint.get('completed_count', 0)}"
            f"/{checkpoint.get('total', 0)} 筆；已完成的部分由快取還原）"
        )
        saved = checkpoint.get("translation_db") or {}
        saved_choice = (bool(saved.get("enabled")), str(saved.get("version") or ""))
        if saved_choice != db_choice:
            log_warning(
                f"⚠️ 這次的 Mod 資料庫選項（使用={db_choice[0]}，版本={db_choice[1] or '未指定'}）"
                f"與中斷的任務（使用={saved_choice[0]}，版本={saved_choice[1] or '未指定'}）不同，"
                "剩餘項目將依這次的選項查詢與寫回；若不是預期的結果請取消並從「續跑」重新開始"
            )
        return
    log_warning("⚠️ 上次中斷的任務屬於其他資料或舊格式，無法接續，已捨棄該標記")
    clear_checkpoint()


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
    cache_hit_count: int,
    checkpoint_fingerprint: str,
    total: int,
    write_checkpoint: bool = True,
    db_ctx: DirectoryDbContext | None = None,
    db_choice: tuple[bool, str] = (False, ""),
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]], int]:
    """執行目錄翻譯 phase，集中 callback、輸出、checkpoint 與終態契約。"""
    _note_directory_checkpoint(
        checkpoint_fingerprint, cache_saving=write_checkpoint, db_choice=db_choice
    )
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
        if (
            db_ctx is not None
            and db_ctx.buffer is not None
            and not item.get("_untranslated")
        ):
            db_ctx.buffer.add(item)
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
        flush_write_back(db_ctx)

    def on_batch_checkpoint(state: dict[str, Any]) -> None:
        nonlocal checkpoint_batch_index
        if not write_checkpoint:
            return
        checkpoint_batch_index += 1
        processed = int(state.get("processed") or 0)
        save_checkpoint(
            checkpoint_batch_index,
            cache_hit_count + processed,
            cache_hit_count + total,
            items_to_translate[processed:],
            str(out_root),
            input_dir=input_dir,
            fingerprint=checkpoint_fingerprint,
            export_lang=export_lang,
            write_new_cache=write_new_cache,
            translation_db={"enabled": db_choice[0], "version": db_choice[1]},
        )

    def on_progress(progress: float, message: str, _eta_sec: float) -> None:
        absolute_progress = min(0.2 + 0.8 * progress, 1.0)
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
    flush_write_back(db_ctx)

    processed = int(result.processed or 0)
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


def _scan_directory_files(root: Path) -> list[Path]:
    """掃描可翻譯檔案；失敗只記錄警告並回傳空清單。"""
    try:
        patchouli_files, lang_files, files = scan_translatable_files(root)
    except Exception as error:  # noqa: BLE001
        log_warning(f"⚠️ 掃描可翻譯檔案失敗，已跳過本次掃描：{error}")
        patchouli_files, lang_files, files = [], [], []
    log_info(f"🔍 掃描完成：Patchouli={len(patchouli_files)}，Lang={len(lang_files)}")
    return files


def _split_db_cache(
    all_items: list[dict[str, Any]],
    db_ctx: DirectoryDbContext | None,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """查詢順序：Mod 資料庫 → 翻譯快取；回傳（可直接套用的項目, 仍需翻譯的項目）。"""
    db_hits, remaining = split_db_hits(db_ctx, all_items)
    cached_items, items_to_translate = _split_directory_items(remaining)
    log_info(
        f"🧠 Cache 命中 {len(cached_items)} 筆，需翻譯 {len(items_to_translate)} 筆"
    )
    if db_ctx is not None and db_ctx.buffer is not None and not dry_run:
        # 快取命中的譯文也補進資料庫（只新增、不覆蓋），讓過去的 AI 成果不流失
        db_ctx.buffer.add_many(cached_items)
        flush_write_back(db_ctx, "快取命中：")
    return db_hits + cached_items, items_to_translate


def translate_directory_generator(
    input_dir: str,
    output_dir: str,
    *,
    dry_run: bool | None = None,
    export_lang: bool = False,
    write_new_cache: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    use_translation_db: bool | None = None,
    translation_db_version: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """編排目錄翻譯各 phase；實際工作由 phase helper 負責。

    查詢順序為 **Mod 資料庫 → 翻譯快取 → AI**。``use_translation_db`` /
    ``translation_db_version`` 為 None 時使用設定檔（``translation_db``）的值。
    """
    dry_run = DEFAULT_DRY_RUN if dry_run is None else dry_run
    validate_api_keys()
    reload_translation_cache()

    root = Path(input_dir).resolve()
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    log_debug(f"DEBUG [3. Translator Gen]: export_lang={export_lang}")
    log_info(f"\\n📂 輸入資料夾：{root}\\n📤 輸出資料夾：{out_root}")
    yield {"progress": 0.0}

    # 資料庫選項（設定為「下次任務才套用」）整個任務只解析一次：開啟資料庫、寫回、checkpoint 共用
    db_choice = resolve_db_choice(use_translation_db, translation_db_version)
    with directory_db(root, use_db=db_choice[0], version=db_choice[1]) as db_ctx:
        files = _scan_directory_files(root)
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
        # 指紋涵蓋全部抽取項目（快取分流之前），不受快取進度影響，重開後才能比對得上
        checkpoint_fingerprint = compute_checkpoint_fingerprint(input_dir, all_items)
        yield {"progress": 0.2}

        cached_items, items_to_translate = _split_db_cache(all_items, db_ctx, dry_run)
        yield {"progress": 0.2}

        if _apply_cached_directory_items(
            cached_items,
            file_cache,
            dry_run=dry_run,
            root=root,
            out_root=out_root,
            export_lang=export_lang,
        ):
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

        cache_saving = _cache_saving_enabled()
        result, pending_events, _translation_log, processed = (
            _run_directory_translation(
                remaining=items_to_translate,
                file_cache=file_cache,
                root=root,
                out_root=out_root,
                export_lang=export_lang,
                should_cancel=should_cancel,
                write_new_cache=write_new_cache,
                input_dir=input_dir,
                items_to_translate=items_to_translate,
                cache_hit_count=len(cached_items),
                checkpoint_fingerprint=checkpoint_fingerprint,
                total=total,
                write_checkpoint=cache_saving,
                db_ctx=db_ctx,
                db_choice=db_choice,
            )
        )
        if pending_events:
            yield from pending_events

        final_message = _directory_final_message(result.status, processed, total)
        log_info(final_message)
        yield {
            "progress": 1.0 if processed >= total else processed / total,
            "log": final_message,
        }
