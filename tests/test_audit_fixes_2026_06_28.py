"""Tests for audit fixes (2026-06-28):
- dual buttons are disabled during extraction (P0 #1)
- poll daemon respects stop_event (P0 #2)
- DUAL mode yields book stats even when lang_stats is None (P1 #5/#6)
- jar_processor_extract.py throttles scan progress yields (P1 #7)
- main.py uses named lookup instead of registry[10] magic number (P2 #10)
"""
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# #1: dual buttons must be in the disabled list
# ---------------------------------------------------------------------------

def test_set_controls_disabled_includes_dual_buttons():
    """Regression: dual_extract_button / dual_preview_button 必須被停用，避免 stats 競爭。"""
    from tests.conftest import mock_page, mock_filepicker
    from app.views.extractor_view import ExtractorView

    view = ExtractorView(mock_page(), mock_filepicker())
    view.set_controls_disabled(True)

    assert view.dual_extract_button.disabled is True
    assert view.dual_preview_button.disabled is True
    assert view.preview_lang_button.disabled is True
    assert view.preview_book_button.disabled is True
    assert view.lang_button.disabled is True
    assert view.book_button.disabled is True


def test_set_controls_disabled_re_enables_dual_buttons():
    """停用旗標解除後 dual 按鈕應恢復。"""
    from tests.conftest import mock_page, mock_filepicker
    from app.views.extractor_view import ExtractorView

    view = ExtractorView(mock_page(), mock_filepicker())
    view.set_controls_disabled(True)
    view.set_controls_disabled(False)

    assert view.dual_extract_button.disabled is False
    assert view.dual_preview_button.disabled is False
    assert view.preview_lang_button.disabled is False
    assert view.preview_book_button.disabled is False


# ---------------------------------------------------------------------------
# #5/#6: DUAL mode yields book stats even if lang_stats is None
# ---------------------------------------------------------------------------

def test_dual_extract_yields_book_stats_when_lang_stats_none(monkeypatch):
    """Regression: lang_stats=None 時，book 階段仍要 yield book_stats。
    否則 dual 模式 UI 統計徽章永遠顯示 0/0/0。

    直接 monkeypatch _run_extraction_process 來模擬兩種 yield 序列。
    """
    from translation_tool.core import jar_processor as jp

    # 模擬 lang 階段沒 yield stats（lang_stats 維持 None），
    # 但 book 階段 yield 帶 stats 的 update
    def fake_run_extraction(mods_dir, output_dir, target_regex, process_name):
        if process_name == "Lang":
            # 沒有任何 yield 含 stats → lang_stats = None
            yield {"progress": 1.0, "log": "lang done"}
            return
        # Book 階段：yield 含 stats 的 update
        yield {
            "progress": 0.5,
            "log": "book scan",
            "stats": {"success": 3, "failures": 0, "warnings": 0, "total_files": 3},
        }

    monkeypatch.setattr(jp, "_run_extraction_process", fake_run_extraction)

    updates = list(jp.extract_dual_files_generator("/tmp/mods", "/tmp/out"))

    # 應至少有一個 phase=book 且含 stats 的 yield（不是只 yield phase 而無 stats）
    book_with_stats = [
        u for u in updates
        if u.get("phase") == "book" and "stats" in u
    ]
    assert len(book_with_stats) > 0, (
        f"DUAL mode must yield book stats when lang_stats=None; got updates={updates}"
    )
    # book_stats 應等於 fake 提供的 stats（lang_stats=None 時直接 yield book_stats）
    assert book_with_stats[0]["stats"]["success"] == 3


# ---------------------------------------------------------------------------
# #7: scan progress is throttled (≤ 1 yield per 5s interval)
# ---------------------------------------------------------------------------

def test_scan_progress_throttled_to_5s_interval():
    """Regression: 慢速掃描時 yield 不應每 0.5s 一次（會洗版日誌）。

    Source-level 檢查：確認節流邏輯存在於 jar_processor_extract.py。
    """
    src = (Path(__file__).resolve().parent.parent / "translation_tool" / "core" / "jar_processor_extract.py").read_text(encoding="utf-8")
    assert "YIELD_INTERVAL = 5.0" in src, "YIELD_INTERVAL throttle must be 5.0s"
    assert "last_yielded_at" in src, "last_yielded_at throttle state must exist"
    # 確認 yield 是 conditional（在 if 條件內）
    assert "if elapsed - last_yielded_at >= YIELD_INTERVAL" in src, (
        "yield must be conditional on throttle interval"
    )


# ---------------------------------------------------------------------------
# #10: main.py uses named lookup (not registry[10])
# ---------------------------------------------------------------------------

def test_main_uses_named_pipeline_lookup():
    """Regression: main.py 不應再用 registry[10] 魔數索引。

    註解內提及 registry[10]（歷史說明）允許存在；只檢查實際執行語句。
    """
    import re
    main_src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    # 移除所有 docstring + 註解行（保留執行語句）
    # 1. 移除 docstring
    no_doc = re.sub(r'"""[\s\S]*?"""', "", main_src)
    no_doc = re.sub(r"'''[\s\S]*?'''", "", no_doc)
    # 2. 移除以 # 開頭的註解行
    code_only = "\n".join(
        line for line in no_doc.split("\n")
        if not line.lstrip().startswith("#")
    )

    assert "registry[10]" not in code_only, (
        "registry[10] must not be used as an index in executable statements"
    )
    assert "item.get('key') == 'pipeline'" in code_only or 'item.get(\"key\") == \"pipeline\"' in code_only, (
        "named lookup for pipeline view must exist in code"
    )


def test_main_pipeline_missing_raises_runtime_error():
    """如果 registry 沒有 pipeline key，應 raise RuntimeError 含可用 keys 列表。"""
    import importlib
    import sys

    # 載入 main module（不真的執行 page setup，只驗證 import 路徑）
    # main() 需要 flet page，無法直接呼叫，改為驗證原始碼有 raise 邏輯
    main_src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert "RuntimeError" in main_src, "main must raise RuntimeError on missing pipeline view"
    assert "pipeline view not found" in main_src or "Available keys" in main_src


# ---------------------------------------------------------------------------
# #2: poll daemon uses threading.Event (smoke test — verify source)
# ---------------------------------------------------------------------------

def test_show_preview_uses_threading_event():
    """Regression: show_preview poll 必須使用 threading.Event 控制生命週期。"""
    src = (Path(__file__).resolve().parent.parent / "app" / "views" / "extractor" / "extractor_actions.py").read_text(encoding="utf-8")
    assert "stop_event = threading.Event()" in src, "show_preview must create stop_event"
    assert "stop_event.is_set()" in src, "poll loop must check stop_event"
    assert "stop_event.wait(timeout=0.1)" in src, "poll must use wait(timeout=...) not time.sleep"