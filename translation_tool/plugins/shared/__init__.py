"""Shared plugin helpers public API."""

from translation_tool.plugins.shared.json_io import (
    collect_json_files,
    read_json_dict,
    write_json_dict,
)
from translation_tool.plugins.shared.lang_path_rules import (
    compute_output_path,
    is_lang_code_segment,
    replace_lang_folder_with_zh_tw,
    should_rename_to_zh_tw,
)
from translation_tool.plugins.shared.lang_text_rules import (
    is_already_zh,
)
from translation_tool.plugins.shared.rich_text_shield import (
    ShieldedText,
    ShieldPiece,
    add_escape_quotes,
    shield_text,
    unshield_text,
)

__all__ = [
    "ShieldPiece",
    "ShieldedText",
    "add_escape_quotes",
    "collect_json_files",
    "compute_output_path",
    "is_already_zh",
    "is_lang_code_segment",
    "read_json_dict",
    "replace_lang_folder_with_zh_tw",
    "shield_text",
    "should_rename_to_zh_tw",
    "unshield_text",
    "write_json_dict",
]
