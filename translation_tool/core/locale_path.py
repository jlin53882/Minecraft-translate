"""路徑中的語系記號：找出位置、換成目標語系。

Minecraft 模組放語系的方式不只一種，掃描、語系整理、翻譯輸出與打包都要用同一套規則判斷，
否則某一階段認得、另一階段認不得，就會把檔案放到遊戲讀不到的位置：

- 語系資料夾：``zh_tw/``（Patchouli）、``_zh_tw/``（GuideME）、帶序號的 ``zh_tw_0/``
  （Ice and Fire 圖鑑 ``lang/bestiary/zh_tw_0``）
- 檔名尾端：``<名稱>_zh_tw.txt``（Tombstone ``text/coral_chant_1_zh_tw.txt``）
- 檔名本身：``lang/zh_tw.json``

替換語系時保留原本的寫法（前導 ``_``、序號、副檔名），只換語系碼。
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

# 常見的單獨語言碼資料夾（``de``、``ru``）；兩字母的一般資料夾名如 ``ui`` 不算
BARE_LOCALES = "de|fr|es|it|pt|ru|ja|ko|pl|nl|tr|cs|sv|uk"
LOCALE_CODE = rf"[a-z]{{2}}[_-][a-z]{{2}}|{BARE_LOCALES}"

# 整段就是語系：``zh_tw``、``_zh_tw``、``zh_tw_0``（群組：前導底線、語系碼、序號）
_WHOLE = re.compile(rf"^(_?)({LOCALE_CODE})((?:_\d+)?)$", re.IGNORECASE)
# 檔名尾端帶語系：``<名稱>_zh_tw``、``<名稱>_zh_tw_0``（群組：名稱與底線、語系碼、序號）
_SUFFIX = re.compile(rf"^(.*?_)({LOCALE_CODE})((?:_\d+)?)$", re.IGNORECASE)

KIND_DIR = "dir"
KIND_NAME = "name"  # 檔名本身就是語系（``lang/zh_tw.json``）
KIND_SUFFIX = "suffix"  # 檔名尾端帶語系（``<名稱>_zh_tw.txt``）


@dataclass(frozen=True)
class LocaleMarker:
    """路徑片段中的一個語系記號。``code`` 已轉小寫並把 ``-`` 換成 ``_``。"""

    index: int
    code: str
    kind: str


def _normalize(code: str) -> str:
    return code.lower().replace("-", "_")


def _split_ext(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem, dot + ext) if dot and stem else (name, "")


def iter_locale_markers(parts: Sequence[str], start: int = 0) -> Iterator[LocaleMarker]:
    """依序列出 ``parts[start:]`` 中所有語系記號（資料夾在前，最後才是檔名）。"""

    last = len(parts) - 1
    for index in range(start, len(parts)):
        part = parts[index]
        if index < last:
            match = _WHOLE.match(part)
            if match:
                yield LocaleMarker(index, _normalize(match.group(2)), KIND_DIR)
            continue
        stem, _ext = _split_ext(part)
        match = _WHOLE.match(stem)
        if match:
            yield LocaleMarker(index, _normalize(match.group(2)), KIND_NAME)
            continue
        match = _SUFFIX.match(stem)
        if match:
            yield LocaleMarker(index, _normalize(match.group(2)), KIND_SUFFIX)


def find_locale_marker(
    parts: Sequence[str], code: str, start: int = 0
) -> LocaleMarker | None:
    """第一個語系碼為 ``code`` 的記號；沒有回傳 None。"""

    wanted = _normalize(code)
    return next(
        (m for m in iter_locale_markers(parts, start) if m.code == wanted), None
    )


def replace_locale(
    parts: Sequence[str],
    marker: LocaleMarker,
    code: str,
    *,
    underscore: bool | None = None,
) -> list[str]:
    """把 ``marker`` 所在片段的語系碼換成 ``code``，保留序號與副檔名。

    ``underscore`` 為 None 時保留原本有無前導 ``_``；只對資料夾與檔名本身有意義。
    """

    result = list(parts)
    part = result[marker.index]
    if marker.kind == KIND_DIR:
        stem, ext = part, ""
    else:
        stem, ext = _split_ext(part)
    pattern = _SUFFIX if marker.kind == KIND_SUFFIX else _WHOLE
    match = pattern.match(stem)
    if match is None:  # 呼叫端傳入的 marker 不屬於這組 parts
        raise ValueError(f"{part!r} 不含語系記號")
    head = match.group(1)
    if marker.kind != KIND_SUFFIX and underscore is not None:
        head = "_" if underscore else ""
    result[marker.index] = f"{head}{_normalize(code)}{match.group(3)}{ext}"
    return result


def has_locale_dir(parts: Sequence[str], code: str, start: int = 0) -> bool:
    """``parts[start:]`` 的資料夾中是否有語系碼為 ``code`` 的語系資料夾。"""

    wanted = _normalize(code)
    return any(
        m.kind == KIND_DIR and m.code == wanted
        for m in iter_locale_markers(parts, start)
    )


__all__ = [
    "BARE_LOCALES",
    "KIND_DIR",
    "KIND_NAME",
    "KIND_SUFFIX",
    "LOCALE_CODE",
    "LocaleMarker",
    "find_locale_marker",
    "has_locale_dir",
    "iter_locale_markers",
    "replace_locale",
]
