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
from pathlib import Path
from typing import Any, Dict, Generator, Callable

from ..utils.config_manager import load_config
from ..utils.log_unit import log_info, log_warning, log_error

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
    *,
    get_file_hash_fn: Callable[[bytes], str] = get_file_hash,
) -> Dict[str, Any]:
    """從單一 JAR 檔案中抽取符合正規表達式的檔案內容。

    僅處理 assets/ 開頭或非 assets 的普通資源檔，
    輸出時保留原始目錄結構。HASH 相同者自動跳過（增量更新）。

    內部實作（PR #55 重構）：
    - 使用 `jar_browser.scan_jars()` 讀取符合 pattern 的文字檔內容，
      由 jar_browser 的 ThreadPoolExecutor 統一管理多執行緒。
    - Binary 檔案（UTF-8 decode 失敗）則 fallback 直接讀取 ZIP，
      以確保 hash 計算和寫入行為與重構前完全一致。

    Args:
        jar_path: JAR 檔案路徑
        output_root: 輸出根目錄
        target_regex: 用來過濾感興趣檔案的正規表達式
        get_file_hash_fn: 檔案 HASH 計算函式（預設 SHA-256）
    Returns:
        包含 extracted/skipped 的統計字典
    """
    from translation_tool.utils.jar_browser import scan_jars

    extracted_count = 0
    skipped_count = 0
    jar_filename_base = _normalize_jar_base_name(jar_path)
    jar_path_obj = Path(jar_path)

    # 如果 jar_path 不存在（被刪除或路徑錯誤），直接返回錯誤
    if not jar_path_obj.exists():
        log.error("JAR 檔案不存在: %s", jar_path)
        return {"status": "error", "extracted": 0, "skipped": 0}

    try:
        # 使用 jar_browser.scan_jars() 讀取文字檔（由 jar_browser 管理執行緒）
        # target_regex.pattern 取出原生 str，正則行為與原本一致
        jar_results: dict[str, str | None] = {}
        scan_results = scan_jars(
            jar_dir=jar_path_obj.parent,
            patterns=[target_regex.pattern],
        )
        # jar_browser.scan_jars() 回傳 dict[Path, dict[str, str | None]]
        # 只取我們感興趣的這個 JAR 的結果
        if jar_path_obj in scan_results:
            jar_results = scan_results[jar_path_obj]
        else:
            # JAR 不在 scan_jars 的結果中（可能全部失敗或無符合），走 fallback
            jar_results = {}

        # 同時用 ZIP 直接列出所有符合的成員（包含 binary 檔案路徑）
        # 確保 binary 檔案不會因為 jar_browser 回傳 None 而漏掉
        with zipfile.ZipFile(jar_path, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                normalized_path = member.filename.replace("\\", "/")
                if not target_regex.search(normalized_path):
                    continue

                # 決定輸出路徑（與原本邏輯完全一致）
                if normalized_path.startswith("assets/"):
                    final_output_path = os.path.join(output_root, normalized_path)
                else:
                    final_mod_folder = f"{jar_filename_base}_extracted"
                    final_output_path = os.path.join(
                        output_root, final_mod_folder, normalized_path
                    )

                # C-4 修復：路徑遍歷防護，拒絕試圖寫入 output_root 外的檔案
                abs_output = os.path.abspath(final_output_path)
                abs_root = os.path.abspath(output_root)
                if not abs_output.startswith(abs_root + os.sep) and abs_output != abs_root:
                    log_warning(
                        f"[jar_extract] ⚠️ 路徑遍歷攻擊偵測：拒絕寫入 {abs_output}（位於 output_root 之外）"
                    )
                    continue

                # 優先使用 jar_browser 的結果（文字檔）
                if normalized_path in jar_results and jar_results[normalized_path] is not None:
                    source_data = jar_results[normalized_path].encode("utf-8")
                else:
                    # Binary 檔案或 jar_browser 未找到：直接讀 ZIP
                    # C-6 修復：讀取前先檢查成員的 header file_size，防止大型 binary 檔案耗盡記憶體
                    # P1 修復：若 jar_browser 因大小限制跳過了文字檔（path in jar_results but None），
                    #          fallback 直接讀 ZIP 時也必須檢查大小（用保守的 10MB 閾值）
                    _MAX_BINARY_SIZE = 100 * 1024 * 1024  # 100MB
                    _MAX_FALLBACK_SIZE = 10 * 1024 * 1024  # 10MB，fallback 保守限制
                    # jar_browser 跳過的大型文字檔也會出現在 jar_results 中（值為 None）
                    is_jar_browser_skipped = (
                        normalized_path in jar_results and jar_results[normalized_path] is None
                    )
                    if is_jar_browser_skipped or normalized_path not in jar_results:
                        # fallback 讀取：套用保守的 10MB 限制（適用於文字檔）
                        if member.file_size > _MAX_FALLBACK_SIZE:
                            log_warning(
                                f"[jar_extract] ⚠️ 拒絕讀取過大檔案（fallback）：{normalized_path}"
                                f"（{member.file_size / 1024 / 1024:.1f}MB > 10MB）"
                            )
                            continue
                    elif member.file_size > _MAX_BINARY_SIZE:
                        log_warning(
                            f"[jar_extract] ⚠️ 拒絕讀取過大檔案：{normalized_path}"
                            f"（{member.file_size / 1024 / 1024:.1f}MB > 100MB）"
                        )
                        continue
                    with zf.open(member) as source:
                        source_data = source.read()

                source_hash = get_file_hash_fn(source_data)

                # 增量更新：hash 相同則跳過
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

        return {"status": "success", "extracted": extracted_count, "skipped": skipped_count}
    except Exception as e:
        log.error("處理 %s 時發生錯誤: %s", os.path.basename(jar_path), e)
        return {"status": "error", "extracted": 0, "skipped": 0}

def run_extraction_process_impl(
    mods_dir: str,
    output_dir: str,
    target_regex: re.Pattern,
    process_name: str,
    *,
    find_jar_files_fn: Callable[[str], list[str]],
    extract_from_jar_fn: Callable[[str, str, re.Pattern], Dict[str, Any]],
) -> Generator[Dict[str, Any], None, None]:
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
    os.makedirs(output_dir, exist_ok=True)
    jar_files = find_jar_files_fn(mods_dir)
    total_jars = len(jar_files)

    if total_jars == 0:
        log.info("在 '%s' 中未找到任何 .jar 檔案。", mods_dir)
        yield {'progress': 1.0}
        return

    log.info("開始從 %s 個 .jar 檔案中提取 %s 檔案...", total_jars, process_name)
    yield {'progress': 0.0}

    processed_count = 0
    total_extracted = 0
    total_skipped = 0
    cpu_count = os.cpu_count() or 2
    max_allowed_workers = max(1, cpu_count // 2)
    config_workers = load_config().get("translator", {}).get("parallel_execution_workers")
    if isinstance(config_workers, int) and config_workers > 0:
        max_workers = min(config_workers, max_allowed_workers)
    else:
        max_workers = max_allowed_workers

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_jar = {
            executor.submit(extract_from_jar_fn, jar, output_dir, target_regex): jar
            for jar in jar_files
        }
        for future in concurrent.futures.as_completed(future_to_jar):
            jar_path = future_to_jar[future]
            processed_count += 1
            prog = processed_count / total_jars
            try:
                result = future.result()
                if result['status'] == 'success':
                    total_extracted += result['extracted']
                    total_skipped += result['skipped']
                log.info("[%s/%s] %s", processed_count, total_jars, os.path.basename(jar_path))
                yield {'progress': prog}
            except Exception as exc:
                log.error("提取 %s 時產生例外: %s", os.path.basename(jar_path), exc)
                yield {'progress': prog}

    log.info(
        "--- %s 提取完成！ ---\n已檢查 %s/%s 個 JAR 檔案。\n  - 新提取或更新的檔案: %s 個\n  - 因內容相同而跳過的檔案: %s 個",
        process_name,
        processed_count,
        total_jars,
        total_extracted,
        total_skipped,
    )
    yield {'progress': 1.0}
