"""translation_tool/core/translatable_extractor.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from pathlib import Path

from translation_tool.core.lm_config_rules import is_translatable_field, is_value_translatable
from translation_tool.utils.config_manager import load_config


def find_patchouli_json(root: Path, dir_names=None):
    """find_patchouli_json 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    patchouli_dir_names = load_config().get("lm_translator", {}).get("patchouli", {}).get("dir_names", [])
    if dir_names is None:
        dir_names = patchouli_dir_names

    files = []
    for dir_name in dir_names:
        pattern = f"assets/*/{dir_name}/**/*.json"
        files.extend(root.rglob(pattern))

    return files


def find_lang_json(root: Path):
    """find_lang_json 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    return list(root.rglob("assets/*/lang/*.json"))


def is_lang_file(file_path: Path) -> bool:
    """is_lang_file 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    return "lang" in file_path.parts


def extract_translatables(json_data, file_path):
    """extract_translatables 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    items = []
    is_lang = is_lang_file(Path(file_path))

    def walk(obj, base_path=""):
        """walk 的用途說明。

        Args:
            參數請見函式簽名。
        Returns:
            回傳內容依實作而定；若無顯式回傳則為 None。
        Side Effects:
            可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
        """
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{base_path}.{k}" if base_path else k

                if is_lang and isinstance(v, str):
                    if base_path == "":
                        if is_value_translatable(v, is_lang=True):
                            items.append(
                                {
                                    "file": str(file_path),
                                    "path": new_path,
                                    "text": v,
                                    "source_text": v,
                                }
                            )
                        continue

                    if k == "text" and is_value_translatable(v, is_lang=True):
                        items.append(
                            {
                                "file": str(file_path),
                                "path": new_path,
                                "text": v,
                                "source_text": v,
                            }
                        )
                    continue

                elif (
                    not is_lang
                    and isinstance(k, str)
                    and isinstance(v, str)
                    and is_translatable_field(k)
                    and is_value_translatable(v, is_lang=False)
                ):
                    items.append(
                        {
                            "file": str(file_path),
                            "path": new_path,
                            "text": v,
                            "source_text": v,
                        }
                    )
                else:
                    walk(v, new_path)

        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                new_path = f"{base_path}[{i}]"
                if isinstance(v, str) and is_value_translatable(v, is_lang=is_lang):
                    items.append(
                        {
                            "file": str(file_path),
                            "path": new_path,
                            "text": v,
                            "source_text": v,
                        }
                    )
                else:
                    walk(v, new_path)

    walk(json_data)
    return items
