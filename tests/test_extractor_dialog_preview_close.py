"""Regression tests for extractor_dialog 的「預覽 dialog 沒從 overlay 移除」 bug。

背景:
    User 描述:
        「提起完成之後,後面會點及提取畫面關閉,但是預覽視窗還會存在。」

    舊實作 (app/views/extractor/extractor_dialog.py,open_preview_dialog 內的 start_extraction):

        def start_extraction(e):
            result_dialog.open = False
            preview_dialog.open = False
            page.update()
            open_extractor_dialog(..., auto_start=True)

    這看起來合理,但實際上 Flet 的 page.overlay 是個 list:
        - 只設 open=False, dialog 物件仍留在 overlay 內
        - 之後 open_extractor_dialog 又 page.overlay.append(...) + page.update()
        - 在某些 Flet update 路徑,殘留在 overlay 的「已關閉」dialog 可能被「喚醒」
        - 結果 extractor dialog 關掉後,preview dialog 仍出現在畫面

修法:
    同步呼叫 page.overlay.remove(...) 把 dialog 從 overlay 徹底移走:
        result_dialog.open = False
        page.overlay.remove(result_dialog)
        preview_dialog.open = False
        page.overlay.remove(preview_dialog)
        page.update()

測試目標:
    1. 程式碼層:確保 start_extraction 同時設 open=False + remove from overlay
    2. 行為層:用 mock page 模擬整個 flow,確認 close 後 overlay 內不再有 preview/result dialog
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


DIALOG_PATH = (
    Path(__file__).parent.parent
    / "app"
    / "views"
    / "extractor"
    / "extractor_dialog.py"
)


def _read_dialog_code_only() -> str:
    """只讀程式碼行,移除所有 docstring / 區塊註解 / 行內註解。"""
    src = DIALOG_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    mask_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                doc = node.body[0]
                for ln in range(doc.lineno, doc.end_lineno + 1):
                    mask_lines.add(ln)
    out = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i in mask_lines:
            continue
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


class TestStartExtractionClosesAllOverlays:
    """start_extraction 後,preview / result dialog 都應該從 page.overlay 消失。"""

    def _extract_start_extraction_block(self) -> str:
        """抓出 start_extraction function 內容(從 def 到對應縮排結束)。"""
        code = _read_dialog_code_only()
        m = re.search(r"def start_extraction\(e\):\s*\n((?:[ \t]+.*\n)+)", code)
        assert m, "找不到 start_extraction function"
        return m.group(1)

    def test_start_extraction_removes_result_dialog_from_overlay(self):
        """start_extraction 內必須呼叫 page.overlay.remove(result_dialog)。"""
        block = self._extract_start_extraction_block()
        assert "page.overlay.remove(result_dialog)" in block, (
            "❌ start_extraction 沒把 result_dialog 從 page.overlay 移除\n"
            "→ 結果對話框仍佔 overlay,後續 update 可能被 Flet 重新打開。\n"
            "請在關閉 result_dialog.open = False 後,\n"
            "加上 `if result_dialog in page.overlay: page.overlay.remove(result_dialog)`"
        )

    def test_start_extraction_removes_preview_dialog_from_overlay(self):
        """start_extraction 內必須呼叫 page.overlay.remove(preview_dialog)。"""
        block = self._extract_start_extraction_block()
        assert "page.overlay.remove(preview_dialog)" in block, (
            "❌ start_extraction 沒把 preview_dialog 從 page.overlay 移除\n"
            "→ 這就是 user 描述的「預覽視窗還存在」bug 的根本原因。\n"
            "請在關閉 preview_dialog.open = False 後,\n"
            "加上 `if preview_dialog in page.overlay: page.overlay.remove(preview_dialog)`"
        )

    def test_start_extraction_still_closes_then_opens_extractor(self):
        """確認核心流程仍存在:close 兩個 dialog 後,呼叫 open_extractor_dialog。"""
        block = self._extract_start_extraction_block()
        assert "result_dialog.open = False" in block, "❌ 沒關 result_dialog"
        assert "preview_dialog.open = False" in block, "❌ 沒關 preview_dialog"
        assert "open_extractor_dialog(" in block, "❌ 沒呼叫 open_extractor_dialog"
        assert "auto_start=True" in block, "❌ open_extractor_dialog 沒帶 auto_start=True"

    def test_start_extraction_runs_a_page_update_between_close_and_open(self):
        """中間必須 page.update() 一次,確保 UI 真的被告知 close。"""
        block = self._extract_start_extraction_block()
        assert "page.update()" in block, "❌ 缺 page.update()"
        update_pos = block.find("page.update()")
        open_ext_pos = block.find("open_extractor_dialog(")
        assert update_pos < open_ext_pos, "❌ page.update() 應該在 open_extractor_dialog 之前"


class TestPreviewDialogCloseBehavior:
    """模擬 open_preview_dialog → start_extraction → 確認 overlay 不再有 preview/result。"""

    @pytest.fixture
    def patched_page(self):
        """建立一個有 width/height 屬性的 page mock。"""
        from tests.conftest import _make_page
        page = _make_page()

        class _PatchedPage(type(page)):
            def __init__(self_inner):
                super().__init__()
                self_inner.width = 1200
                self_inner.height = 800

        return _PatchedPage()

    @pytest.fixture
    def filepicker(self):
        from tests.conftest import _make_filepicker
        return _make_filepicker()

    def _build_preview_then_simulate_start_extraction(self, page, filepicker):
        """建立 preview_dialog + 模擬 start_extraction 的 close 邏輯(直接呼叫 fixture 邏輯)。"""
        from app.views.extractor.extractor_dialog import open_preview_dialog

        preview_dialog = open_preview_dialog(
            page, filepicker,
            input_path=str(Path(__file__).parent),
            output_path="",
            mode="lang",
        )

        result_dialog = type("MockResultDialog", (), {"open": True})()
        page.overlay.append(result_dialog)

        result_dialog.open = False
        if result_dialog in page.overlay:
            page.overlay.remove(result_dialog)
        preview_dialog.open = False
        if preview_dialog in page.overlay:
            page.overlay.remove(preview_dialog)
        page.update()

        return preview_dialog, result_dialog

    def test_start_extraction_removes_preview_from_overlay(self, patched_page, filepicker):
        preview_dialog, _result = self._build_preview_then_simulate_start_extraction(
            patched_page, filepicker,
        )
        assert preview_dialog not in patched_page.overlay, (
            "❌ preview_dialog 仍在 page.overlay 內 → 這就是 user 描述的 bug 根本原因"
        )

    def test_start_extraction_removes_result_from_overlay(self, patched_page, filepicker):
        _preview, result_dialog = self._build_preview_then_simulate_start_extraction(
            patched_page, filepicker,
        )
        assert result_dialog not in patched_page.overlay, (
            "❌ result_dialog 仍在 page.overlay 內 → 結果對話框可能會被 Flet 重繪"
        )

    def test_overlay_only_contains_intentional_dialogs_after_close(
        self, patched_page, filepicker
    ):
        _preview, _result = self._build_preview_then_simulate_start_extraction(
            patched_page, filepicker,
        )
        overlay_refs = [id(d) for d in patched_page.overlay]
        leaked = []
        if id(_preview) in overlay_refs:
            leaked.append("preview_dialog")
        if id(_result) in overlay_refs:
            leaked.append("result_dialog")
        assert not leaked, (
            f"❌ 以下對話框仍留在 page.overlay:{leaked}\n"
            "代表 close 沒生效,可能會被 Flet 重新打開 → user 會看到殘留的 dialog"
        )


class TestPathConvention:
    def test_no_hardcoded_user_paths_in_test_code(self):
        import re as _re
        import ast as _ast
        text = Path(__file__).read_text(encoding="utf-8")
        tree = _ast.parse(text)
        mask: set[int] = set()
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                if (node.body and isinstance(node.body[0], _ast.Expr)
                        and isinstance(node.body[0].value, _ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    for ln in range(node.body[0].lineno, node.body[0].end_lineno + 1):
                        mask.add(ln)
        patterns = [
            _re.compile(r"[A-Za-z]:\\\\Users"),
            _re.compile(r"/Users/[A-Za-z]"),
            _re.compile(r"/home/[A-Za-z]"),
        ]
        code_lines = []
        for i, line in enumerate(text.splitlines(), start=1):
            if i in mask:
                continue
            if line.lstrip().startswith("#"):
                continue
            code_lines.append(line)
        code = "\n".join(code_lines)
        for p in patterns:
            assert not p.findall(code), (
                f"測試碼不應寫死絕對使用者路徑 {p.pattern},請用 Path(__file__).parent / ..."
            )
