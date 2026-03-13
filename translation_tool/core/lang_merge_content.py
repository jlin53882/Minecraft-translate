"""translation_tool/core/lang_merge_content.py 模組。

用途：作為 lang merge content layer 的相容 façade，維持既有 import surface。
維護注意：主要實作已拆到 lang_merge_content_* / lang_merge_pending 子模組。
"""

from __future__ import annotations

import logging

import orjson as json

from ..utils.config_manager import load_config
from ..utils.text_processor import apply_replace_rules  # noqa: F401
from ..utils.text_processor import recursive_translate_dict
from .lang_codec import normalize_patchouli_book_root
from .lang_processing_format import get_text_processor
from .lang_merge_content_copy import process_content_or_copy_file_impl
from .lang_merge_content_patchers import patch_localized_content_json_impl
from .lang_merge_pending import export_filtered_pending_impl, remove_empty_dirs_impl
from .lang_merge_zip_io import (
    _read_text_from_zip,
    _write_bytes_atomic,
    _write_text_atomic,
    quarantine_copy_from_zip,
)

logger = logging.getLogger(__name__)

def _patch_localized_content_json(
    zf,
    cn_path: str,
    tw_output_path: str,
    rules: list,
    log_prefix: str,
    output_dir: str,
):
    return patch_localized_content_json_impl(
        zf,
        cn_path,
        tw_output_path,
        rules,
        log_prefix,
        output_dir,
        recursive_translate_dict_fn=recursive_translate_dict,
        quarantine_copy_from_zip_fn=quarantine_copy_from_zip,
        json_module=json,
        logger_override=logger,
    )

def _process_content_or_copy_file(
    zf,
    input_path: str,
    rules: list,
    output_dir: str,
    only_process_lang: bool = False,
    all_files_cache=None,
):
    return process_content_or_copy_file_impl(
        zf,
        input_path,
        rules,
        output_dir,
        only_process_lang=only_process_lang,
        all_files_cache=all_files_cache,
        load_config_fn=load_config,
        recursive_translate_dict_fn=recursive_translate_dict,
        get_text_processor_fn=get_text_processor,
        read_text_from_zip_fn=_read_text_from_zip,
        write_bytes_atomic_fn=_write_bytes_atomic,
        write_text_atomic_fn=_write_text_atomic,
        quarantine_copy_from_zip_fn=quarantine_copy_from_zip,
        normalize_patchouli_book_root_fn=normalize_patchouli_book_root,
        patch_localized_content_json_fn=_patch_localized_content_json,
        json_module=json,
        logger_override=logger,
    )

def remove_empty_dirs(root_dir: str):
    return remove_empty_dirs_impl(root_dir, logger_override=logger)

def export_filtered_pending(pending_root: str, output_root: str, min_count: int):
    return export_filtered_pending_impl(
        pending_root,
        output_root,
        min_count,
        json_module=json,
    )

__all__ = [
    "_patch_localized_content_json",
    "_process_content_or_copy_file",
    "remove_empty_dirs",
    "export_filtered_pending",
    "load_config",
    "apply_replace_rules",
    "recursive_translate_dict",
]
