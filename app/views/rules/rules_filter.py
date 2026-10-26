"""替換規則頁的搜尋比對（純邏輯，不碰 UI）。

查詢字串以 ``/…/`` 包起來視為正則；其餘為不分大小寫的子字串。比對欄位為
``from`` / ``to`` / ``comment`` / ``category``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SEARCH_FIELDS = ("from", "to", "comment", "category")


@dataclass(frozen=True)
class RuleQuery:
    """一次搜尋的條件；``pattern`` 只在正則模式且語法正確時才有值。"""

    keyword: str
    regex: bool = False
    pattern: re.Pattern | None = field(default=None, compare=False)

    @property
    def regex_invalid(self) -> bool:
        """正則模式但語法錯誤（此時比對退回一般字串）。"""
        return self.regex and self.pattern is None


def parse_query(text: str) -> RuleQuery | None:
    """把搜尋框文字轉成查詢；空白回傳 None（代表不過濾）。"""
    text = (text or "").strip()
    if not text:
        return None
    if len(text) > 2 and text.startswith("/") and text.endswith("/"):
        keyword = text[1:-1]
        try:
            pattern = re.compile(keyword, re.IGNORECASE)
        except re.error:
            pattern = None
        return RuleQuery(keyword, True, pattern)
    return RuleQuery(text)


def rule_matches(rule: dict, query: RuleQuery) -> bool:
    """規則的任一搜尋欄位符合查詢即為真。"""
    needle = query.keyword.lower()
    for name in SEARCH_FIELDS:
        value = rule.get(name) or ""
        if not value:
            continue
        if query.pattern is not None and query.pattern.search(value):
            return True
        if needle in value.lower():
            return True
    return False


def filter_rules(rules: list[dict], query: RuleQuery | None) -> list[dict] | None:
    """依查詢過濾（保留原物件與順序）；query 為 None 時回傳 None 代表未過濾。"""
    if query is None:
        return None
    return [rule for rule in rules if rule_matches(rule, query)]
