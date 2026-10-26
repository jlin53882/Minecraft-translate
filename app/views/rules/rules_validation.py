"""替換規則的逐條驗證與非阻擋提示（純邏輯，不碰 UI）。

``validate_rule`` 的結果是「錯誤」，會擋下儲存；``rule_hint`` 只是提醒，不擋儲存。
"""

from __future__ import annotations

import re

# 載入器（translation_tool/utils/text_processor.py）用這組字元判斷 from 是不是正則
_REGEX_CHARS = ".?*[]()\\"


def translate_regex_error(err: re.error) -> str:
    """將 Python 正則表達式錯誤轉換為中文用戶提示。"""
    msg = str(err)
    if "missing )" in msg or "unterminated subpattern" in msg:
        return "正則表達式缺少結尾括號「)」。"
    if "bad escape" in msg:
        return "無效的跳脫字元。"
    if "multiple repeat" in msg:
        return "不合法的重複符號。"
    if "unterminated character set" in msg:
        return "字元集合（[ ]）未正確結束。"
    if "unknown extension" in msg:
        return "無效的正則語法。"
    return "正則語法錯誤：" + msg


def build_from_index(all_rules) -> dict:
    """建立 {from: [索引...]}，供批次驗證時 O(1) 查重複。"""
    index: dict = {}
    for idx, rule in enumerate(all_rules):
        index.setdefault(rule.get("from"), []).append(idx)
    return index


def check_rule(src: str, dst: str, all_rules, current_index, from_index=None):
    """驗證規則格式；通過回傳 None，否則回傳 (出錯的欄位 "from"/"to", 訊息)。

    ``from_index`` 為 ``build_from_index`` 的結果；批次驗證時傳入，重複檢查就不必
    每條都掃過整個清單（O(n²) → O(n)）。
    """
    if not src.strip():
        return "from", "from 欄位不可為空"

    try:
        compiled = re.compile(src)
    except re.error as err:
        return "from", translate_regex_error(err)

    if from_index is not None:
        candidates = from_index.get(src, ())
    else:
        candidates = (
            idx for idx, rule in enumerate(all_rules) if rule.get("from") == src
        )
    for idx in candidates:
        if idx != current_index:
            return "from", f"⚠ 與第 {idx + 1} 條規則重複"

    group_refs = re.findall(r"(?:\\+(\d+)|\$(\d+))", dst)
    if group_refs:
        refs = [int(a or b) for a, b in group_refs]
        max_group = compiled.groups
        for ref in refs:
            if ref > max_group:
                return "to", f"引用群組 \\{ref} 超出群組數 {max_group}"

    if re.search(r"\\\\(?!\d)", dst):
        return "to", "可能存在無效跳脫（\\\\）"

    return None


def validate_rule(src: str, dst: str, all_rules, current_index, from_index=None):
    """驗證規則格式，回傳 (is_valid, 錯誤訊息)。"""
    error = check_rule(src, dst, all_rules, current_index, from_index)
    return (True, "") if error is None else (False, error[1])


def rule_hint(src: str, dst: str) -> str:
    """通過驗證的規則若會被當成正則處理，回傳一行提醒；否則回傳空字串（不阻擋儲存）。"""
    if src.strip() and any(ch in src for ch in _REGEX_CHARS):
        return "將視為正規表達式"
    return ""
