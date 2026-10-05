"""PR #104 Finding 1：流水線「預覽結果」必須真的跑完 finalization。

原本 do_final 定義在 poll_preview 內、且在 await do_final(None) 之後，
執行時拋 UnboundLocalError，對話框永遠停在「預覽掃描中」。
這裡執行 production 的按鈕 → 背景掃描 → event loop coroutine 流程。
"""

import asyncio

import flet as ft
import pytest

from app.views.pipeline import pipeline_extract_dialog as ped


class _Page:
    width = 1280
    height = 900

    def __init__(self):
        self.overlay = []
        self.tasks = []

    def update(self, *a, **k):
        pass

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))

    def show_dialog(self, d):
        self.overlay.append(d)

    def pop_dialog(self):
        pass


class _SyncThread:
    """讓背景掃描在測試中同步執行（UI 更新仍只在 run_task 的 coroutine 裡）。"""

    def __init__(self, target=None, daemon=None, **kw):
        self._target = target

    def start(self):
        self._target()


def _texts(control):
    """收集控制項樹中所有 Text 的內容。"""
    out = []
    stack = [control]
    while stack:
        c = stack.pop()
        if isinstance(c, ft.Text) and c.value:
            out.append(c.value)
        for attr in ("content", "controls"):
            child = getattr(c, attr, None)
            if isinstance(child, list):
                stack.extend(child)
            elif child is not None:
                stack.append(child)
    return out


def _run_preview(monkeypatch, tmp_path, generator):
    mods = tmp_path / "mods"
    mods.mkdir()
    monkeypatch.setattr(ped.threading, "Thread", _SyncThread)
    monkeypatch.setattr(ped, "find_jar_files", lambda d: ["a.jar", "b.jar"])
    monkeypatch.setattr(ped, "preview_extraction_generator", generator)

    page = _Page()
    ped.open_extract_dialog(
        page,
        ft.FilePicker(),
        input_path=str(mods),
        output_path=str(tmp_path / "out"),
        on_run_extraction=lambda *a: None,
        lang_code_checks={},
        show_snack_bar=lambda *a, **k: None,
    )
    main_dialog = page.overlay[0]
    preview_btn = next(
        b for b in main_dialog.actions if getattr(b, "content", None) == "預覽結果"
    )
    preview_btn.on_click(None)

    preview_dialog = page.overlay[-1]
    # 一開始只顯示「正在搜尋 JAR」：JAR 探索在背景執行緒，不在 UI handler 內
    assert "正在搜尋 JAR..." in _texts(preview_dialog.content)
    # 執行 event loop 上的輪詢 + finalization（production coroutine）
    while page.tasks:
        handler, args = page.tasks.pop(0)
        asyncio.run(handler(*args))
    return preview_dialog


def test_preview_success_finalizes_dialog(monkeypatch, tmp_path):
    def gen(mods, mode, lang_codes=None):
        yield {"progress": 0.5, "current": 1, "total": 2}
        yield {
            "progress": 1.0,
            "result": {
                "total_files": 3,
                "total_size_mb": 0.1,
                "preview_results": [
                    {"jar": "a.jar", "count": 3},
                    {"jar": "b.jar", "count": 0},
                ],
            },
        }

    dlg = _run_preview(monkeypatch, tmp_path, gen)
    texts = _texts(dlg.content)
    assert not any("預覽掃描中" in t for t in texts)
    assert any("預計提取：3 個檔案" in t for t in texts)
    assert any("a.jar" in t for t in texts)
    assert not any("b.jar" in t for t in texts)  # N3：0 個檔案的 JAR 不列出
    assert [a.content for a in dlg.actions] == ["確定"]


@pytest.mark.parametrize("mode", ["generator_error", "exception"])
def test_preview_error_finalizes_dialog(monkeypatch, tmp_path, mode):
    def gen(mods, mode_, lang_codes=None):
        if mode == "exception":
            raise RuntimeError("boom")
        yield {"error": "boom"}

    dlg = _run_preview(monkeypatch, tmp_path, gen)
    texts = _texts(dlg.content)
    assert any("錯誤" in t and "boom" in t for t in texts)
    assert not any("預覽掃描中" in t for t in texts)
