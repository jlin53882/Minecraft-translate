"""用 jar 的英文原文判斷 KubeJS 語系條目在不同整合包版本是否仍是同一句。

整合包常在 KubeJS 只放非英文的語系檔（例如只有 ``pt_br.json``），匯入時以該語系當原文；
整合包作者在版本之間修改這份翻譯的用字時，兩個版本的原文就不同，但遊戲內容沒變。
同一個命名空間、同一個鍵值在模組 jar 裡的英文原文（``kind='lang'``）才代表內容本身：
兩邊所在遊戲版本的 jar 英文相同，就視為同一句。

jar 原文在匯入時發生變動，資料庫不會改寫既有原文，而是記錄在 ``src_change``；
所以有待處理的原文變動紀錄時，條目上的英文不一定是目前的內容，這種情況一律不採用。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .content_identity import loose_text
from .schema import KIND_LANG

if TYPE_CHECKING:
    import sqlite3

JarEnglish = dict[str, str]  # 遊戲版本 → jar 英文原文（只含沒有待處理原文變動的條目）


def jar_english(
    conn: sqlite3.Connection, keys: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], JarEnglish]:
    """``(命名空間, 鍵值)`` → 各遊戲版本可信的 jar 英文原文。

    每組鍵值一次查詢，走 ``idx_entry_kind_mod_key``；原文變動紀錄以 ``src_change`` 的唯一索引
    （``kind, mc_version, mod_id, key``）判斷。原文空白或有待處理變動的版本不列入。
    """
    out: dict[tuple[str, str], JarEnglish] = {}
    for namespace, key in sorted(set(keys)):
        found: JarEnglish = {}
        for version, en_us, pending in conn.execute(
            "SELECT e.mc_version, e.en_us, EXISTS (SELECT 1 FROM src_change s "
            "WHERE s.kind = e.kind AND s.mc_version = e.mc_version "
            "AND s.mod_id = e.mod_id AND s.key = e.key) "
            "FROM entry e WHERE e.kind = ? AND e.mod_id = ? AND e.key = ?",
            (KIND_LANG, namespace, key),
        ):
            if en_us and en_us.strip() and not pending:
                found[version] = en_us
        out[(namespace, key)] = found
    return out


def same_jar_english(english: JarEnglish, version: str, other_version: str) -> bool:
    """兩個遊戲版本都有可信的 jar 英文，而且相同（只差大小寫或空白也算相同）。"""
    left, right = english.get(version), english.get(other_version)
    return (
        left is not None
        and right is not None
        and (left == right or loose_text(left) == loose_text(right))
    )
