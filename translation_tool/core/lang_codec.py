"""translation_tool/core/lang_codec.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import logging
import re
from typing import Dict

logger = logging.getLogger(__name__)

JSON_LINE = re.compile(r'^\s*"(.+?)"\s*:\s*"(.+?)"\s*,?\s*$')
KEY_ZH = re.compile(r"^([a-zA-Z0-9_.-]+)([\u4e00-\u9fff].+)$")


def try_repair_lang_line(line: str):
    # JSON 風格
    """處理此函式的工作（細節以程式碼為準）。

    - 主要包裝：`match`

    回傳：依函式內 return path。
    """
    m = JSON_LINE.match(line)
    if m:
        return m.group(1), m.group(2)

    # key中文黏一起
    m = KEY_ZH.match(line)
    if m:
        return m.group(1), m.group(2)

    return None


def collapse_lang_lines(text: str):
    """
    Forge .lang: 行尾 \\ 表示續行
    將多行合併成實際的一行
    """
    lines = text.splitlines()
    out = []
    buf = ""

    for line in lines:
        if buf:
            buf += line.lstrip()
        else:
            buf = line

        if buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1]  # 移除 \ 繼續
            continue
        else:
            out.append(buf)
            buf = ""

    if buf:
        out.append(buf)

    return out


def parse_lang_text(text: str, *, on_error=None) -> Dict[str, str]:
    """
    優化後的 .lang 解析：處理 BOM、註解、以及無 '=' 的長文本續行。
    將 .lang 檔案的 key=value 內容解析為字典
    """
    # 1. 移除 UTF-8 BOM
    text = text.lstrip("\ufeff")

    data = {}
    lines = text.splitlines()  # 如果 collapse_lang_lines 效果不好，建議直接 split

    last_key = None  # 用於記錄上一個處理的 Key，處理續行問題

    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()

        # 跳過空行或註解
        if not line or line.startswith(("#", "//", "<")):
            continue

        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()

            if key:
                data[key] = val
                last_key = key
            elif on_error:
                on_error(idx, raw, "empty key")
        else:
            # 🔥 解決 [line 39] missing '=':
            # 如果這行沒有 '='，它極可能是上一行價值的延伸（續行）
            if last_key is not None:
                # 將這行內容合併到上一個 key 的 value 中
                data[last_key] += "\n" + line
                logger.debug(f"已自動修復續行 (line {idx}): 合併至 {last_key}")
            else:
                # 如果第一行就沒有 '=' 且不是註解，這才是真正的錯誤
                if on_error:
                    on_error(idx, raw, "missing '=' at beginning")
                continue

    return data


def dump_lang_text(data: Dict[str, str]) -> str:
    """將字典轉換回 .lang 的文字格式"""
    lines = []
    # 按照 key 排序以保持檔案整潔
    for key in sorted(data.keys()):
        lines.append(f"{key}={data[key]}")
    return "\n".join(lines)


def is_mc_standard_lang_path(path: str) -> bool:
    """
    判定該路徑是否為 Minecraft 標準的語言資料夾結構。
    例如: assets/mymod/lang/zh_cn.lang -> True
    例如: assets/mymod/patchouli_books/item.lang -> False
    """
    p = path.replace("\\", "/").lower()
    # 必須在 /lang/ 資料夾內且為 .lang 結尾
    return "/lang/" in p and p.endswith(".lang")


def pick_first_not_none(*vals):
    """處理此函式的工作（細節以程式碼為準）。

    回傳：依函式內 return path。
    """
    for v in vals:
        if v is not None:
            return v
    return ""


def normalize_patchouli_book_root(path: str) -> str:
    """
    將：
    mod_book/assets/modid/patchouli_books/book/
    → assets/modid/patchouli_books/book/
    """
    p = path.replace("\\", "/")
    idx = p.find("assets/")
    return p[idx:] if idx != -1 else p
