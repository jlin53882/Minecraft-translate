"""translation_tool/core/translation_path_writer.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from pathlib import Path

def map_lang_output_path(src: Path) -> Path:
    """

    """
    if src.name.lower() == "en_us.json" and "lang" in src.parts:
        return src.with_name("zh_tw.json")
    return src

def set_by_path(root: dict, path: str, value):
    """依路徑設定值。"""
    current = root
    normalized_path = path.replace("][", "].[")
    parts = normalized_path.split(".")
    i = 0

    while i < len(parts):
        part = parts[i]

        if "[" in part and part.endswith("]"):
            open_bracket_idx = part.find("[")
            key = part[:open_bracket_idx]
            index_str = part[open_bracket_idx + 1 : -1]

            try:
                index = int(index_str)
            except ValueError:
                raise ValueError(f"路徑索引解析錯誤: '{index_str}' 來自於 '{part}'")

            if not key:
                if not isinstance(current, list):
                    raise TypeError(
                        f"預期是 list 但得到 {type(current)}，於路徑片段 '{part}'"
                    )
                if i == len(parts) - 1:
                    current[index] = value
                    return
                current = current[index]
            else:
                if not isinstance(current, dict) or key not in current:
                    raise KeyError(f"找不到列表 key: '{key}' 於路徑 '{part}'")
                target_list = current[key]
                if not isinstance(target_list, list):
                    raise TypeError(
                        f"Key '{key}' 的值不是 list，而是 {type(target_list)}"
                    )
                if i == len(parts) - 1:
                    target_list[index] = value
                    return
                current = target_list[index]

            i += 1
            continue

        if isinstance(current, dict):
            found_flat = False

            for j in range(len(parts), i, -1):
                candidate = ".".join(parts[i:j])

                if candidate in current:
                    if j == len(parts):
                        current[candidate] = value
                        return
                    current = current[candidate]
                    i = j
                    found_flat = True
                    break

                if candidate.endswith("]") and "[" in candidate:
                    lb = candidate.rfind("[")
                    key2 = candidate[:lb]
                    idx_str = candidate[lb + 1 : -1]
                    if idx_str.isdigit() and key2 in current:
                        target = current[key2]
                        idx = int(idx_str)
                        if isinstance(target, list) and 0 <= idx < len(target):
                            if j == len(parts):
                                target[idx] = value
                                return
                            current = target[idx]
                            i = j
                            found_flat = True
                            break

            if found_flat:
                continue

        if isinstance(current, dict):
            if part in current:
                if i == len(parts) - 1:
                    current[part] = value
                    return
                current = current[part]
                i += 1
                continue
            else:
                if i == len(parts) - 1:
                    current[part] = value
                    return
                raise KeyError(f"無法解析路徑片段: '{part}' (在路徑 {path} 中)")

        raise TypeError(f"路徑解析中斷：'{part}' 無法在非 dict/list 對象上繼續導航")

    return root
