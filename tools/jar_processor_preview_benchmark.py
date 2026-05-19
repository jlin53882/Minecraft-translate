"""jar_processor_preview_benchmark.py
效能瓶頸測試腳本：測量 preview_extraction_generator 各階段耗時

用法：
  python tools/jar_processor_preview_benchmark.py "C:/path/to/mods"
"""
import time
import sys
import os
import re
from pathlib import Path

# 確保可以 import 專案內的模組
sys.path.insert(0, str(Path(__file__).parent.parent))

from translation_tool.core.jar_processor import (
    find_jar_files,
    build_lang_file_regex,
    build_book_path_regex,
    BOOK_PATH_REGEX_DUAL_STRUCTURE,
)


def benchmark_preview(mods_dir: str, mode: str = "lang", lang_codes: list[str] | None = None):
    """測量 preview 各階段耗時"""
    import zipfile

    jar_files = find_jar_files(mods_dir)
    total_jars = len(jar_files)
    print(f"JAR 數量: {total_jars}")
    print(f"模式: {mode}")
    if lang_codes:
        print(f"lang_codes: {lang_codes}")
    print()

    if lang_codes:
        lang_regex = build_lang_file_regex(codes=lang_codes)
    else:
        lang_regex = build_lang_file_regex()
    book_regex = build_book_path_regex(codes=lang_codes)

    t0_find_jars = time.time()
    t1_find_jars = time.time()
    print(f"[1] find_jar_files: {t1_find_jars - t0_find_jars:.3f}s")

    t0_total = time.time()
    t0_zip_open = 0.0
    t0_regex_total = 0.0
    t0_infolist_total = 0.0
    matched_total = 0

    for idx, jar_path in enumerate(jar_files):
        t_zip = time.time()
        with zipfile.ZipFile(jar_path, "r") as zf:
            t0_infolist = time.time()
            members = zf.infolist()
            t1_infolist = time.time()
            t0_infolist_total += t1_infolist - t0_infolist

            if mode == "lang":
                target_regex = lang_regex
            elif mode == "book":
                target_regex = book_regex
            elif mode == "dual":
                target_regex = None
            else:
                target_regex = lang_regex

            t_regex = time.time()
            for member in members:
                if member.is_dir():
                    continue
                normalized_path = member.filename.replace("\\", "/")
                if mode == "dual":
                    lang_regex.search(normalized_path)
                    book_regex.search(normalized_path)
                else:
                    target_regex.search(normalized_path)
            t0_regex_total += time.time() - t_regex

            t0_zip_open += time.time() - t_zip

        if (idx + 1) % 50 == 0:
            print(f"  處理中... {idx + 1}/{total_jars}")

    t1_total = time.time()

    print()
    print(f"[2] zipfile.ZipFile open + infolist: {t0_infolist_total:.3f}s ({t0_infolist_total / (t1_total - t0_total) * 100:.1f}%)")
    print(f"[3] regex.search 總計: {t0_regex_total:.3f}s ({t0_regex_total / (t1_total - t0_total) * 100:.1f}%)")
    print(f"[4] zip open 總計: {t0_zip_open:.3f}s")
    print(f"[5] 總耗時: {t1_total - t0_total:.3f}s")
    print(f"    平均每 JAR: {(t1_total - t0_total) / total_jars * 1000:.1f}ms")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    mods_dir = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "lang"
    lang_codes = None

    if not os.path.isdir(mods_dir):
        print(f"錯誤：{mods_dir} 不是有效目錄")
        sys.exit(1)

    benchmark_preview(mods_dir, mode, lang_codes)


if __name__ == "__main__":
    main()