"""translation_tool/core/jar_processor_preview.py 模組。

用途：Mod JAR 檔案的預覽功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。

多執行緒實作：
- 掃描階段由 ThreadPoolExecutor 平行執行 _scan_single_jar_for_preview。
- 主執行緒透過 as_completed 輪詢完成狀態，逐步 yield 進度更新。
- 結果合併在主執行緒完成（所有工作執行緒結束後才執行），避免跨執行緒競爭。

Thread Safety：
- _scan_single_jar_for_preview 為無副作用純函式，執行緒安全。
- 唯一共享狀態 scan_results 由 scan_lock 保護寫入。
- yield 進度更新時仍在主執行緒（as_completed 回呼），UI 只讀取 preview_state，無競爭條件。
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Generator, Callable

from translation_tool.utils.config_manager import load_config

log = logging.getLogger(__name__)


def _get_preview_workers() -> int:
    """從 config 讀取 parallel_execution_workers 作為預覽掃描的執行緒數量。"""
    try:
        config = load_config()
        config_workers = config.get("translator", {}).get("parallel_execution_workers")
        if isinstance(config_workers, int) and config_workers > 0:
            return config_workers
    except Exception:
        pass
    return min(4, os.cpu_count() or 2)


def _scan_single_jar_for_preview(
    jar_path: str,
    mode: str,
    target_regex: re.Pattern | None,
    book_path_regex: re.Pattern | None,
    lang_regex: re.Pattern | None,
) -> Dict[str, Any]:
    """在單一 JAR 中執行預覽比對（ThreadPoolExecutor 並行工作函式）。

    與原本 for-loop 內的邏輯完全一致，只是包成可並行的純函式。
    無任何副作用，不修改任何共享狀態，適合多執行緒並行呼叫。

    參數：
        jar_path：JAR 檔案的完整路徑。
        mode：'lang' | 'book' | 'dual'，決定比對邏輯。
        target_regex：lang/book 單一模式時使用的正則表達式，dual 模式為 None。
        book_path_regex：dual 模式時的 book 路徑正則，單一模式為 None。
        lang_regex：dual 模式時的 lang 路徑正則，單一模式為 None。

    回傳：
        dict，lang/book 模式：{jar, matched_files, count, size_mb, error}
        dual 模式：{jar, lang_matched, book_matched, lang_count, book_count, size_mb, error}
    """
    jar_name = os.path.basename(jar_path)
    jar_size = os.path.getsize(jar_path)

    matched_bytes = 0
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            if mode == "dual":
                lang_matched = []
                book_matched = []
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    normalized_path = member.filename.replace("\\", "/")
                    matched_lang = lang_regex.search(normalized_path)
                    matched_book = book_path_regex.search(normalized_path)
                    if matched_lang:
                        lang_matched.append(normalized_path)
                        matched_bytes += member.file_size
                    if matched_book:
                        book_matched.append(normalized_path)
                        matched_bytes += member.file_size
                return {
                    "jar": jar_name,
                    "lang_matched": lang_matched,
                    "book_matched": book_matched,
                    "lang_count": len(lang_matched),
                    "book_count": len(book_matched),
                    "size_mb": matched_bytes / (1024**2),
                    "jar_size_mb": jar_size / (1024**2),
                    "error": None,
                }
            else:
                matched_files = []
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    normalized_path = member.filename.replace("\\", "/")
                    if target_regex.search(normalized_path):
                        matched_files.append(normalized_path)
                        matched_bytes += member.file_size
                return {
                    "jar": jar_name,
                    "matched_files": matched_files,
                    "count": len(matched_files),
                    "size_mb": matched_bytes / (1024**2),
                    "jar_size_mb": jar_size / (1024**2),
                    "error": None,
                }
    except Exception as e:
        log.warning("預覽 %s 時發生錯誤: %s", jar_name, e)
        return {
            "jar": jar_name,
            "matched_files": [],
            "lang_matched": [],
            "book_matched": [],
            "count": 0,
            "lang_count": 0,
            "book_count": 0,
            "size_mb": jar_size / (1024**2),
            "error": str(e),
        }


class ExtractionSummary:
    """提取結果摘要（記錄成功/警告/失敗）。

    Attributes：
        success：成功提取的 JAR 清單，每筆包含 jar 和 files 欄位。
        warnings：警告的 JAR 清單，每筆包含 jar 和 reason 欄位。
        failures：失敗的 JAR 清單，每筆包含 jar 和 error 欄位。
    """

    def __init__(self):
        self.success = []
        self.warnings = []
        self.failures = []

    def add_success(self, jar_name: str, file_count: int):
        self.success.append({"jar": jar_name, "files": file_count})

    def add_warning(self, jar_name: str, reason: str):
        self.warnings.append({"jar": jar_name, "reason": reason})

    def add_failure(self, jar_name: str, error: str):
        self.failures.append({"jar": jar_name, "error": error})

    def get_summary(self) -> Dict[str, Any]:
        return {
            "success_count": len(self.success),
            "warning_count": len(self.warnings),
            "failure_count": len(self.failures),
            "success": self.success,
            "warnings": self.warnings[:5],
            "failures": self.failures[:5],
        }


def preview_extraction_generator_impl(
    mods_dir: str,
    mode: str,
    *,
    find_jar_files_fn: Callable[[str], list[str]],
    book_path_regex: re.Pattern,
    lang_codes: list[str] | None = None,
    skip_zh_cn: bool = False,
) -> Generator[Dict[str, Any], None, None]:
    """產生 JAR 檔案預覽（多執行緒平行掃描版本）。

    流程：
        1. find_jar_files_fn 收集所有待掃描的 JAR 路徑。
        2. 建立 ThreadPoolExecutor，以 _get_preview_workers() 為執行緒數上限。
        3. 所有 JAR 由執行緒池並行掃描；主執行緒以 as_completed 輪詢完成的未來物件，
           每完成一個即時 yield 進度更新（progress, current, total, log）。
        4. 所有 JAR 完成後，在主執行緒合併掃描結果並 yield 最終報告。

    參數：
        mods_dir：Mod 目錄路徑。
        mode：'lang' | 'book' | 'dual'。
        find_jar_files_fn：回傳 JAR 路徑列表的無參函式。
        book_path_regex：Book 檔案路徑正則表達式（用於 dual 模式）。
        lang_codes：指定語言代碼列表，若為 None 則從 config 讀取。
        skip_zh_cn：是否跳過 zh_cn（preview 路徑也支援,
            跟 extract 路徑行為一致 — 2026-07-14 user review 補發現）。
            注意:book mode 不受 skip_zh_cn 影響(跟 extract 一致)。

    Yields：
        各階段進度字典：
        - 每個 JAR 完成時：{'progress': float, 'current': int, 'total': int, 'log': str}
        - 所有 JAR 完成後（最終）：{'progress': float, 'current': int, 'total': int,
                  'result': {...}, 'log': str}，其中 result 包含
                  total_jars, preview_results, total_files, total_size_mb, failed_jars。
                  注意：total_size_mb 是所有 matched 檔案大小的總和（bytes sum），
                  不是整個 JAR 大小——反映實際抽取量。
        - 目錄為空時：{'progress': 1.0, 'result': {...}}（直接結束）。
        - mode 無效時：{'error': str}（直接結束）。
    """
    jar_files = find_jar_files_fn(mods_dir)
    total_jars = len(jar_files)

    if total_jars == 0:
        yield {
            "progress": 1.0,
            "result": {
                "total_jars": 0,
                "preview_results": [],
                "total_files": 0,
                "total_size_mb": 0,
            },
        }
        return

    if mode == "lang":
        from translation_tool.core.jar_processor import build_lang_file_regex
        target_regex = build_lang_file_regex(codes=lang_codes, skip_zh_cn=skip_zh_cn)
        lang_regex = None
    elif mode == "book":
        from translation_tool.core.jar_processor import build_book_path_regex
        target_regex = build_book_path_regex(codes=lang_codes, skip_zh_cn=skip_zh_cn)
        lang_regex = None
    elif mode == "dual":
        from translation_tool.core.jar_processor import build_lang_file_regex
        lang_regex = build_lang_file_regex(codes=lang_codes, skip_zh_cn=skip_zh_cn)
        target_regex = None
    else:
        yield {"error": f"未知模式: {mode}"}
        return

    # ---- 多執行緒掃描階段 ----
    workers = _get_preview_workers()
    log.info("[preview] 開始多執行緒預覽掃描，JAR 數量=%d，workers=%d", total_jars, workers)

    scan_results: dict[str, Dict[str, Any]] = {}
    scan_lock = threading.Lock()  # 保護 scan_results 的寫入
    done_count = [0]  # 已完成的 JAR 數量（用 list 包裝以便跨執行緒修改）

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_jar = {
            executor.submit(
                _scan_single_jar_for_preview,
                jar_path,
                mode,
                target_regex,
                book_path_regex,
                lang_regex,
            ): jar_path
            for jar_path in jar_files
        }

        for future in as_completed(future_to_jar):
            jar_path = future_to_jar[future]
            try:
                result = future.result()
            except Exception as e:
                log.warning("預覽 %s 時發生例外: %s", jar_path, e)
                result = {
                    "jar": os.path.basename(jar_path),
                    "matched_files": [],
                    "lang_matched": [],
                    "book_matched": [],
                    "count": 0,
                    "lang_count": 0,
                    "book_count": 0,
                    "size_mb": 0,
                    "error": str(e),
                }

            with scan_lock:
                scan_results[result["jar"]] = result
                done_count[0] += 1

            processed = done_count[0]
            progress = processed / total_jars
            yield {
                "progress": progress,
                "current": processed,
                "total": total_jars,
                "log": f"[預覽掃描] {processed}/{total_jars} ({result['jar']})",
            }

    # ---- 合併結果 ----
    log.info("[preview] 掃描完成，開始合併結果...")
    preview_results = []
    total_files = 0
    total_size_bytes = 0
    failed_jars = []

    for jar_name, result in scan_results.items():
        if result.get("error"):
            failed_jars.append({"jar": result["jar"], "error": result["error"]})

        if mode == "dual":
            lang_count = result.get("lang_count", 0)
            book_count = result.get("book_count", 0)
            preview_results.append({
                "jar": result["jar"],
                "lang_files": result.get("lang_matched", []),
                "book_files": result.get("book_matched", []),
                "lang_count": lang_count,
                "book_count": book_count,
                "size_mb": result["size_mb"],
            })
            total_files += lang_count + book_count
        else:
            count = result.get("count", 0)
            preview_results.append({
                "jar": result["jar"],
                "files": result.get("matched_files", []),
                "count": count,
                "size_mb": result["size_mb"],
            })
            total_files += count

        total_size_bytes += int(result["size_mb"] * (1024**2))

    final_progress = (
        (total_jars - len(failed_jars)) / total_jars if failed_jars else 1.0
    )
    yield {
        "progress": final_progress,
        "current": total_jars,
        "total": total_jars,
        "result": {
            "total_jars": total_jars,
            "preview_results": preview_results,
            "total_files": total_files,
            "total_size_mb": total_size_bytes / (1024**2),
            "failed_jars": failed_jars,
        },
        "log": f"[預覽完成] 找到 {total_files} 個檔案，{len(failed_jars)} 個 JAR 失敗",
    }


def generate_preview_report(result: Dict[str, Any], mode: str, output_path: str) -> str:
    """將預覽結果寫入 Markdown 報告檔案。

    報告包含摘要統計與每個 JAR 的檔案清單（超過 50 筆時截斷）。

    參數：
        result：預覽 generator 最終回傳的結果字典，需包含
            preview_results, total_jars, total_files, total_size_mb。
        mode：預覽模式（'lang' | 'book' | 'dual'），用於報告標題與檔名。
        output_path：報告檔案的輸出目錄，會自動建立多層目錄。

    回傳：
        產生的報告檔案之完整路徑（str）。
    """
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"preview_report_{mode}_{timestamp}.md"
    report_path = output_dir / report_filename

    preview_results = result.get("preview_results", [])
    total_jars = result.get("total_jars", 0)
    total_files = result.get("total_files", 0)
    total_size_mb = result.get("total_size_mb", 0)

    report_lines = [
        f"# JAR 提取預覽報告 - {mode.upper()}",
        "",
        f"**生成時間：** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 摘要統計",
        "",
        f"- **總 JAR 數量：** {total_jars}",
        f"- **找到檔案數量：** {total_files}",
        f"- **有檔案的 JAR：** {len(preview_results)}",
        f"- **總大小：** {total_size_mb:.2f} MB",
        "",
        "## 詳細清單",
        "",
    ]

    # dual mode 使用 lang_count/book_count，否則使用 count
    for idx, r in enumerate(preview_results, 1):
        report_lines.append(f"### {idx}. {r['jar']}")
        report_lines.append("")
        if mode == "dual":
            report_lines.append(f"- **Lang 檔案：** {r.get('lang_count', 0)}")
            report_lines.append(f"- **Book 檔案：** {r.get('book_count', 0)}")
            files = r.get('lang_files', []) + r.get('book_files', [])
        else:
            report_lines.append(f"- **檔案數量：** {r.get('count', 0)}")
            files = r.get('files', [])
        report_lines.append(f"- **JAR 大小：** {r['size_mb']:.2f} MB")
        report_lines.append("- **檔案清單：**")
        report_lines.append("")
        for file_path in files[:50]:
            report_lines.append(f"  - `{file_path}`")
        if len(files) > 50:
            report_lines.append(f"  - ... 還有 {len(files) - 50} 個檔案")
        report_lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    log.info("✅ 預覽報告生成成功：%s", report_path)
    return str(report_path)
