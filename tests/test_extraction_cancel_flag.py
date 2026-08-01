'''Regression test for 提取進行中按取消真的中斷 Service (問題 4)。

User 報錯:
    「在提取的時候 點取消 是不會取消提取的工作流程
     預期取消 會回到 提取設定對話框中」

Root cause:
    原本 on_cancel_click 只設 state["cancelled"] = True,
    但 run_extraction_loop 是用另一份 cancelled_flag list 來偵測取消,
    所以按「取消」背景線程繼續跑,只是 UI 顯示「正在取消...」。

修法:
    加 outer-scope extraction_cancel_flag list,
    on_cancel_click 同步設 extraction_cancel_flag[0] = True,
    run_extraction 內 reset 後把 reference 傳給 Service。
'''
from __future__ import annotations

import re
from pathlib import Path

import pytest


EXTRACTOR_DIALOG = Path(__file__).parent.parent / "app" / "views" / "extractor" / "extractor_dialog.py"


def _read_code_only(src):
    import ast
    tree = ast.parse(src)
    mask = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                for ln in range(node.body[0].lineno, node.body[0].end_lineno + 1):
                    mask.add(ln)
    out = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i in mask:
            continue
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return chr(10).join(out)


class TestExtractionCancelFlag:
    def _read_function_body(self, fn_name):
        src = EXTRACTOR_DIALOG.read_text(encoding="utf-8")
        code = _read_code_only(src)
        pattern = "def " + re.escape(fn_name) + r"\([^)]*\):(.*?)(?=\n    def )"
        m = re.search(pattern, code, flags=re.DOTALL)
        assert m, f"找不到 {fn_name} function"
        return m.group(1)

    def test_outer_scope_flag_exists(self):
        body = self._read_function_body("open_extractor_dialog")
        assert "extraction_cancel_flag = [False]" in body, (
            "outer-scope extraction_cancel_flag 沒建立, "
            "on_cancel_click 無法中斷 Service"
        )

    def test_run_extraction_resets_and_references_outer_flag(self):
        body = self._read_function_body("run_extraction")
        assert "extraction_cancel_flag[0] = False" in body, (
            "run_extraction 沒 reset extraction_cancel_flag, "
            "連續任務之間 cancel flag 會殘留"
        )
        assert "cancelled_flag = extraction_cancel_flag" in body, (
            "cancelled_flag 沒用 outer-scope reference, "
            "Service 看不到 outer flag 修改, 按取消不會中斷"
        )
        assert "cancelled_flag = [False]" not in body, (
            "run_extraction 還有 cancelled_flag = [False] (local list), "
            "Service 用 local list 偵測, outer flag 修改無效"
        )

    def test_on_cancel_click_sets_outer_flag(self):
        body = self._read_function_body("on_cancel_click")
        assert "extraction_cancel_flag[0] = True" in body, (
            "on_cancel_click 沒設 extraction_cancel_flag, "
            "按「取消」背景線程繼續跑"
        )
        assert 'state["cancelled"] = True' in body, (
            "on_cancel_click 沒設 state['cancelled']"
        )


class TestServiceRespectsCancellation:
    def test_run_extraction_loop_signature(self):
        from app.services_impl.pipelines.extract_service import run_extraction_loop
        import inspect
        sig = inspect.signature(run_extraction_loop)
        assert "cancelled_flag" in sig.parameters

    def test_run_extraction_loop_returns_early_when_cancelled(self):
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        def gen():
            for i in range(100):
                yield {"progress": i / 100.0, "log": f"step {i}"}

        flag = [False]
        call_count = [0]

        def on_update(update):
            call_count[0] += 1
            if call_count[0] >= 2:
                flag[0] = True

        stats = run_extraction_loop(gen(), cancelled_flag=flag, on_update=on_update)
        assert call_count[0] < 10, (
            f"run_extraction_loop 應該在 cancellation 後 early return, "
            f"但 on_update 被呼叫 {call_count[0]} 次"
        )
        assert stats["success"] == 0
        assert stats["warnings"] == 0
        assert stats["failures"] == 0
        # Phase 3 (2026-07-13) sub-dict default
        assert stats["lang"] == {"success": 0, "warnings": 0, "failures": 0}
        assert stats["book"] == {"success": 0, "warnings": 0, "failures": 0}


class TestPathConvention:
    def test_no_hardcoded_user_paths(self):
        import re as _re
        text = Path(__file__).read_text(encoding="utf-8")
        code = _read_code_only(text)
        forbidden_patterns = [
            _re.compile(r"[A-Za-z]:\\Users"),
            _re.compile(r"/Users/[A-Za-z]"),
            _re.compile(r"/home/[A-Za-z]"),
        ]
        for p in forbidden_patterns:
            assert not p.findall(code), (
                f"測試碼不應寫死絕對使用者路徑 {p.pattern}"
            )
