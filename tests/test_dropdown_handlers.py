"""Flet 1.0 的 ft.Dropdown 只觸發 on_select；on_change 會被靜默忽略。"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _is_dropdown_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Dropdown"
    )


def _target_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _dropdown_on_change_uses(tree: ast.AST) -> list[int]:
    dropdown_names: set[str] = set()
    bad: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_dropdown_call(node.value):
            for target in node.targets:
                key = _target_key(target)
                if key:
                    dropdown_names.add(key)
        if _is_dropdown_call(node):
            bad += [node.lineno for kw in node.keywords if kw.arg == "on_change"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "on_change"
                    and _target_key(target.value) in dropdown_names
                ):
                    bad.append(node.lineno)
    return bad


def test_detector_flags_dropdown_on_change():
    src = (
        "import flet as ft\nself.dd = ft.Dropdown(on_change=f)\nself.dd.on_change = f\n"
    )
    assert _dropdown_on_change_uses(ast.parse(src)) == [2, 3]


def test_no_dropdown_uses_on_change():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [
            f"{path.relative_to(APP_DIR)}:{ln}" for ln in _dropdown_on_change_uses(tree)
        ]
    assert offenders == []
