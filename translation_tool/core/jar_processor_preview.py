"""translation_tool/core/jar_processor_preview.py 模組。

用途：Mod JAR 檔案的預覽功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Generator, Callable

log = logging.getLogger(__name__)


class ExtractionSummary:
    """提取結果摘要（記錄成功/警告/失敗）。"""

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
) -> Generator[Dict[str, Any], None, None]:
    """產生 JAR 檔案預覽（不實際寫入檔案，僅掃描並回報找到的檔案）。

    Args:
        mods_dir: Mod 目錄路徑。
        mode: 模式（'lang' 或 'book'）。
        find_jar_files_fn: 尋找 JAR 檔案的函式。
        book_path_regex: Book 檔案路徑正則表達式。
        lang_codes: 指定語言代碼列表，若為 None 則從 config 讀取。
    Yields:
        進度字典，含預覽結果（檔案數，大小等）。
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

        target_regex = build_lang_file_regex(codes=lang_codes)
    elif mode == "book":
        target_regex = book_path_regex
    elif mode == "dual":
        from translation_tool.core.jar_processor import build_lang_file_regex

        lang_regex = build_lang_file_regex(codes=lang_codes)
        target_regex = None
    else:
        yield {"error": f"未知模式: {mode}"}
        return

    preview_results = []
    total_files = 0
    total_size_bytes = 0
    failed_jars = []
    total_jars = len(jar_files)
    half_total = total_jars * 2

    for idx, jar_path in enumerate(jar_files):
        jar_name = os.path.basename(jar_path)
        jar_size = os.path.getsize(jar_path)
        lang_matched = []
        book_matched = []
        matched_files = []

        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    normalized_path = member.filename.replace("\\", "/")

                    if mode == "dual":
                        if lang_regex.search(normalized_path):
                            lang_matched.append(normalized_path)
                            total_size_bytes += member.file_size
                        if book_path_regex.search(normalized_path):
                            book_matched.append(normalized_path)
                            total_size_bytes += member.file_size
                    else:
                        if target_regex.search(normalized_path):
                            matched_files.append(normalized_path)
                            total_size_bytes += member.file_size

            if mode == "dual":
                if lang_matched or book_matched:
                    preview_results.append({
                        "jar": jar_name,
                        "lang_files": lang_matched,
                        "book_files": book_matched,
                        "lang_count": len(lang_matched),
                        "book_count": len(book_matched),
                        "size_mb": jar_size / (1024**2),
                    })
                    total_files += len(lang_matched) + len(book_matched)
            else:
                if matched_files:
                    preview_results.append({
                        "jar": jar_name,
                        "files": matched_files,
                        "count": len(matched_files),
                        "size_mb": jar_size / (1024**2),
                    })
                    total_files += len(matched_files)
        except Exception as e:
            log.warning("預覽 %s 時發生錯誤: %s", jar_name, e)
            failed_jars.append({"jar": jar_name, "error": str(e)})

        current = idx + 1
        if mode == "dual":
            progress = current / total_jars
        else:
            progress = current / total_jars
        yield {"progress": progress, "current": current, "total": total_jars}

    # 若有任何 JAR 失敗，progress 低於 1.0；UI 可依 failed_jars 判斷「有失敗但完成了」
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
            "failed_jars": failed_jars,  # 供 UI 判斷是否有 JAR 處理失敗
        },
    }


def generate_preview_report(result: Dict[str, Any], mode: str, output_path: str) -> str:
    """將預覽結果寫入 Markdown 報告檔案。

    報告包含摘要統計與每個 JAR 的檔案清單（最多 50 筆）。

    Args:
        result: 預覽 generator 最終回傳的結果字典（包含 preview_results、total_jars 等）。
        mode: 預覽模式（用於報告檔名）。
        output_path: 報告檔案的輸出目錄。

    Returns:
        產生的報告檔案之完整路徑。
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
