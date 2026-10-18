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


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _scope_users() -> dict[str, set[str]]:
    users: dict[str, set[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls_or_mod in [
            tree,
            *[n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)],
        ]:
            owner = getattr(cls_or_mod, "name", None)
            for fn in cls_or_mod.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = _called_names(fn)
                if "session_task_scope" in names:
                    key = f"{owner}.{fn.name}" if owner else fn.name
                    users[key] = names
    return users


def test_functions_using_session_task_scope_also_write_a_screen_summary():
    users = _scope_users()
    assert EXPECTED_USERS <= users.keys(), (
        "掃描範圍失效或呼叫端改名，請同步更新 EXPECTED_USERS"
    )
    missing = sorted(
        name
        for name, called in users.items()
        if not any("add_log" in called_name for called_name in called)
    )
    assert not missing, (
        "使用 session_task_scope 的函式沒有 add_log 系列呼叫，"
        f"失敗時畫面不會有任何訊息：{missing}"
    )
