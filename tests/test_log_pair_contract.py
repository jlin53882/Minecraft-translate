"""後台記錄與畫面訊息成對出現時，兩邊文字必須一致（去重才會生效）。

UI→後台鏡像（``translation_tool/utils/ui_mirror.py``）靠「文字完全相同」判斷一則畫面訊息
後台是否已經有了。所以在同一個 ``except`` 區塊裡，只要同時有「後台記錄」與「會被鏡像的畫面訊息」，
兩邊文字就必須相同；只改其中一邊（例如把後台改成 ``{e!r}``、畫面還是 ``{e}``），
後台就會對同一個事件記兩次。

這個測試用 AST 掃描 ``app/`` 與 ``translation_tool/``：

- 畫面訊息不鏡像（``add_log_unmirrored`` / ``mirror=False``）→ 通過。
- 後台與畫面的訊息運算式（``ast.unparse``）相同 → 通過。
- 其餘必須登記在 ``REVIEWED`` 並寫明為什麼是安全的；新增的成對寫法會讓測試失敗，
  請讓兩邊文字一致，或改用 ``add_log_unmirrored``。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("app", "translation_tool")

BACKEND_CALLS = {
    "log_error",
    "log_warning",
    "log_info",
    "log_exception",
    "error",
    "warning",
    "exception",
    "info",
}
# 會把文字寫進畫面並鏡像到後台的呼叫
MIRRORING_UI_CALLS = {
    "add_log",
    "_session_log",
    "mirror_session_log",
    "_safe_add_log",
    "_safe_session_log",
    "_append_log",
    "add_start_log",
}

#: (相對路徑, 函式名稱) -> 為什麼這一組成對寫法是安全的（人工確認過）
REVIEWED: dict[tuple[str, str], str] = {
    (
        "app/services_impl/pipelines/_task_runner.py",
        "run_callable_task",
    ): "if/else 互斥：只會走 mirror_session_log 或 logger.error 其中一條",
    (
        "translation_tool/core/jar_processor_extract.py",
        "run_extraction_process_impl",
    ): "後台第一行與畫面文字相同（[ERROR] 提取 … 時產生例外），細節接在後面；多行訊息逐行去重",
    (
        "translation_tool/core/lang_merge_extracted_assets.py",
        "merge_extracted_to_assets",
    ): "後台第一行與畫面文字相同（… 錯誤: {exc!r}），後面接 traceback；"
    "由 test_merge_ext_assets_fatal_exception_is_written_to_backend_once 鎖住",
    (
        "app/services_impl/pipelines/lookup_service.py",
        "run_batch_lookup_service",
    ): "backend 與 UI 使用同一個 message 變數",
}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return getattr(func, "id", "")


def _first_arg_text(node: ast.Call) -> str | None:
    return ast.unparse(node.args[0]) if node.args else None


def _is_unmirrored(node: ast.Call) -> bool:
    if _call_name(node) == "add_log_unmirrored":
        return True
    return any(
        kw.arg == "mirror"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is False
        for kw in node.keywords
    )


def _enclosing_function(tree: ast.AST, lineno: int) -> str:
    best = None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.lineno <= lineno <= (node.end_lineno or node.lineno)
            and (best is None or node.lineno > best.lineno)
        ):
            best = node
    return best.name if best else "<module>"


def _handler_pairs():
    """回傳 [(相對路徑, 函式, 行號, 後台文字集合, 畫面文字集合)]（只含畫面訊息會被鏡像的）。"""
    found = []
    for scan in SCAN_DIRS:
        for path in sorted((ROOT / scan).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for handler in ast.walk(tree):
                if not isinstance(handler, ast.ExceptHandler):
                    continue
                backend, ui = [], []
                for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
                    if isinstance(node, ast.Call):
                        name = _call_name(node)
                        if name in BACKEND_CALLS:
                            backend.append(_first_arg_text(node))
                        elif name in MIRRORING_UI_CALLS and not _is_unmirrored(node):
                            # add_log(session, text) 與 _session_log(session, text) 的文字在第 2 個參數
                            args = node.args
                            text_node = (
                                args[1]
                                if name
                                in {
                                    "_session_log",
                                    "mirror_session_log",
                                    "_safe_add_log",
                                    "_safe_session_log",
                                }
                                and len(args) > 1
                                else (args[0] if args else None)
                            )
                            if name == "mirror_session_log" and len(args) > 2:
                                text_node = args[2]
                            ui.append(ast.unparse(text_node) if text_node else None)
                    elif isinstance(node, ast.Dict):
                        for key, value in zip(node.keys, node.values, strict=False):
                            is_log_key = (
                                isinstance(key, ast.Constant) and key.value == "log"
                            )
                            is_none = (
                                isinstance(value, ast.Constant) and value.value is None
                            )
                            if is_log_key and not is_none:
                                ui.append(ast.unparse(value))
                if backend and ui:
                    found.append(
                        (
                            str(path.relative_to(ROOT)).replace("\\", "/"),
                            _enclosing_function(tree, handler.lineno),
                            handler.lineno,
                            {t for t in backend if t},
                            {t for t in ui if t},
                        )
                    )
    return found


def test_backend_and_ui_messages_in_one_handler_use_identical_text():
    problems = []
    for rel, func, lineno, backend, ui in _handler_pairs():
        if backend & ui:
            continue  # 有一組文字完全相同 → 去重會抵銷
        if (rel, func) in REVIEWED:
            continue
        problems.append(f"{rel}:{lineno} ({func})")
    assert not problems, (
        "同一個 except 區塊同時有後台記錄與會被鏡像的畫面訊息，但兩邊文字不同，"
        "後台會對同一個事件記兩次。請讓兩邊文字一致（例如都用 {e!r}），"
        "或畫面訊息改用 add_log_unmirrored；確認安全後才登記到 REVIEWED：\n  "
        + "\n  ".join(problems)
    )


def test_reviewed_entries_are_not_stale():
    pairs = {(rel, func) for rel, func, *_ in _handler_pairs()}
    stale = [key for key in REVIEWED if key not in pairs]
    assert not stale, f"REVIEWED 內已不存在的項目，請移除：{stale}"
