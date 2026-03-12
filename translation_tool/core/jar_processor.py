"""translation_tool/core/jar_processor.py 模組。

用途：作為 jar discovery / extract / preview 的相容入口。
維護注意：主要實作已拆到 jar_processor_discovery / extract / preview 子模組。
"""

import re
from typing import Dict, Any, Generator

from translation_tool.core.jar_processor_discovery import find_jar_files
from translation_tool.core.jar_processor_extract import (
    extract_from_jar_impl,
    run_extraction_process_impl,
)
from translation_tool.core.jar_processor_preview import (
    ExtractionSummary,
    generate_preview_report,
    preview_extraction_generator_impl,
)


BOOK_PATH_REGEX_DUAL_STRUCTURE = re.compile(
    rf"(assets|data)/([^/]+)/"
    rf"(patchouli_books|book|manual|guidebook)/"
    rf"(?:([^/]+)/)?"
    rf"(?:"
    rf"(_?(?:en_us|zh_tw|zh_cn))(/.*)?"
    rf"|"
    rf"book\.json"
    rf")$",
    re.IGNORECASE,
)


LANG_CODES = ["en_us", "zh_tw", "zh_cn"]
lang_pattern = r"_?(?:" + "|".join(map(re.escape, LANG_CODES)) + r")"


def _extract_from_jar(jar_path: str, output_root: str, target_regex: re.Pattern) -> Dict[str, Any]:
    return extract_from_jar_impl(jar_path, output_root, target_regex)


def _run_extraction_process(
    mods_dir: str, output_dir: str, target_regex: re.Pattern, process_name: str
) -> Generator[Dict[str, Any], None, None]:
    yield from run_extraction_process_impl(
        mods_dir,
        output_dir,
        target_regex,
        process_name,
        find_jar_files_fn=find_jar_files,
        extract_from_jar_fn=_extract_from_jar,
    )


def extract_lang_files_generator(mods_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
    lang_file_regex = re.compile(
        r"(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$", re.IGNORECASE
    )
    yield from _run_extraction_process(
        mods_dir=mods_dir,
        output_dir=output_dir,
        target_regex=lang_file_regex,
        process_name="Lang",
    )


def extract_book_files_generator(mods_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
    yield from _run_extraction_process(
        mods_dir,
        output_dir,
        BOOK_PATH_REGEX_DUAL_STRUCTURE,
        "Patchouli Book",
    )


def preview_extraction_generator(mods_dir: str, mode: str) -> Generator[Dict[str, Any], None, None]:
    yield from preview_extraction_generator_impl(
        mods_dir,
        mode,
        find_jar_files_fn=find_jar_files,
        book_path_regex=BOOK_PATH_REGEX_DUAL_STRUCTURE,
    )


__all__ = [
    "find_jar_files",
    "_extract_from_jar",
    "_run_extraction_process",
    "extract_lang_files_generator",
    "extract_book_files_generator",
    "preview_extraction_generator",
    "ExtractionSummary",
    "generate_preview_report",
    "BOOK_PATH_REGEX_DUAL_STRUCTURE",
    "LANG_CODES",
    "lang_pattern",
]
