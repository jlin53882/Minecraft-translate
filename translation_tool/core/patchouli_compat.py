"""Patchouli 手冊：判斷既有譯文與 en_us 原文的格式是否一致。

mod 改版後，zh_cn／zh_tw 常常沒有跟著更新：頁數不同、欄位不同，或 ``item`` 還是
1.20.4 以前的 ``id{NBT}`` 寫法（1.20.5 起要寫成 ``id[component=…]``）。這種譯文
放進遊戲會讓整本書載入失敗，也無法和原文逐頁對應，所以必須視為「沒有譯文」，
改由 en_us 重新翻譯。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# ``namespace:id{…}``：舊版物品 NBT 寫法（字串開頭就是物品 id 緊接大括號）
OLD_NBT_ITEM_RE = re.compile(r"^#?[a-z0-9_.-]+:[a-z0-9_/.-]+\{")


def count_old_nbt_items(value: Any) -> int:
    """結構內有幾個字串是舊版物品 NBT 寫法。"""
    if isinstance(value, str):
        return 1 if OLD_NBT_ITEM_RE.match(value.strip()) else 0
    if isinstance(value, list):
        return sum(count_old_nbt_items(item) for item in value)
    if isinstance(value, dict):
        return sum(count_old_nbt_items(item) for item in value.values())
    return 0


def _kind(value: Any) -> str:
    if isinstance(value, dict):
        return "物件"
    if isinstance(value, list):
        return "陣列"
    if isinstance(value, str):
        return "文字"
    return "數值"


def _shape_difference(en: Any, zh: Any, path: str) -> str | None:
    """第一個結構差異的描述；沒有差異回傳 None。文字內容本身不比較。"""
    if _kind(en) != _kind(zh):
        return f"{path or '根'} 的型別不同（{_kind(en)} / {_kind(zh)}）"
    if isinstance(en, dict):
        missing = sorted(set(en) - set(zh))
        extra = sorted(set(zh) - set(en))
        if missing or extra:
            return f"{path or '根'} 的欄位不同（缺少 {missing}、多出 {extra}）"
        for key in en:
            found = _shape_difference(
                en[key], zh[key], f"{path}.{key}" if path else key
            )
            if found:
                return found
    elif isinstance(en, list):
        if len(en) != len(zh):
            return f"{path or '根'} 的項目數不同（{len(en)} / {len(zh)}）"
        for index, (en_item, zh_item) in enumerate(zip(en, zh, strict=True)):
            found = _shape_difference(en_item, zh_item, f"{path}[{index}]")
            if found:
                return found
    return None


def _marker_difference(en: Any, zh: Any, path: str) -> str | None:
    """第一個格式標記（``$(br)``、``§a``、``%s`` 等）數量不同的文字欄位；沒有回傳 None。

    只在 ``_shape_difference`` 確認兩邊結構相同後呼叫，所以同路徑的值型別一定相同。
    換行數量允許不同，其餘標記和 LM 修復輸出的檢查規則一致。
    """
    if isinstance(en, str):
        # 延遲匯入：translation_db 套件初始化時會回頭匯入本模組。
        from translation_tool.translation_db.quality import repair_output_issues

        issues = repair_output_issues(en, zh)
        return (
            f"{path or '根'} 的格式標記不同（{'、'.join(issues)}）" if issues else None
        )
    if isinstance(en, dict):
        children = ((f"{path}.{key}" if path else key, en[key], zh[key]) for key in en)
    elif isinstance(en, list):
        children = ((f"{path}[{i}]", a, b) for i, (a, b) in enumerate(zip(en, zh)))
    else:
        return None
    for child_path, en_child, zh_child in children:
        found = _marker_difference(en_child, zh_child, child_path)
        if found:
            return found
    return None


def find_patchouli_mismatch(en_data: Any, zh_data: Any) -> str | None:
    """比對 en_us 與 zh_cn／zh_tw 的同一個 Patchouli 檔案。

    格式不一致（舊版物品 NBT、頁數或欄位不同）時回傳原因，一致回傳 None。
    原文本身用舊 NBT 寫法時（舊版遊戲）不算差異。這些差異會讓書載入失敗或譯文錯位，
    所以呼叫端應該不採用該譯文。
    """
    if count_old_nbt_items(zh_data) > count_old_nbt_items(en_data):
        return "物品欄位仍是舊版 NBT 寫法"
    return _shape_difference(en_data, zh_data, "")


def find_patchouli_marker_issue(en_data: Any, zh_data: Any) -> str | None:
    """同一個文字欄位的格式標記數量和 en_us 不同時回傳原因，否則回傳 None。

    只影響排版（譯者常把 ``$(br2)`` 改成 ``$(br)``），不會讓書載入失敗，所以呼叫端
    只該提醒、不該丟棄譯文。結構不同時無從逐欄比對，回傳 None（由
    ``find_patchouli_mismatch`` 負責）。
    """
    if _shape_difference(en_data, zh_data, ""):
        return None
    return _marker_difference(en_data, zh_data, "")


def find_old_nbt_item_files(root: str) -> list[str]:
    """``root`` 底下 ``patchouli_books`` 內仍含舊版物品 NBT 寫法的 JSON 檔（相對路徑）。"""
    found: list[str] = []
    for folder, _dirs, files in os.walk(root):
        if "patchouli_books" not in Path(folder).parts:
            continue
        for name in files:
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if count_old_nbt_items(data):
                found.append(os.path.relpath(path, root).replace("\\", "/"))
    return sorted(found)
