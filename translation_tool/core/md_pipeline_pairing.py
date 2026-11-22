"""Markdown 管線：en_us／zh_tw 配對與「只翻未譯區塊」的分類。

用途：
  - 找出會寫到同一個輸出（完成/…/zh_tw/…）的來源檔，決定誰是寫入基底
  - 有對應的 zh_tw 時以 zh_tw 為底稿，用「與英文原文完全相同」判斷哪些區塊還沒翻

維護注意：不做任何位置或順序對齊（舊版 zip() 的做法）。zh_tw 區塊是否未譯，只看
它的 content_hash 是否出現在對應英文檔的區塊集合裡；無法確定的一律保留不送翻。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from translation_tool.plugins.md.md_extract_qa import (
    contains_cjk,
    detect_lang_segment,
    map_rel_lang_path,
)

_PAIRED_LANGS = ("en_us", "zh_tw")


@dataclass
class SourcePlan:
    """各來源檔在 Step 1 的處理方式。"""

    # zh_tw 檔的相對路徑 -> 與它配對的 en_us 檔相對路徑
    pair_en: dict[str, str] = field(default_factory=dict)
    # 不產生 pending 的檔案：相對路徑 -> 原因（"paired_en"／"output_conflict"）
    skip: dict[str, str] = field(default_factory=dict)
    # 會互相覆蓋同一個輸出的檔案群組（每組是一串相對路徑）
    conflicts: list[list[str]] = field(default_factory=list)


def lang_output_key(rel: str) -> str | None:
    """來源檔對應到的輸出身分；不是 en_us／zh_tw 來源（例如 zh_cn、無語言資料夾）回傳 None。

    en_us 與 zh_tw 會映射到同一個鍵（Step 3 都寫到 zh_tw 路徑），並以 casefold 比較，
    所以只差大小寫的兩個路徑視為同一個輸出。`en_us` 與 `_zh_tw` 這種底線不一致的組合
    本來就會寫到不同路徑，鍵也不同，不會被配對。
    """
    parts = rel.replace("\\", "/").split("/")
    if detect_lang_segment(parts) not in _PAIRED_LANGS:
        return None
    return map_rel_lang_path(rel, src_lang="en_us", dst_lang="zh_tw").casefold()


def plan_sources(rels: list[str]) -> SourcePlan:
    """依輸出身分分組：成對的以 zh_tw 為底稿、同語系重複的視為衝突。結果與列舉順序無關。"""
    groups: dict[str, dict[str, list[str]]] = {}
    for rel in sorted(rels):
        key = lang_output_key(rel)
        if key is None:
            continue
        lang = detect_lang_segment(rel.replace("\\", "/").split("/"))
        groups.setdefault(key, {"en_us": [], "zh_tw": []})[lang].append(rel)

    plan = SourcePlan()
    for members in groups.values():
        en_files, zh_files = members["en_us"], members["zh_tw"]
        if len(en_files) > 1 or len(zh_files) > 1:
            everyone = en_files + zh_files
            plan.conflicts.append(everyone)
            plan.skip.update(dict.fromkeys(everyone, "output_conflict"))
        elif en_files and zh_files:
            plan.pair_en[zh_files[0]] = en_files[0]
            plan.skip[en_files[0]] = "paired_en"
    return plan


def select_untranslated_blocks(
    en_items: list[Any], zh_items: list[Any]
) -> tuple[list[Any], int]:
    """從 zh_tw 檔的區塊中挑出「仍是英文原文」的區塊。

    判斷只看 content_hash 是否出現在英文檔區塊的集合：和英文完全相同才算未譯。
    其他區塊（已譯、人工改過、混合中英文、品牌名）一律保留，不靠 CJK 字元決定。
    回傳 (要送翻的區塊, 保留下來但沒有 CJK 的區塊數)：後者是舊版英文、符號等無法確定
    的內容，只供回報，不影響寫入。
    """
    en_hashes = {it.content_hash for it in en_items}
    pending: list[Any] = []
    ambiguous = 0
    for it in zh_items:
        if it.content_hash in en_hashes:
            pending.append(it)
        elif not contains_cjk(it.text) and any(
            ch.isascii() and ch.isalpha() for ch in it.text
        ):
            ambiguous += 1
    return pending, ambiguous
