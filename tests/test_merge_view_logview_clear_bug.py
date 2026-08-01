"""Regression test for merge_view.start_merge 的 LogView API 誤用 bug。

User 報錯 (line 依版本會變動,但方法名稱 .controls.clear() 不變):
    [flet] Unhandled error in 'on_click' handler
    File "...app/views/merge_view.py", line ..., in start_merge
        self.log_view.controls.clear()
    AttributeError: 'LogView' object has no attribute 'controls'

背景:
    PR refactor/unified-log-view 把散落的 ft.ListView 包進 LogView widget
    (app/views/_log/log_view.py)。

    LogView 是 ft.Container 子類,內部用 self._list_view = ft.ListView(...),
    公開 API 為 .add() / .clear() / .sync_from_session() / .sync_entries() 等。

    但 merge_view 的舊 caller 仍用舊的 ft.ListView API:
        self.log_view.controls.clear()

    ft.Container 雖然有內建 .controls 屬性,
    但 LogView (Container 子類) 在某些 Flet 版本沒有 .controls,
    導致 AttributeError。

修法:
    把 self.log_view.controls.clear() 改成 self.log_view.clear()
    (LogView 對外 API)。

測試目標:
    1. Source-level: start_merge 不再含舊 buggy pattern
    2. Behavior-level: 真的呼叫 start_merge 到 log_view.clear() 那行後,內部 list 是空的
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
MERGE_VIEW_PATH = REPO_ROOT / "app/views/merge_view.py"


def _read_code_only(source_text: str) -> str:
    """遮罩 docstring 區段與行內註解,只回傳實際程式碼行。"""
    tree = ast.parse(source_text)
    mask: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                for ln in range(node.body[0].lineno, node.body[0].end_lineno + 1):
                    mask.add(ln)
    out = []
    for i, line in enumerate(source_text.splitlines(), start=1):
        if i in mask:
            continue
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


# =============================================================================
# Source-level — 確保舊 buggy pattern 不再回來
# =============================================================================

class TestStartMergeUsesLogViewClearApi:
    """merge_view.start_merge 必須用 self.log_view.clear(),不能直接 .controls.clear()。"""

    def _read_merge_view_code(self) -> str:
        return _read_code_only(MERGE_VIEW_PATH.read_text(encoding="utf-8"))

    def _extract_start_merge_block(self) -> str:
        """用 AST 抓出 start_merge function 的完整 body。"""
        src = MERGE_VIEW_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "start_merge":
                lines = src.splitlines()
                start_idx = node.lineno
                end_idx = node.end_lineno
                return "\n".join(lines[start_idx - 1: end_idx])
        raise AssertionError("找不到 start_merge function")

    def test_start_merge_block_does_not_call_log_view_controls_clear(self):
        """start_merge function 內不應出現 log_view.controls.clear()。"""
        block = self._extract_start_merge_block()
        offenders = re.findall(r"\.?log_view\.controls\.clear\(\)", block)
        assert not offenders, (
            "merge_view.start_merge 還在用 log_view.controls.clear(),\n"
            "這是 user 報錯的根因 (AttributeError: 'LogView' object has no attribute 'controls')。\n"
            "LogView widget API 是 .clear() 不是 .controls.clear()。\n"
            f"命中:{offenders[:3]}"
        )

    def test_merge_view_no_log_view_controls_clear_anywhere(self):
        """整個 merge_view.py 都不能出現 log_view.controls.clear()。"""
        code = self._read_merge_view_code()
        offenders = re.findall(r"\.?log_view\.controls\.clear\(\)", code)
        assert not offenders, (
            f"merge_view.py 還有 log_view.controls.clear() 用法:{offenders[:3]}"
        )

    def test_start_merge_block_uses_log_view_clear(self):
        """start_merge function 必須呼叫 self.log_view.clear()。"""
        block = self._extract_start_merge_block()
        assert "self.log_view.clear()" in block, (
            "start_merge function 沒呼叫 self.log_view.clear()。\n"
            "LogView widget 不暴露 .controls,必須走 .clear()。\n"
            f"實際塊:\n{block[:800]}"
        )


# =============================================================================
# Behavior-level — 真的跑 start_merge 路徑
# =============================================================================

class TestStartMergeActuallyClearsLogView:
    """模擬「user 選好 folder + output → 按開始合併」,確認 log_view 真的被清空。"""

    @pytest.fixture
    def merge_view_env(self, monkeypatch):
        """建立一個 MergeView,monkeypatch 必要的 session / ui_poller / config。"""
        from tests.conftest import mock_page, mock_filepicker
        from app.views import merge_view

        class _Session:
            def __init__(self, max_logs=2000):
                self.logs = []
                self.started = 0

            def start(self):
                self.started += 1

            def add_log(self, text):
                self.logs.append(text)

            def snapshot(self):
                return {"status": "DONE", "progress": 1.0, "logs": self.logs}

        monkeypatch.setattr(merge_view, "TaskSession", _Session)
        monkeypatch.setattr(merge_view, "load_config", lambda: {"lang_merger": {}})

        page = mock_page()
        view = merge_view.MergeView(page, mock_filepicker())

        view.log_view.add("stale line 1", level="info")
        view.log_view.add("stale line 2", level="info")
        assert len(view.log_view._list_view.controls) == 2, (
            "測試前提:log_view 內已有 2 行"
        )

        monkeypatch.setattr(view, "_start_ui_poller", lambda: None)

        return view

    def test_start_merge_clears_log_view_without_attribute_error(self, merge_view_env):
        """走到 start_merge 內部 log_view.clear() 那行,不應拋 AttributeError。"""
        view = merge_view_env

        view.input_mode_group.value = "folder"
        view.folder_path_field.value = r"D:\fake\mods"
        view.output_dir_field.value = r"D:\fake\output"

        before_count = len(view.log_view._list_view.controls)
        assert before_count == 2

        try:
            view.start_merge(None)
        except AttributeError as ex:
            pytest.fail(
                f"start_merge 拋 AttributeError (user 報的 bug):{ex}\n"
                "→ 確認 self.log_view.clear() 而不是 self.log_view.controls.clear()"
            )

        after_count = len(view.log_view._list_view.controls)
        assert after_count == 0, (
            f"log_view 內還剩 {after_count} 筆,start_merge 沒清空它"
        )

    def test_log_view_exposes_clear_method(self, merge_view_env):
        """LogView widget 必須暴露 .clear() method。"""
        view = merge_view_env
        assert callable(getattr(view.log_view, "clear", None)), (
            "LogView 必須有 .clear() method"
        )


# =============================================================================
# 路徑慣例 — 不要寫死使用者路徑
# =============================================================================

class TestNoHardcodedPaths:
    """測試本身不應寫死 Windows 使用者目錄絕對路徑。"""

    def test_test_file_does_not_use_hardcoded_user_paths(self):
        text = Path(__file__).read_text(encoding="utf-8")
        code = _read_code_only(text)
        forbidden = re.findall(r"[A-Za-z]:\\\\Users", code)
        forbidden.extend(re.findall(r"/Users/[A-Za-z]", code))
        forbidden.extend(re.findall(r"/home/[A-Za-z]", code))
        assert not forbidden, (
            f"測試不應寫死使用者路徑:{forbidden}\n"
            "請用 tmp_path 或 monkeypatch"
        )

    def test_test_setup_uses_synthetic_paths_only(self):
        """測試輸入路徑只能用合成假路徑。"""
        text = Path(__file__).read_text(encoding="utf-8")
        all_drive_paths = re.findall(r"[A-Za-z]:\\\\[A-Za-z0-9_]+", text)
        forbidden_real = [
            p for p in all_drive_paths
            if not p.split("\\")[2].startswith(("fake", "test", "temp"))
        ]
        assert not forbidden_real, (
            f"測試使用真實驅動槽路徑:{forbidden_real}\n"
            "只允許 D:\\fake\\... 或 tmp_path"
        )
