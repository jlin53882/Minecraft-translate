"""#114 worker-thread 契約的靜態護欄（搭配 ``docs/WORKER_THREAD_AUDIT.md`` 與行為測試）。

1. 背景執行緒啟動點必須與盤點一致：新增 ``threading.Thread`` 要同步更新
   ``EXPECTED_THREAD_SITES`` 與稽核文件（強迫每個新 worker 都被分類）。
2. Thread 的 target 函式本身不得直接改控制項或呼叫 ``page.update()``；
   要回 UI 必須透過巢狀的 ``apply`` 函式／lambda 交給 ``run_task`` 等 marshal 機制。
3. ``app/`` 內不得使用 ``time.sleep``（event loop 上睡眠會凍結 UI；worker 請用 Event／輪詢）。
4. UI 層不得直接呼叫會等待外部程式的 ``subprocess.run`` 等（見 ``test_view_lifecycle_contracts``）。

這些是啟發式檢查，補強而不是取代行為測試（``test_view_lifecycle_contracts.py``）。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

# 與 docs/WORKER_THREAD_AUDIT.md 的「背景執行緒啟動點」表一致
EXPECTED_THREAD_SITES = {
    "app/shell/config_effects.py": 1,
    "app/startup_tasks.py": 1,
    "app/tasks/operation_registry.py": 2,
    "app/views/moddb/scan_panel.py": 1,
    "app/views/moddb/translate_panel.py": 2,
    "app/views/pipeline/pipeline_session.py": 1,
}

# worker 內直接做這些就是「碰 UI」
_UI_ATTRS = {"value", "visible", "disabled", "color", "bgcolor", "label", "text"}
_UI_CALLS = {"update", "show_dialog", "pop_dialog"}


def _is_thread_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        getattr(node.func, "attr", None) == "Thread"
        or getattr(node.func, "id", None) == "Thread"
    )


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _thread_sites() -> dict[str, int]:
    sites: dict[str, int] = {}
    for path in sorted(APP.rglob("*.py")):
        count = sum(1 for n in ast.walk(_parse(path)) if _is_thread_call(n))
        if count:
            sites[path.relative_to(ROOT).as_posix()] = count
    return sites


def test_thread_start_sites_match_the_audit():
    assert _thread_sites() == EXPECTED_THREAD_SITES, (
        "背景執行緒啟動點與盤點不一致：請把新的 worker 分類後更新 "
        "docs/WORKER_THREAD_AUDIT.md 與 EXPECTED_THREAD_SITES"
    )


def _own_statements(func: ast.AST):
    """函式本身的節點（不含巢狀函式／lambda 內部——那些是要交給 event loop 的 apply）。"""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _target_name(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg != "target":
            continue
        value = kw.value
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return value.attr
        if (
            isinstance(value, ast.Call)
            and getattr(value.func, "attr", "") == "partial"
            and value.args
        ):
            first = value.args[0]
            return (
                first.id
                if isinstance(first, ast.Name)
                else getattr(first, "attr", None)
            )
    return None


def test_worker_threads_do_not_mutate_controls_directly():
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        tree = _parse(path)
        functions: dict[str, list[ast.AST]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, []).append(node)
        for call in (n for n in ast.walk(tree) if _is_thread_call(n)):
            name = _target_name(call)
            for func in functions.get(name or "", []):
                for node in _own_statements(func):
                    attr_store = (
                        isinstance(node, ast.Attribute)
                        and isinstance(node.ctx, ast.Store)
                        and node.attr in _UI_ATTRS
                    )
                    ui_call = (
                        isinstance(node, ast.Call)
                        and getattr(node.func, "attr", None) in _UI_CALLS
                    )
                    if attr_store or ui_call:
                        offenders.append(
                            f"{path.relative_to(ROOT).as_posix()}:{node.lineno} "
                            f"(worker {name})"
                        )
    assert offenders == [], (
        "worker thread 不得直接 mutate Flet Control／page.update；"
        "請交給 run_task／UiBatcher 等 marshal 機制：\n" + "\n".join(offenders)
    )


def test_no_time_sleep_in_app():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "sleep"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "time"
            ):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert offenders == [], "app/ 內不得使用 time.sleep：\n" + "\n".join(offenders)
