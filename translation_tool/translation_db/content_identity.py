"""跨版本「內容相同」的單一定義。

內容相同＝類型、模組、鍵值、原文都相同。模組識別一般就是 ``entry.mod_id``；KubeJS／FTB
的 lang 內容例外：識別是「命名空間@整合包資料夾名稱@路徑雜湊」，同一個整合包在不同版本的
資料夾名稱或路徑不同，識別就不同，所以這兩種內容比對時只看 ``@`` 前面的命名空間。

所有跨版本比對（條目清單的「版本不同」與其觸發器維護的 ``effective.diff``、條目詳情的
「同鍵值・其他版本」、儲存與審核的同步範圍、批量審核同步、跨版本沿用與版本差異審核）
都用這裡的表達式，避免各處對「同一筆內容」的看法不一致。

``idx_entry_content_ns`` 是 ``(kind, 內容模組識別, key, en_us)`` 的表達式索引，讓這個比對
和原本的 ``idx_entry_content`` 一樣可以逐筆精確查找；查詢必須使用與索引相同的表達式。
同一版本內的唯一性與匯入的既有條目查找仍然使用完整的 ``mod_id``。
"""

from __future__ import annotations

from .schema import KIND_FTB_QUEST_LANG, KIND_KUBEJS_LANG

PACK_KINDS = (KIND_KUBEJS_LANG, KIND_FTB_QUEST_LANG)
INDEX_NAME = "idx_entry_content_ns"


def content_mod_expr(kind: str, mod_id: str) -> str:
    """內容模組識別的 SQL 運算式；``kind``、``mod_id`` 是欄位或值的 SQL 片段。"""
    kinds = ",".join(f"'{k}'" for k in PACK_KINDS)
    return (
        f"(CASE WHEN {kind} IN ({kinds}) AND instr({mod_id}, '@') > 0 "
        f"THEN substr({mod_id}, 1, instr({mod_id}, '@') - 1) ELSE {mod_id} END)"
    )


def content_mod_sql(alias: str) -> str:
    """資料表別名 ``alias`` 的內容模組識別。"""
    return content_mod_expr(f"{alias}.kind", f"{alias}.mod_id")


def same_content_sql(left: str, right: str) -> str:
    """兩個別名的條目是同一筆內容（類型、內容模組識別、鍵值、原文相同）的條件。"""
    return (
        f"{right}.kind = {left}.kind "
        f"AND {content_mod_sql(right)} = {content_mod_sql(left)} "
        f"AND {right}.key = {left}.key AND {right}.en_us = {left}.en_us"
    )


def content_mod_value(kind: str, mod_id: str) -> str:
    """Python 端的同一個定義（與 :func:`content_mod_expr` 的結果一致）。"""
    if kind in PACK_KINDS and "@" in mod_id:
        return mod_id.split("@", 1)[0]
    return mod_id


def create_index_sql() -> str:
    return (
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} ON entry "
        f"(kind, {content_mod_expr('kind', 'mod_id')}, key, en_us)"
    )
