"""執行緒池／背景執行緒必須帶著任務歸屬（``contextvars``），UI→後台鏡像去重才分得出任務。

標準的 ``ThreadPoolExecutor`` 與 ``threading.Thread`` 不會繼承 context，在裡面寫出的後台記錄
會失去任務歸屬（退回只比文字＋時間窗）。專案內改用
``translation_tool.utils.ui_mirror.ContextThreadPoolExecutor`` 與 ``run_in_context``。
"""

from __future__ import annotations

import ast
import logging
import threading
from pathlib import Path

from app.tasks.task_session import TaskSession
from translation_tool.utils import ui_mirror
from translation_tool.utils.ui_mirror import (
    ContextThreadPoolExecutor,
    new_task_scope,
    run_in_context,
    task_scope,
)

ROOT = Path(__file__).resolve().parents[1]

#: 不需要任務歸屬、刻意沿用標準執行緒池的地方（路徑 -> 原因）
POOL_REVIEWED = {
    "app/views/cache_manager/cache_history_store.py": "全域、與任務無關的快取歷史檔鏡像寫入執行緒，不寫任務日誌",
}


def _py_files(*dirs):
    for d in dirs:
        yield from sorted((ROOT / d).rglob("*.py"))


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _call_target_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return getattr(func, "id", "")


# ---------------------------------------------------------------- 契約（AST）


def test_no_plain_thread_pool_executor_in_app_or_core():
    offenders = []
    for path in _py_files("app", "translation_tool"):
        rel = _rel(path)
        if rel == "translation_tool/utils/ui_mirror.py" or rel in POOL_REVIEWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and _call_target_name(node) == "ThreadPoolExecutor"
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "請改用 translation_tool.utils.ui_mirror.ContextThreadPoolExecutor（它會把任務歸屬帶進"
        "工作執行緒）；與任務無關的才登記到 POOL_REVIEWED 並寫明原因：\n  "
        + "\n  ".join(offenders)
    )


def test_core_threads_run_inside_the_callers_context():
    """translation_tool 內的核心流程自己開的 Thread，target 必須用 run_in_context 包起來。"""
    offenders = []
    for path in _py_files("translation_tool"):
        rel = _rel(path)
        if rel == "translation_tool/utils/ui_mirror.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _call_target_name(node) == "Thread"):
                continue
            target = next(
                (kw.value for kw in node.keywords if kw.arg == "target"), None
            )
            wrapped = (
                isinstance(target, ast.Call)
                and _call_target_name(target) == "run_in_context"
            )
            if not wrapped:
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "核心流程開的背景執行緒 target 請用 run_in_context(...) 包起來：\n  "
        + "\n  ".join(offenders)
    )


def test_pool_reviewed_entries_exist():
    for rel in POOL_REVIEWED:
        assert (ROOT / rel).exists(), rel


# ---------------------------------------------------------------- 行為


def test_pool_workers_inherit_the_submitting_tasks_attribution():
    seen = []
    with new_task_scope("pool") as scope, ContextThreadPoolExecutor(2) as pool:
        futures = [pool.submit(ui_mirror.current_task) for _ in range(4)]
        mapped = list(pool.map(lambda _i: ui_mirror.current_task(), range(4)))
        seen = [f.result() for f in futures] + mapped
    assert seen == [scope] * 8


def test_plain_thread_loses_attribution_but_run_in_context_keeps_it():
    results = {}

    def probe(key):
        results[key] = ui_mirror.current_task()

    with task_scope("T-1"):
        plain = threading.Thread(target=probe, args=("plain",))
        wrapped = threading.Thread(target=run_in_context(probe), args=("wrapped",))
        plain.start()
        wrapped.start()
        plain.join()
        wrapped.join()

    assert results == {"plain": None, "wrapped": "T-1"}


def test_concurrent_submissions_do_not_share_one_context_object():
    """每次 submit 各自複製 context：同一個 Context 不能被兩條執行緒同時進入。"""
    barrier = threading.Barrier(4)

    def work(i):
        barrier.wait(timeout=5)  # 四個工作同時進入
        return ui_mirror.current_task()

    with task_scope("T-2"), ContextThreadPoolExecutor(4) as pool:
        results = [f.result() for f in [pool.submit(work, i) for i in range(4)]]
    assert results == ["T-2"] * 4


def test_pool_backend_records_are_attributed_so_concurrent_tasks_stay_separate(caplog):
    """池內寫的後台記錄屬於提交它的任務：另一個任務同文字的 UI 行不會被它抵銷。"""
    a, b = TaskSession(name="A"), TaskSession(name="B")
    ui_mirror.BACKEND_SEEN_TRACKER.clear()
    ui_mirror.ensure_tracker()  # 應用程式啟動時由 update_logger_config 掛上

    def log_in_pool():
        logging.getLogger("core").info("池內處理完成")

    with caplog.at_level(logging.INFO):
        with task_scope(a.task_id), ContextThreadPoolExecutor(2) as pool:
            pool.submit(log_in_pool).result()
        b.add_log("池內處理完成")  # B 只有 UI：不可被 A 的池內記錄抵銷
        a.add_log("池內處理完成")  # A 自己的 UI 轉送：被抵銷

    mirrored = [
        r.getMessage() for r in caplog.records if getattr(r, "ui_mirrored", False)
    ]
    assert mirrored == ["[B] 池內處理完成"]
    ui_mirror.BACKEND_SEEN_TRACKER.clear()
