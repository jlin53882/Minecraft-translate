"""``session_task_scope`` 只讓後台記錄帶上任務標籤，不會把內容送進任務畫面。

服務結束後工作執行緒已被 ``set_session(None)`` 解除登記，邊界 ``except`` 裡用
``session_task_scope`` 寫的完整堆疊只留在後台。使用它的函式必須在同一處用 ``add_log`` 系列
把使用者要看的摘要寫進 session，否則失敗時畫面上什麼都沒有。
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
EXPECTED_USERS = {
    "MergeView._run_merge_worker",
    "SpeciesBulkLookup._run",
    "_report_service_failure",
}


def _calls(fn: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            found.append((name, node.lineno))
    return found


def _scope_users() -> dict[str, bool]:
    """函式名稱 -> 最後一個 ``session_task_scope`` 之後是否還有 ``add_log`` 系列呼叫。"""
    users: dict[str, bool] = {}
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        containers = [tree, *[n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]]
        for container in containers:
            owner = getattr(container, "name", None)
            for fn in container.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                calls = _calls(fn)
                scope_lines = [
                    line for name, line in calls if name == "session_task_scope"
                ]
                if scope_lines:
                    last = max(scope_lines)
                    users[f"{owner}.{fn.name}" if owner else fn.name] = any(
                        "add_log" in name and line >= last for name, line in calls
                    )
    return users


def test_functions_using_session_task_scope_also_write_a_screen_summary():
    users = _scope_users()
    assert EXPECTED_USERS <= users.keys(), (
        "掃描範圍失效或呼叫端改名，請同步更新 EXPECTED_USERS"
    )
    missing = sorted(name for name, has_summary in users.items() if not has_summary)
    assert not missing, (
        "session_task_scope 之後沒有 add_log 系列呼叫，失敗時畫面不會有任何訊息："
        f"{missing}"
    )
