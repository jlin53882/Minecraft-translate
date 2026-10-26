"""translation_tool/core/translation_path_writer.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from pathlib import Path

from translation_tool.core.translatable_extractor import is_advancement_file


def map_lang_output_path(src: Path) -> Path:
    """根據來源路徑自動判定翻譯輸出的目標路徑。

    - ``…/lang/en_us.json`` → ``…/lang/zh_tw.json``
    - ``data/<模組>/lang/*.json`` → ``assets/<模組>/lang/*.json``
    - ``…/patchouli_books/<書>/en_us/…`` → ``…/patchouli_books/<書>/zh_tw/…``
    - 其他手冊資料夾 ``assets/<模組>/<資料夾>/…/en_us/…`` → 同路徑換成 ``zh_tw``
    - Advancement 即使子路徑包含 ``en_us`` 或 ``lang``，仍保留完整來源路徑
    """
    if is_advancement_file(src):
        return src
    if src.name.lower() == "en_us.json" and "lang" in src.parts:
        parts = list(src.parts)
        # data/<namespace>/lang is a valid scan source in mod JARs, but it is
        # not the final Resource Pack language location. Advancement files
        # returned above before this branch and therefore keep their data/
        # path and translate keys intact.
        if parts and parts[0].lower() in {"data", "resources"}:
            parts[0] = "assets"
            return Path(*parts[:-1], "zh_tw.json")
        return src.with_name("zh_tw.json")
    if src.name.lower() == "zh_tw.json" and "lang" in src.parts:
        parts = list(src.parts)
        if parts and parts[0].lower() in {"data", "resources"}:
            parts[0] = "assets"
            return Path(*parts)
    parts = list(src.parts)
    if "patchouli_books" in parts:
        locale_index = parts.index("patchouli_books") + 2  # 其後依序是書名、語系資料夾
        if locale_index < len(parts) - 1 and parts[locale_index].lower() == "en_us":
            parts[locale_index] = "zh_tw"
            return Path(*parts)
        return src
    # 其他手冊／指南資料夾（名稱不限）：模組資料夾之後，第一個 ``en_us``／``_en_us`` 資料夾
    # 換成 zh_tw，且它前面至少隔著一層資料夾（``assets/<模組>/<資料夾>/…/en_us/…``）
    for index in range(3, len(parts) - 1):
        segment = parts[index].lower()
        if segment in ("en_us", "_en_us"):
            parts[index] = "_zh_tw" if segment.startswith("_") else "zh_tw"
            return Path(*parts)
    return src


def set_by_path(root: dict, path: str, value):
    """根據點分隔與方括號混合的路徑（如 `a.b[0].c`），在多層巢狀 dict/list 結構中設定數值。"""
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
