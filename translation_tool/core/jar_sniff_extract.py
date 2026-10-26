"""jar_sniff_extract.py

提取資料夾名稱沒對上的手冊／指南檔（依內容辨識，不需要設定資料夾名稱）。

``jar_processor`` 的 Book 提取只認得資料夾名稱設定；這裡在它之後補一輪：
讀每個 jar 的檔案內容，用 ``translation_db.sniff_groups`` 同一套判斷找出手冊 JSON 與指南
（``.md``／``.txt``），把原文與要提取的語系變體寫到同一個輸出資料夾，路徑慣例與
``jar_processor_extract`` 相同（``assets/`` 底下照原路徑，其他放進 ``<模組>_extracted/``）。
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from translation_tool.core.jar_processor_extract import (
    _normalize_jar_base_name,
    get_file_hash,
)
from translation_tool.translation_db.schema import KIND_GUIDE, KIND_PATCHOULI
from translation_tool.translation_db.sniff_groups import sniffed_groups
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.log_unit import log_error, log_warning
from translation_tool.utils.zip_safety import (
    MAX_FILE_BYTES,
    UnsafePathError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
    safe_join,
)

_BOOK_KINDS = (KIND_PATCHOULI, KIND_GUIDE)


def _wanted_langs(lang_codes: list[str], skip_zh_cn: bool) -> set[str]:
    langs = {"en_us"}
    langs.update(c for c in lang_codes if c in ("zh_tw", "zh_cn"))
    if skip_zh_cn:
        langs.discard("zh_cn")
    return langs


def _output_path(output_root: str, jar_base: str, member: str) -> str:
    if member.startswith("assets/"):
        return safe_join(output_root, member)
    return safe_join(output_root, f"{jar_base}_extracted", member)


def extract_sniffed_from_jar(
    jar_path: str,
    output_root: str,
    claimed: Callable[[str], bool],
    langs: set[str],
) -> tuple[int, int]:
    """回傳 ``(寫出, 略過)``。``claimed(名稱)`` 為真的檔案已由名稱規則提取，不重複處理。"""
    written = skipped = 0
    jar_base = _normalize_jar_base_name(jar_path)
    with zipfile.ZipFile(jar_path) as zf:
        names = zf.namelist()
        taken = {n for n in names if claimed(n)}
        diagnostics: list[str] = []
        groups = sniffed_groups(zf, names, taken, diagnostics=diagnostics)
        for member in dict.fromkeys(diagnostics):
            log_warning("[Sniff] 略過無法解析的書籍 JSON：%s（來源 %s）", member, jar_path)
        budget = ZipReadBudget(label=os.path.basename(jar_path))
        for (kind, _mod, _key), members in groups.items():
            if kind not in _BOOK_KINDS:
                continue
            for lang, member in members.items():
                if lang not in langs:
                    continue
                try:
                    target = _output_path(output_root, jar_base, member)
                    data = read_limited(zf, member, MAX_FILE_BYTES, budget=budget)
                except UnsafePathError:
                    log_error("拒絕提取不安全路徑: %s（來源 %s）", member, jar_path)
                    skipped += 1
                    continue
                except ZipSizeError as exc:
                    log_error("略過過大檔案 %s: %s", member, exc)
                    skipped += 1
                    continue
                if os.path.exists(target):
                    with open(target, "rb") as existing:
                        if get_file_hash(existing.read()) == get_file_hash(data):
                            skipped += 1
                            continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as out:
                    out.write(data)
                written += 1
    return written, skipped


def extract_sniffed_book_files(
    jar_paths: list[str],
    output_dir: str,
    claimed: Callable[[str], bool],
    *,
    lang_codes: list[str],
    skip_zh_cn: bool,
) -> Generator[dict[str, Any], None, None]:
    """逐個 jar 補提取依內容辨識的手冊／指南，產生進度更新（只含 ``log``）。"""
    langs = _wanted_langs(lang_codes, skip_zh_cn)
    total_written = 0
    for jar_path in jar_paths:
        raise_if_cancelled()
        try:
            written, _skipped = extract_sniffed_from_jar(
                jar_path, output_dir, claimed, langs
            )
        except (zipfile.BadZipFile, OSError) as exc:
            log_error("[Sniff] 無法讀取 %s：%s", jar_path, exc)
            continue
        if written:
            total_written += written
            yield {"log": f"[依內容辨識] {Path(jar_path).name}：提取 {written} 個檔案"}
    yield {"log": f"[依內容辨識] 共補提取 {total_written} 個手冊／指南檔"}
