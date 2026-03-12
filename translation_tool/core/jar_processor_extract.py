from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import re
import zipfile
from typing import Any, Dict, Generator, Callable

from ..utils.config_manager import load_config

log = logging.getLogger(__name__)

VERSION_REGEX = re.compile(
    r"[-_](?:[a-zA-Z]+-)?\d+(?:\.\d+)+(?:[-_.][a-zA-Z0-9]+)*$",
    re.IGNORECASE,
)


def get_file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_jar_base_name(jar_filename: str) -> str:
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
    extracted_count = 0
    skipped_count = 0
    jar_filename_base = _normalize_jar_base_name(jar_path)

    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                normalized_path = member.filename.replace('\\', '/')
                if not target_regex.search(normalized_path):
                    continue

                if normalized_path.startswith('assets/'):
                    final_output_path = os.path.join(output_root, normalized_path)
                else:
                    final_mod_folder = f"{jar_filename_base}_extracted"
                    final_output_path = os.path.join(output_root, final_mod_folder, normalized_path)

                with zf.open(member) as source:
                    source_data = source.read()
                    source_hash = get_file_hash_fn(source_data)

                if os.path.exists(final_output_path):
                    with open(final_output_path, 'rb') as existing_file:
                        existing_hash = get_file_hash_fn(existing_file.read())
                    if source_hash == existing_hash:
                        skipped_count += 1
                        continue

                os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
                with open(final_output_path, 'wb') as target:
                    target.write(source_data)
                extracted_count += 1

        return {'status': 'success', 'extracted': extracted_count, 'skipped': skipped_count}
    except Exception as e:
        log.error("處理 %s 時發生錯誤: %s", os.path.basename(jar_path), e)
        return {'status': 'error', 'extracted': 0, 'skipped': 0}


def run_extraction_process_impl(
    mods_dir: str,
    output_dir: str,
    target_regex: re.Pattern,
    process_name: str,
    *,
    find_jar_files_fn: Callable[[str], list[str]],
    extract_from_jar_fn: Callable[[str, str, re.Pattern], Dict[str, Any]],
) -> Generator[Dict[str, Any], None, None]:
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
    max_workers = load_config().get('translation', {}).get('parallel_execution_workers') or os.cpu_count()

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
