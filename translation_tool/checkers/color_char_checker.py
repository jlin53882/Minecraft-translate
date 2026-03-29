"""translation_tool/checkers/color_char_checker.py 模組。

用途：檢查翻譯檔案中的非法顏色字元（& 後接非法的 Minecraft 顏色代碼字元）。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Generator

# 核心檢查：& 後只能接 Minecraft 合法的格式化代碼字元
# 合法範圍：0-9（數字）, a-f（顏色代碼）, k-o（格式代碼）, r（重置）
# 違法：& 後面接了合法範圍 0-9a-fk-or 以外的任何字元
COLOR_PATTERN = re.compile(r"&([^0-9a-fk-or])")


@dataclass
class ColorCharError:
    """單一顏色字元錯誤。"""

    file_path: str
    key: str
    value: str
    illegal_char: str
    position: int  # 在 value 中的位置
    message: str


def check_color_chars(value: str) -> list[ColorCharError] | None:
    """檢查單一字串中的非法顏色字元。

    Args:
        value: 要檢查的字串。

    Returns:
        錯誤列表（若無錯誤則回傳 None）。
    """
    errors: list[ColorCharError] = []
    for match in COLOR_PATTERN.finditer(value):
        illegal_char = match.group(1)
        pos = match.start()
        errors.append(
            ColorCharError(
                file_path="",
                key="",
                value=value,
                illegal_char=illegal_char,
                position=pos,
                message=f"在位置 {pos} 發現非法顏色字元 '&{illegal_char}'，"
                f"& 後只能接 0-9, a-f, k-o, r。",
            )
        )
    return errors if errors else None


def _check_value(
    file_path: str,
    key: str,
    value: Any,
    parent_path: str = "",
    *,
    include_current_key_in_parent: bool = False,
) -> Generator[ColorCharError, None, None]:
    """遞迴檢查單一值，若為字串則檢查顏色字元。

    Args:
        file_path: 檔案路徑。
        key: 目前檢查的 key。
        value: 目前檢查的值。
        parent_path: 父層的完整路徑（用於 nested dict key path 報告）。
    """
    # 建立完整的 nested dict key path（用於 nested dict 巢狀回報）
    if parent_path and include_current_key_in_parent:
        full_key = f"{parent_path}.{key}"
    elif parent_path:
        full_key = parent_path
    else:
        full_key = key

    if isinstance(value, str):
        errors = check_color_chars(value)
        if errors:
            for err in errors:
                # 補足 file_path 與 key（從上層傳入）
                yield ColorCharError(
                    file_path=err.file_path or file_path,
                    key=full_key,
                    value=err.value,
                    illegal_char=err.illegal_char,
                    position=err.position,
                    message=err.message,
                )
    elif isinstance(value, dict):
        for k, v in value.items():
            # 進入 nested dict：之後的子層 key 要接在目前完整路徑後面
            yield from _check_value(
                file_path,
                k,
                v,
                parent_path=full_key,
                include_current_key_in_parent=True,
            )
    elif isinstance(value, list):
        for i, item in enumerate(value):
            # list index 視為目前 key 的子路徑一部分
            yield from _check_value(
                file_path,
                f"{key}[{i}]",
                item,
                parent_path=parent_path,
                include_current_key_in_parent=True,
            )


def check_json_file(file_path: str) -> Generator[ColorCharError, None, None]:
    """讀取 JSON 檔並遞迴檢查所有 string value。

    Args:
        file_path: JSON 檔案路徑。

    Yields:
        找到的 ColorCharError。
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # 不阻断，继续检查其他文件
        return

    if isinstance(data, dict):
        for key, value in data.items():
            # 傳入空字串作為 initial key，避免頂層 key 被重複附加到 nested path
            yield from _check_value(file_path, key, value, parent_path="")


def check_directory(dir_path: str) -> Generator[ColorCharError, None, None]:
    """遞迴檢查目錄下所有 .json 檔。

    Args:
        dir_path: 目錄路徑。

    Yields:
        找到的 ColorCharError。
    """
    for root, _, files in os.walk(dir_path):
        for file in files:
            if file.endswith(".json"):
                yield from check_json_file(os.path.join(root, file))
