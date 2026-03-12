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
        self.success.append({'jar': jar_name, 'files': file_count})

    def add_warning(self, jar_name: str, reason: str):
        self.warnings.append({'jar': jar_name, 'reason': reason})

    def add_failure(self, jar_name: str, error: str):
        self.failures.append({'jar': jar_name, 'error': error})

    def get_summary(self) -> Dict[str, Any]:
        return {
            'success_count': len(self.success),
            'warning_count': len(self.warnings),
            'failure_count': len(self.failures),
            'success': self.success,
            'warnings': self.warnings[:5],
            'failures': self.failures[:5],
        }


def preview_extraction_generator_impl(
    mods_dir: str,
    mode: str,
    *,
    find_jar_files_fn: Callable[[str], list[str]],
    book_path_regex: re.Pattern,
) -> Generator[Dict[str, Any], None, None]:
    jar_files = find_jar_files_fn(mods_dir)
    total_jars = len(jar_files)

    if total_jars == 0:
        yield {'progress': 1.0, 'result': {'total_jars': 0, 'preview_results': [], 'total_files': 0, 'total_size_mb': 0}}
        return

    if mode == 'lang':
        target_regex = re.compile(r"(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$", re.IGNORECASE)
    elif mode == 'book':
        target_regex = book_path_regex
    else:
        yield {'error': f'未知模式: {mode}'}
        return

    preview_results = []
    total_files = 0
    total_size_bytes = 0

    for idx, jar_path in enumerate(jar_files, 1):
        jar_name = os.path.basename(jar_path)
        jar_size = os.path.getsize(jar_path)
        matched_files = []
        try:
            with zipfile.ZipFile(jar_path, 'r') as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    normalized_path = member.filename.replace('\\', '/')
                    if target_regex.search(normalized_path):
                        matched_files.append(normalized_path)
                        total_size_bytes += member.file_size
            if matched_files:
                preview_results.append({'jar': jar_name, 'files': matched_files, 'count': len(matched_files), 'size_mb': jar_size / (1024**2)})
                total_files += len(matched_files)
        except Exception as e:
            log.warning("預覽 %s 時發生錯誤: %s", jar_name, e)

        yield {'progress': idx / total_jars, 'current': idx, 'total': total_jars}

    yield {
        'progress': 1.0,
        'current': total_jars,
        'total': total_jars,
        'result': {
            'total_jars': total_jars,
            'preview_results': preview_results,
            'total_files': total_files,
            'total_size_mb': total_size_bytes / (1024**2),
        },
    }


def generate_preview_report(result: Dict[str, Any], mode: str, output_path: str) -> str:
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    report_filename = f'preview_report_{mode}_{timestamp}.md'
    report_path = output_dir / report_filename

    preview_results = result.get('preview_results', [])
    total_jars = result.get('total_jars', 0)
    total_files = result.get('total_files', 0)
    total_size_mb = result.get('total_size_mb', 0)

    report_lines = [
        f'# JAR 提取預覽報告 - {mode.upper()}',
        '',
        f"**生成時間：** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        '',
        '## 摘要統計',
        '',
        f'- **總 JAR 數量：** {total_jars}',
        f'- **找到檔案數量：** {total_files}',
        f'- **有檔案的 JAR：** {len(preview_results)}',
        f'- **總大小：** {total_size_mb:.2f} MB',
        '',
        '## 詳細清單',
        '',
    ]

    for idx, r in enumerate(preview_results, 1):
        report_lines.append(f"### {idx}. {r['jar']}")
        report_lines.append('')
        report_lines.append(f"- **檔案數量：** {r['count']}")
        report_lines.append(f"- **JAR 大小：** {r['size_mb']:.2f} MB")
        report_lines.append('- **檔案清單：**')
        report_lines.append('')
        for file_path in r['files'][:50]:
            report_lines.append(f"  - `{file_path}`")
        if len(r['files']) > 50:
            report_lines.append(f"  - ... 還有 {len(r['files']) - 50} 個檔案")
        report_lines.append('')

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))

    log.info("✅ 預覽報告生成成功：%s", report_path)
    return str(report_path)
