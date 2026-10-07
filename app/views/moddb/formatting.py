"""app/views/moddb/formatting.py：Mod 資料庫頁共用的顯示文字與色調（純函式，不依賴 Flet）。"""

from __future__ import annotations

import re
from collections import Counter

from app.services_impl.moddb_service import (
    SOURCE_NAMES,
    SRC_AI,
    SRC_JAR_CN,
    SRC_MANUAL,
)

STATE_LABELS = {
    "all": "全部",
    "none": "未翻譯",
    "diff": "版本不同",
    "changed": "原文已變動",
    "same": "翻譯與原文相同",
    "manual": "人工",
    "ok": "有譯文",
}
# 條目狀態 → 強調色組（design.tone 的名稱）
STATE_TONES = {
    "none": "neutral",
    "diff": "gold",
    "changed": "gold",
    "manual": "ench",
    "ok": "dia",
}
KIND_LABELS = {"lang": "語言檔", "patchouli": "Patchouli 手冊"}


def kind_label(kind: str) -> str:
    """條目類型的顯示名稱；日後資料庫新增的類型沒登錄名稱時直接顯示代碼。"""
    return KIND_LABELS.get(kind, kind)


def source_label(source: int | None) -> str:
    return SOURCE_NAMES.get(source, "—") if source is not None else "—"


def source_tone(source: int | None) -> str:
    """譯文來源的色調：人工 = 紫、簡中轉繁 = 金、AI = 中性、其餘 = 藍。"""
    if source == SRC_MANUAL:
        return "ench"
    if source == SRC_JAR_CN:
        return "gold"
    if source == SRC_AI or source is None:
        return "neutral"
    return "dia"


def shorten(text: str, limit: int = 60) -> str:
    text = visible_breaks(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_count(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def percent(part: int, total: int) -> int:
    return round(100 * part / total) if total else 0


def impact_text(version: str, new_text: str, impacts) -> str:
    """儲存前的影響說明；``impacts`` 為 ``Impact`` 清單（含自己）。"""
    others = [i for i in impacts if not i.is_self]
    if not others:
        return f"這個內容只出現在 {version}，儲存後不會影響其他版本。"
    differ = [i for i in others if i.old_zh_tw and i.old_zh_tw != new_text]
    names = "、".join(i.mc_version for i in others)
    text = f"儲存後，{len(others)} 個版本（{names}）的相同原文會一併改為「{shorten(new_text, 30)}」。"
    if differ:
        detail = "、".join(
            f"{i.mc_version}「{shorten(i.old_zh_tw, 16)}」" for i in differ
        )
        text += f" 其中 {len(differ)} 個目前的譯文不同：{detail}。"
    return text + " 原譯文保留在記錄中，可還原。"


# --- 特殊字元：換行、格式碼、佔位符 -------------------------------------------------
_TOKEN_RE = re.compile(
    r"§[0-9a-fk-orA-FK-OR]|%(?:\d+\$)?[sdfxXeEgGcb%]|\$\([^)]*\)|\{\d*\}|\\n|\n"
)
_TOKEN_NAMES = {"\n": "換行", "\\n": "字面 \\n", "$(t:…)": "提示文字 $(t:…)"}


def visible_breaks(text: str) -> str:
    """清單預覽用：真正的換行顯示成 ¶（折成一行），其餘空白折疊。"""
    flat = (text or "").replace("\r\n", "\n").replace("\n", " ¶ ")
    return " ".join(flat.split())


_VISIBLE_RE = re.compile(_TOKEN_RE.pattern + r"|^[ \t]+|[ \t]+$", re.MULTILINE)


def visible_segments(text: str) -> list[tuple[str, str]]:
    """把特殊字元攤開成 ``[(顯示文字, 種類)]``；種類為 text / token / newline / space。

    換行顯示成 ``↵`` 並真的換行；行首行尾的空白顯示成 ``·``；格式碼、佔位符標成 token。
    """
    text = (text or "").replace("\r\n", "\n")
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _VISIBLE_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], "text"))
        raw = m.group(0)
        if raw == "\n":
            out += [("↵", "newline"), ("\n", "text")]
        elif raw.strip(" \t") == "":
            out.append(("·" * len(raw.replace("\t", "    ")), "space"))
        else:
            out.append((raw, "token"))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], "text"))
    return out


# Patchouli 的提示文字 `$(t:提示文字)`：括號裡的文字是要翻譯的，只比對「有幾個提示標記」
_TOOLTIP_PREFIX = "$(t:"
_TOOLTIP_TOKEN = "$(t:…)"


# 掃描用：Patchouli 巨集只認開頭 `$(`，結尾用括號配對找（提示文字／網址裡可能有成對的括號）
_SCAN_RE = re.compile(
    r"§[0-9a-fk-orA-FK-OR]|%(?:\d+\$)?[sdfxXeEgGcb%]|\$\(|\{\d*\}|\\n|\n"
)


def _macro_end(text: str, start: int) -> int | None:
    """``$(`` 之後的結尾位置（含 ``)``）。依括號配對（巢狀的成對括號算在內）；
    括號沒有配對完（缺 ``)``）時退回第一個 ``)``；完全沒有 ``)`` 回傳 None。"""
    depth = 1
    for i in range(start, len(text)):
        char = text[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    first = text.find(")", start)
    return None if first < 0 else first + 1


def _iter_tokens(text: str):
    pos = 0
    while True:
        m = _SCAN_RE.search(text, pos)
        if m is None:
            return
        if m.group(0) == "$(":
            end = _macro_end(text, m.end())
            if end is None:  # 沒有結尾的 `$(` 不是標記
                pos = m.end()
                continue
            yield text[m.start() : end]
            pos = end
        else:
            yield m.group(0)
            pos = m.end()


def format_tokens(text: str) -> Counter[str]:
    """文字中的換行、`§` 格式碼、`%s` 類佔位符、Patchouli `$(…)`、`{0}` 的出現次數。

    ``$(t:提示文字)`` 的內容會被翻譯，所以統一記成 ``$(t:…)``；其餘 ``$(…)``
    （``$(item)``、``$(ttcolor)``、``$(l:連結)`` 等不能翻譯）仍須完全相同。
    巨集的結尾依括號配對，提示文字或網址裡成對的括號（例如 ``f(x)``）不會讓標記提早結束。
    """
    tokens: Counter[str] = Counter()
    for raw in _iter_tokens(text or ""):
        token = raw.replace("\r", "")
        if token.startswith(_TOOLTIP_PREFIX):
            token = _TOOLTIP_TOKEN
        tokens[token] += 1
    return tokens


def token_issues(source: str, translated: str) -> list[str]:
    """譯文與原文的特殊字元不一致時的提醒（缺少或多出）。"""
    want, got = format_tokens(source), format_tokens(translated)
    issues: list[str] = []
    for token in sorted(set(want) | set(got)):
        diff = got[token] - want[token]
        if diff:
            name = _TOKEN_NAMES.get(token, token)
            issues.append(f"{'多了' if diff > 0 else '少了'} {abs(diff)} 個「{name}」")
    return issues


def whitespace_note(text: str) -> str:
    """前後有空白或換行時的提醒（儲存時原樣保留，不會被修剪）。"""
    if not text or text == text.strip():
        return ""
    return "譯文前後有空白或換行，儲存時會原樣保留"
