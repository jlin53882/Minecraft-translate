"""規模防護欄（取代 #114 的 800 行／100 行硬限制）。

行數只是防護欄，不是重構目標：請依職責、ownership、依賴邊界與控制流程複雜度拆分，
不要為了通過行數門檻而拆檔或抽沒有獨立語義的 helper。

- 函式：< 80 行正常；80–149 行為「審查區」；>= 150 行硬擋。
- View 檔案：< 800 行正常；800–1199 行為「審查區」；>= 1200 行硬擋。
- 審查區採 ratchet（棘輪）：``tests/data/size_baseline.json`` 記錄現況，
  新增的超標項目、或已記錄的項目變得更長才失敗；縮短或消失不會失敗。
  確認是合理成長（例如同一職責的宣告式版面變長）後，用
  ``UPDATE_SIZE_BASELINE=1 pytest tests/test_view_size_limits.py`` 更新快照，
  並在 PR 說明為什麼不需要拆。
- 複雜度（近似 cyclomatic）> 12、巢狀 > 4 只輸出警告，不擋 CI。
"""

from __future__ import annotations

import ast
import json
import os
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BASELINE = ROOT / "tests" / "data" / "size_baseline.json"

SOFT_FUNCTION_LINES = 80
HARD_FUNCTION_LINES = 150
SOFT_VIEW_LINES = 800
HARD_VIEW_LINES = 1200
WARN_COMPLEXITY = 12
WARN_NESTING = 4

_BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.comprehension,
)
_NEST_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _functions():
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path, node


def _complexity(func: ast.AST) -> int:
    score = 1
    for node in ast.walk(func):
        if isinstance(node, _BRANCH_NODES):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += len(node.values) - 1
    return score


def _nesting(node: ast.AST, depth: int = 0) -> int:
    deepest = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # 巢狀函式自己算
        step = depth + 1 if isinstance(child, _NEST_NODES) else depth
        deepest = max(deepest, _nesting(child, step))
    return deepest


def _measure() -> tuple[dict[str, int], dict[str, int], dict[str, int], list[str]]:
    """回傳 (函式行數, View 檔案行數, 硬限制違規, 複雜度警告)。

    函式行數的鍵為 ``路徑:名稱``（同名取最長者）；只記錄審查區以上的項目。
    """
    funcs: dict[str, int] = {}
    files: dict[str, int] = {}
    hard: dict[str, int] = {}
    warns: list[str] = []
    for path, node in _functions():
        length = node.end_lineno - node.lineno + 1
        key = f"{_rel(path)}:{node.name}"
        if length >= HARD_FUNCTION_LINES:
            hard[f"{key}:{node.lineno}"] = length
        if length >= SOFT_FUNCTION_LINES:
            funcs[key] = max(funcs.get(key, 0), length)
        cx, nest = _complexity(node), _nesting(node)
        if cx > WARN_COMPLEXITY or nest > WARN_NESTING:
            warns.append(f"{key}:{node.lineno} 複雜度 {cx}、巢狀 {nest}")
    for path in sorted((APP / "views").rglob("*.py")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines >= HARD_VIEW_LINES:
            hard[_rel(path)] = lines
        if lines >= SOFT_VIEW_LINES:
            files[_rel(path)] = lines
    return funcs, files, hard, warns


def _load_baseline() -> dict[str, dict[str, int]]:
    if not BASELINE.is_file():
        return {"functions": {}, "view_files": {}}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_nothing_reaches_the_hard_limits():
    _funcs, _files, hard, _warns = _measure()
    assert hard == {}, (
        "超過硬限制（函式 >= 150 行／View >= 1200 行），請依職責拆分：\n"
        + "\n".join(f"{k}: {v} 行" for k, v in hard.items())
    )


def test_review_zone_does_not_grow():
    """審查區（函式 80–149 行／View 800–1199 行）不得新增或變長（ratchet）。"""
    funcs, files, _hard, warns = _measure()
    if os.environ.get("UPDATE_SIZE_BASELINE"):
        BASELINE.write_text(
            json.dumps(
                {
                    "functions": dict(sorted(funcs.items())),
                    "view_files": dict(sorted(files.items())),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    baseline = _load_baseline()
    problems = []
    for label, current, recorded in (
        ("函式", funcs, baseline["functions"]),
        ("View 檔案", files, baseline["view_files"]),
    ):
        for key, length in sorted(current.items()):
            old = recorded.get(key)
            if old is None:
                problems.append(f"{label}新進入審查區：{key}（{length} 行）")
            elif length > old:
                problems.append(f"{label}變長：{key}（{old} → {length} 行）")
    assert problems == [], (
        "審查區項目新增或變長。請確認它仍是單一職責（不是為了行數硬拆、也不是職責膨脹）；"
        "合理的話用 UPDATE_SIZE_BASELINE=1 更新 tests/data/size_baseline.json 並在 PR 說明：\n"
        + "\n".join(problems)
    )
    if warns:
        warnings.warn(
            f"複雜度／巢狀偏高的函式 {len(warns)} 個（僅提醒）：\n"
            + "\n".join(warns[:20]),
            stacklevel=1,
        )
