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
    ShieldPiece,
    ShieldedText,
    shield_text,
    unshield_text,
    add_escape_quotes,
)

__all__ = [
    "collect_json_files",
    "read_json_dict",
    "write_json_dict",
    "compute_output_path",
    "is_lang_code_segment",
    "replace_lang_folder_with_zh_tw",
    "should_rename_to_zh_tw",
    "is_already_zh",
    "ShieldPiece",
    "ShieldedText",
    "shield_text",
    "unshield_text",
    "add_escape_quotes",
]
