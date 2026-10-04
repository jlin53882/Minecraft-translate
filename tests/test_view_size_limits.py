"""#114 驗收的規模目標：View 檔案 < 800 行、函式 < 100 行（防止倒退）。"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
MAX_VIEW_FILE_LINES = 800
MAX_FUNCTION_LINES = 100


def test_view_files_are_under_the_line_limit():
    too_big = []
    for path in sorted((APP / "views").rglob("*.py")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines >= MAX_VIEW_FILE_LINES:
            too_big.append(f"{path.relative_to(ROOT).as_posix()}: {lines} 行")
    assert too_big == [], "View 檔案超過行數上限：\n" + "\n".join(too_big)


def test_no_function_in_app_reaches_the_length_limit():
    too_long = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = node.end_lineno - node.lineno + 1
                if length >= MAX_FUNCTION_LINES:
                    too_long.append(
                        f"{path.relative_to(ROOT).as_posix()}:{node.lineno} "
                        f"{node.name}: {length} 行"
                    )
    assert too_long == [], "函式超過長度上限：\n" + "\n".join(too_long)
