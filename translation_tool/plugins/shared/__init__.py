"""Shared helpers for translation_tool.plugins.* modules."""

from .json_io import read_json_dict, write_json_dict, collect_json_files
from .lang_path_rules import (
    should_rename_to_zh_tw,
    is_lang_code_segment,
    replace_lang_folder_with_zh_tw,
    compute_output_path,
)

__all__ = [
    "read_json_dict",
    "write_json_dict",
    "collect_json_files",
    "should_rename_to_zh_tw",
    "is_lang_code_segment",
    "replace_lang_folder_with_zh_tw",
    "compute_output_path",
]
