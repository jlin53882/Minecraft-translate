"""SQLite 查詢共用的小型輔助函數。"""

from __future__ import annotations

from translation_tool.translation_db.schema import SRC_MANUAL


def like_pattern(text: str) -> str:
    """把使用者輸入轉成含 ESCAPE 反斜線的 LIKE 字面比對模式。"""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def effective_review_sql(
    alias: str, has_review_state: bool, *, else_unreviewed: bool = False
) -> str:
    """生效譯文審核狀態的 SQL 運算式（全庫共用的單一定義）。

    審核狀態與生效來源各自獨立：``review_status`` 是合法值就直接採用；沒有狀態（或舊資料的
    未知值）時，人工來源是「舊資料未知」（``legacy_unknown``），其他來源是「未審核」。
    沒有生效列（``alias.source IS NULL``）時結果為 ``NULL``。

    - ``has_review_state=False``：歷史 schema 沒有 ``review_status`` 欄，只有人工來源有狀態。
    - ``else_unreviewed=True``：只用在確定有生效列的統計觸發器，省略 ``source IS NOT NULL`` 判斷
      （觸發器 SQL 內容不變，已安裝的資料庫不會因此重裝）。
    """
    if not has_review_state:
        return f"CASE WHEN {alias}.source={SRC_MANUAL} THEN 'legacy_unknown' END"
    tail = (
        "ELSE 'unreviewed' END"
        if else_unreviewed
        else f"WHEN {alias}.source IS NOT NULL THEN 'unreviewed' END"
    )
    return (
        f"CASE WHEN {alias}.review_status IN "
        "('unreviewed','reviewed','legacy_unknown') "
        f"THEN {alias}.review_status WHEN {alias}.source={SRC_MANUAL} "
        f"THEN 'legacy_unknown' {tail}"
    )


def translation_review_sql(alias: str, has_review_state: bool) -> str:
    """單一來源譯文列（``translation`` 表）的審核狀態：欄位原值；歷史 schema 只有人工來源有值。"""
    if has_review_state:
        return f"{alias}.review_status"
    return f"CASE WHEN {alias}.source={SRC_MANUAL} THEN 'legacy_unknown' END"


__all__ = ["effective_review_sql", "like_pattern", "translation_review_sql"]
