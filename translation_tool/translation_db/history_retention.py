"""提供每個條目的翻譯歷史保留政策輔助函式。

條目編輯器只需要顯示精簡且可操作的稽核軌跡。把上限集中在這裡，
可讓 schema migration 與一般寫入交易共用相同政策，呼叫端不必知道
SQL 排名的細節。
"""

from __future__ import annotations

import sqlite3

HISTORY_RETENTION_LIMIT = 5


def prune_history_for_entries(conn: sqlite3.Connection, history_id_floor: int) -> int:
    """只重新整理本次交易有變更條目的最新五筆歷史。

    ``history_id_floor`` 是交易開始後立即取得的最大 history id。一般歷史
    寫入使用 SQLite 遞增的整數主鍵，因此大於此界線的記錄能精確找出
    需要重新檢查保留範圍的條目；未被本次交易修改的條目不會被掃描或重寫。
    """
    return _prune(
        conn,
        "WHERE h.entry_id IN (SELECT DISTINCT entry_id FROM history WHERE id > ?)",
        (history_id_floor,),
    )


def prune_all_history(conn: sqlite3.Connection) -> int:
    """在 migration 期間保留所有條目最新的五筆歷史。"""
    return _prune(conn, "", ())


def _prune(
    conn: sqlite3.Connection, scope_sql: str, scope_params: tuple[object, ...]
) -> int:
    """刪除保留範圍以外的記錄，並回傳刪除數量。"""
    before = conn.total_changes
    conn.execute(
        "DELETE FROM history WHERE id IN ("
        "SELECT id FROM ("
        "SELECT h.id, ROW_NUMBER() OVER ("
        "PARTITION BY h.entry_id ORDER BY h.id DESC"
        ") AS row_number FROM history AS h "
        f"{scope_sql}"
        ") ranked WHERE row_number > ?"
        ")",
        (*scope_params, HISTORY_RETENTION_LIMIT),
    )
    return conn.total_changes - before
