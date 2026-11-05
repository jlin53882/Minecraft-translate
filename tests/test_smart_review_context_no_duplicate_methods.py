"""``smart_review_context`` 的類別不得重複定義同名方法。

Python 會讓後面的定義覆蓋前面的；被覆蓋的版本不會執行，卻會讓維護者改錯地方
（曾經有兩個簽章不同的 ``_target_rows_from_bulk``）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import translation_tool.translation_db.smart_review_context as module


def test_no_class_defines_the_same_method_twice():
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    duplicates: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        seen: set[str] = set()
        for child in node.body:
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if child.name in seen:
                    duplicates.append(f"{node.name}.{child.name}")
                seen.add(child.name)
    assert duplicates == []
