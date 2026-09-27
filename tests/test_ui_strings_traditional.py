"""UI 顯示字串必須是繁體中文（避免簡體字混入介面）。"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 常見「只會出現在簡體」的字；繁體介面不應出現
_SIMPLIFIED_ONLY = set("无请结关调筛选设执载误确储档页数简单项输类报态进删")


def _ui_string_literals(tree: ast.AST):
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


def test_no_simplified_chinese_in_ui_strings():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, text in _ui_string_literals(tree):
            if _SIMPLIFIED_ONLY & set(text):
                offenders.append(f"{path.relative_to(APP_DIR)}:{lineno}: {text[:40]}")
    assert offenders == []
