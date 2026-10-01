"""translation_tool/core/jar_processor_extract.py 模組。

用途：從 Mod JAR 檔案中提取翻譯資源的功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。

重構記錄（PR #55）：
- 內部 JAR 讀取改用 `jar_browser.scan_jars()`，消除重複的 ZIP 掃描邏輯。
- 對 binary 檔案（UTF-8 decode 失敗）仍直接讀取 ZIP 以取得原始 bytes。
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import re
import zipfile
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from ..utils.config_manager import load_config
from ..utils.log_unit import log_error
from ..utils.zip_safety import (
    MAX_FILE_BYTES,
    MAX_TEXT_BYTES,
    ArchiveBudgetError,
    UnsafePathError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
    safe_join,
)

log = logging.getLogger(__name__)

VERSION_REGEX = re.compile(
    r"[-_](?:[a-zA-Z]+-)?\d+(?:\.\d+)+(?:[-_.][a-zA-Z0-9]+)*$",
    re.IGNORECASE,
)


def get_file_hash(data: bytes) -> str:
    """計算資料的 SHA-256 雜湊值（16進位字串）。

    Args:
        data: 原始位元組資料
    Returns:
        SHA-256 雜湊的 16 進位表示（64字元）
    """
    return hashlib.sha256(data).hexdigest()


def _normalize_jar_base_name(jar_filename: str) -> str:
    """從 JAR 檔名提取乾淨的 Mod ID（去除版本號、forge/fabric 等前輈字）。

    Args:
        jar_filename: JAR 檔案路徑或檔名
    Returns:
        乾淨的 Mod ID（如 "Botania"）
    """
    base_full = os.path.splitext(os.path.basename(jar_filename))[0]
    clean_name = re.sub(
        r"[-_](neoforge|forge|fabric|quilt|build|release|alpha|beta)[-_]?",
        "-",
        base_full,
        flags=re.IGNORECASE,
    ).strip("-_")
    match_version = VERSION_REGEX.search(clean_name)
    if match_version:
        base_name = clean_name[: match_version.start()].strip("-_")
    else:
        base_name = clean_name
    return base_name or base_full


def extract_from_jar_impl(
    jar_path: str,
    output_root: str,
    target_regex: re.Pattern,
    get_file_hash_fn: Callable[[bytes], str] = get_file_hash,
    scan_results: dict[Path, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    """從單一 JAR 檔案提取符合目標正規表達式的檔案。

    效能優化：scan_results 由 caller 預先掃描後傳入，避免重複掃描目錄。

    Args:
        jar_path: JAR 檔案路徑
        output_root: 輸出根目錄
        target_regex: 目標正規表達式
        get_file_hash_fn: 檔案 HASH 計算函式（預設 SHA-256）
        scan_results: 預先掃描的 JAR 結果（由 run_extraction_process_impl 一次性掃描後傳入）
    Returns:
        包含 extracted/skipped 的統計字典
    """
    extracted_count = 0
    skipped_count = 0
    jar_filename_base = _normalize_jar_base_name(jar_path)
    jar_path_obj = Path(jar_path)
    # 同一個 JAR 的「預掃描 + 提取」共用同一份累計預算（防止大量合法大小成員的 ZIP bomb）。
    # 正式流程由 scan_jars 建立並隨 scan_results（ScanResults）傳入；
    # 直接呼叫本函式（沒有預掃描結果）時，才建立自己的預算供本函式的掃描與提取共用。
    scan_budgets = getattr(scan_results, "budgets", None) or {}
    budget = scan_budgets.get(jar_path_obj)
    if budget is None:
        budget = ZipReadBudget(label=jar_path_obj.name)

    if not jar_path_obj.exists():
        log_error("JAR 檔案不存在: %s", jar_path)
        return {"status": "error", "extracted": 0, "skipped": 0}

    try:
        jar_results: dict[str, str | None] = {}
        if scan_results is not None and jar_path_obj in scan_results:
            jar_results = scan_results[jar_path_obj]
        else:
            with zipfile.ZipFile(jar_path, "r") as zf:
                for name in zf.namelist():
                    if target_regex.search(name):
                        try:
                            jar_results[name] = read_limited(
                                zf, name, MAX_TEXT_BYTES, budget=budget
                            ).decode("utf-8")
                        except UnicodeDecodeError:
                            jar_results[name] = None
                        except ArchiveBudgetError:
                            raise  # 整包累計超限：交給外層中止這個 JAR（不是單一檔案過大）
                        except ZipSizeError as size_err:
                            log_error("略過過大檔案 %s: %s", name, size_err)

        with zipfile.ZipFile(jar_path, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                normalized_path = member.filename.replace("\\", "/")
                if not target_regex.search(normalized_path):
                    continue

                # 防止路徑遍歷（zip-slip）：最終路徑必須留在 output_root 內
                try:
                    if normalized_path.startswith("assets/"):
                        final_output_path = safe_join(output_root, normalized_path)
                    else:
                        final_mod_folder = f"{jar_filename_base}_extracted"
                        final_output_path = safe_join(
                            output_root, final_mod_folder, normalized_path
                        )
                except UnsafePathError:
                    log_error(
                        "拒絕提取不安全路徑: %s（來源 %s）", normalized_path, jar_path
                    )
                    skipped_count += 1
                    continue

                if (
                    normalized_path in jar_results
                    and jar_results[normalized_path] is not None
                ):
                    source_data = jar_results[normalized_path].encode("utf-8")
                else:
                    try:
                        source_data = read_limited(
                            zf, member, MAX_FILE_BYTES, budget=budget
                        )
                    except ArchiveBudgetError:
                        raise  # 整包累計超限：交給外層中止這個 JAR
                    except ZipSizeError as size_err:
                        log_error("略過過大檔案 %s: %s", normalized_path, size_err)
                        skipped_count += 1
                        continue

                source_hash = get_file_hash_fn(source_data)

                if os.path.exists(final_output_path):
                    with open(final_output_path, "rb") as existing_file:
                        existing_hash = get_file_hash_fn(existing_file.read())
                    if source_hash == existing_hash:
                        skipped_count += 1
                        continue

                os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
                with open(final_output_path, "wb") as target:
                    target.write(source_data)
                extracted_count += 1

        return {
            "status": "success",
            "extracted": extracted_count,
            "skipped": skipped_count,
        }
    except ArchiveBudgetError as e:
        # 累計讀取超過安全上限：停止處理這個 JAR（已寫出的檔案保留），回報錯誤但不影響其他 JAR
        log_error(
            "[extract] 略過 %s 剩餘內容（累計讀取超過安全上限）: %s",
            jar_path_obj.name,
            e,
        )
        return {
            "status": "error",
            "extracted": extracted_count,
            "skipped": skipped_count,
        }
    except Exception as e:  # noqa: BLE001
        log_error("[extract] %s >>> EXCEPTION: %s", jar_path_obj.name, e)
        return {"status": "error", "extracted": 0, "skipped": 0}


def run_extraction_process_impl(
    mods_dir: str,
    output_dir: str,
    target_regex: re.Pattern,
    process_name: str,
    *,
    find_jar_files_fn: Callable[[str], list[str]],
    extract_from_jar_fn: Callable[[str, str, re.Pattern], dict[str, Any]],
) -> Generator[dict[str, Any], None, None]:
    """實作：對 mods 目錄下所有 JAR 執行批量提取流程。

    使用執行緒池並行處理，並在提取過程中回傳進度。

    Args:
        mods_dir: Mod 資料夾路徑。
        output_dir: 輸出根目錄。
        target_regex: 用以比對要提取之檔案路徑的正規表達式。
        process_name: 處理類型名稱（如 "Lang"、"Patchouli Book"），用於日誌。
        find_jar_files_fn: 用以掃描 JAR 檔案的函式（供測試替換用）。
        extract_from_jar_fn: 用以對單一 JAR 提取檔案的函式（供測試替換用）。

    Yields:
        進度字典，包含 progress（0.0~1.0）欄位。
    """
    from translation_tool.utils.jar_browser import scan_jars

    os.makedirs(output_dir, exist_ok=True)
    jar_files = find_jar_files_fn(mods_dir)
    total_jars = len(jar_files)

    if total_jars == 0:
        log.info("在 '%s' 中未找到任何 .jar 檔案。", mods_dir)
        yield {"progress": 1.0}
        return

    log.info("開始從 %s 個 .jar 檔案中提取 %s 檔案...", total_jars, process_name)
    yield {"progress": 0.0, "log": "[掃描階段] 開始掃描 JAR 檔案..."}

    import threading

    scan_done = threading.Event()
    scan_error = [None]  # 利用 list 可變特性跨執行緒傳遞
    scan_results_local = [{}]  # [0] = dict | None

    def _scan_in_background():
        try:
            # 傳入與實際處理相同的清單（含子目錄 JAR），預掃描才不會與提取階段脫節
            scan_results_local[0] = scan_jars(
                jar_dir=Path(mods_dir),
                patterns=[target_regex.pattern],
                jar_files=jar_files,
            )
        except Exception as e:  # noqa: BLE001
            scan_error[0] = e
        finally:
            scan_done.set()

    scan_thread = threading.Thread(
        target=_scan_in_background, name="scan-jars-bg", daemon=True
    )
    scan_thread.start()

    # 輪詢等待 scan 完成，每 0.5s 检查一次
    import time

    scan_start = time.time()
    last_yielded_at = 0.0
    YIELD_INTERVAL = 5.0  # 節流：每 5 秒才 yield 一次，避免日誌洗版
    while not scan_done.is_set():
        elapsed = time.time() - scan_start
        # 節流 yield：避免 100+ JAR × 30s 掃描產生 ~60 條重複訊息淹沒日誌
        if elapsed - last_yielded_at >= YIELD_INTERVAL:
            last_yielded_at = elapsed
            log.info(
                "[scan_jars] background scanning... elapsed=%.1fs, jar_count=%s",
                elapsed,
                total_jars,
            )
            yield {
                "progress": 0.0,
                "current": 0,
                "total": total_jars,
                "log": f"[掃描階段] 已掃描 {total_jars} 個 JAR ({elapsed:.0f}s)...",
            }
        scan_done.wait(timeout=0.5)
    # 最後一次 yield 確保 UI 收到完成訊號
    elapsed = time.time() - scan_start
    if elapsed - last_yielded_at >= 0:  # 永遠 yield 最終狀態
        last_yielded_at = elapsed
        yield {
            "progress": 0.0,
            "current": 0,
            "total": total_jars,
            "log": f"[掃描階段] 已掃描 {total_jars} 個 JAR ({elapsed:.0f}s)...",
        }

    scan_thread.join()
    elapsed_total = time.time() - scan_start

    if scan_error[0]:
        log_error("[scan_jars] background scan failed: %s", scan_error[0])
        yield {
            "progress": 0.0,
            "error": True,
            "log": f"[錯誤] 掃描失敗: {scan_error[0]}",
        }
        return

    all_scan_results = scan_results_local[0]
    # 預掃描過的 JAR 數（每個被掃描的 JAR 都有預算）與「含可提取內容」的數量分開記錄，
    # 兩者都要能對上 total_jars（issue #111）。
    scanned_jars = len(getattr(all_scan_results, "budgets", None) or {}) or len(
        all_scan_results
    )
    log.info(
        "[scan_jars] 完成，共預掃描 %s / %s 個 JAR（其中 %s 個含可提取內容），耗時 %.1fs",
        scanned_jars,
        total_jars,
        len(all_scan_results),
        elapsed_total,
    )
    yield {
        "progress": 0.0,
        "current": 0,
        "total": total_jars,
        "log": f"[提取階段] 開始提取 ({total_jars} 個 JAR)...",
    }

    processed_count = 0
    total_extracted = 0
    total_skipped = 0
    failed_jars: list[str] = []
    cpu_count = os.cpu_count() or 2
    config_workers = (
        load_config().get("translator", {}).get("parallel_execution_workers")
    )
    if isinstance(config_workers, int) and config_workers > 0:
        max_workers = min(config_workers, 32)
        log.info(
            "[workers] config=%s, actual=%s (cpu_count=%s, capped at 32)",
            config_workers,
            max_workers,
            cpu_count,
        )
    else:
        max_workers = max(1, cpu_count // 2)
        log.info(
            "[workers] default=%s (config invalid/missing, cpu_count=%s)",
            max_workers,
            cpu_count,
        )

    import time as time_module

    _ex_start = time_module.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_jar = {}
        for jar in jar_files:
            _t_jar_submit = time_module.time()
            future_to_jar[
                executor.submit(
                    extract_from_jar_fn, jar, output_dir, target_regex, all_scan_results
                )
            ] = (jar, _t_jar_submit)

        for future in concurrent.futures.as_completed(future_to_jar):
            jar_path, submit_time = future_to_jar[future]
            _t_done = time_module.time()
            wall_time = _t_done - submit_time
            queue_time = submit_time - _ex_start
            processed_count += 1
            prog = processed_count / total_jars
            try:
                result = future.result()
                jar_name = os.path.basename(jar_path)
                if result["status"] != "success":
                    # 損毀或無法讀取的 JAR 必須計入失敗，不能被當成成功
                    failed_jars.append(jar_name)
                    yield {
                        "progress": prog,
                        "current": processed_count,
                        "total": total_jars,
                        "log": f"[ERROR] 無法提取 {jar_name}（JAR 可能已損毀或無法讀取）",
                    }
                    continue
                total_extracted += result["extracted"]
                total_skipped += result["skipped"]
                log.info(
                    "[%s/%s] %s queue=%.1fs wall=%.1fs",
                    processed_count,
                    total_jars,
                    os.path.basename(jar_path),
                    queue_time,
                    wall_time,
                )
                yield {
                    "progress": prog,
                    "current": processed_count,
                    "total": total_jars,
                    "log": f"[{processed_count}/{total_jars}] {os.path.basename(jar_path)}",
                }
            except Exception as exc:  # noqa: BLE001
                failed_jars.append(os.path.basename(jar_path))
                log_error(
                    "提取 %s 時產生例外: %s (wall=%.1fs)",
                    os.path.basename(jar_path),
                    exc,
                    wall_time,
                )
                yield {
                    "progress": prog,
                    "current": processed_count,
                    "total": total_jars,
                    "log": f"[ERROR] 提取 {os.path.basename(jar_path)} 時產生例外",
                }

    summary = (
        f"--- {process_name} 提取完成！ ---\n已檢查 {processed_count}/{total_jars} 個 JAR 檔案。\n"
        f"  - 新提取或更新的檔案: {total_extracted} 個\n"
        f"  - 因內容相同而跳過的檔案: {total_skipped} 個"
    )
    if failed_jars:
        summary += f"\n  - 無法提取的 JAR: {len(failed_jars)} 個（{', '.join(failed_jars[:10])}"
        summary += "…）" if len(failed_jars) > 10 else "）"
    log.info(summary)
    final = {
        "progress": 1.0,
        "current": processed_count,
        "total": total_jars,
        "log": summary,
        "stats": {
            "success": total_extracted,
            "warnings": total_skipped,
            "failures": len(failed_jars),
            "total_files": total_extracted,
        },
    }
    yield final
