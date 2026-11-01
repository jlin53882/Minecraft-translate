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


def source_locale_sql(entry_alias: str) -> str:
    """回傳持久化來源語系的 SQL 摘要。

    ``unknown:<entry_id>`` 是查詢內部的唯一哨兵值：來源不存在、包含無法
    還原的來源，或同一條目混有多個語系時，都不能只以空字串與其他 unknown
    條目合併。已知的單一來源語系則直接回傳正規化 token。新掃描明確保存的
    ``zh_tw``／``zh_cn`` 可能就是實際選定的 fallback 原文，不能因為語系名稱
    常被用作譯文而一律排除；舊資料的遷移會把無法確認角色的來源保留為 NULL，
    讓這裡保守回傳 unknown。
    """
    entry_id = f"CAST({entry_alias}.id AS TEXT)"
    unknown = f"'unknown:' || {entry_id}"
    return (
        "COALESCE((SELECT CASE "
        "WHEN EXISTS (SELECT 1 FROM entry_origin eo0 "
        f"WHERE eo0.entry_id={entry_alias}.id AND eo0.source_locale IS NULL) "
        f"THEN {unknown} "
        "WHEN COUNT(DISTINCT eo.source_locale)=1 THEN MAX(eo.source_locale) "
        f"ELSE {unknown} END FROM entry_origin eo "
        f"WHERE eo.entry_id={entry_alias}.id), {unknown})"
    )


def source_locale_match_sql(left_alias: str, right_alias: str) -> str:
    """建立可重用的來源語系相容條件。

    兩個沒有 ``entry_origin`` 的舊條目維持升級前的重用行為；只要任一條目
    有來源紀錄但語系無法確認，就不把 ``unknown:<id>`` 當成可匹配的語系。
    """
    left_locale = source_locale_sql(left_alias)
    right_locale = source_locale_sql(right_alias)
    return (
        "("
        f"(NOT EXISTS (SELECT 1 FROM entry_origin eo_{left_alias} "
        f"WHERE eo_{left_alias}.entry_id={left_alias}.id) "
        f"AND NOT EXISTS (SELECT 1 FROM entry_origin eo_{right_alias} "
        f"WHERE eo_{right_alias}.entry_id={right_alias}.id)) "
        f"OR ({left_locale} NOT LIKE 'unknown:%' "
        f"AND {right_locale}={left_locale})"
        ")"
    )


__all__ = [
    "effective_review_sql",
    "like_pattern",
    "source_locale_match_sql",
    "source_locale_sql",
    "translation_review_sql",
]
