"""Tests for audit fixes (2026-06-28):
- dual buttons are disabled during extraction (P0 #1)
- poll daemon respects stop_event (P0 #2)
- DUAL mode yields book stats even when lang_stats is None (P1 #5/#6)
- jar_processor_extract.py throttles scan progress yields (P1 #7)
- main.py uses named lookup instead of registry[10] magic number (P2 #10)
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# #1: dual buttons must be in the disabled list
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
    book_with_stats = [u for u in updates if u.get("phase") == "book" and "stats" in u]
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
    src = (
        Path(__file__).resolve().parent.parent
        / "translation_tool"
        / "core"
        / "jar_processor_extract.py"
    ).read_text(encoding="utf-8")
    assert "YIELD_INTERVAL = 5.0" in src, "YIELD_INTERVAL throttle must be 5.0s"
    assert "last_yielded_at" in src, "last_yielded_at throttle state must exist"
    # 確認 yield 是 conditional（在 if 條件內）
    assert "if elapsed - last_yielded_at >= YIELD_INTERVAL" in src, (
        "yield must be conditional on throttle interval"
    )


# ---------------------------------------------------------------------------
# #10: 外殼用具名 key 查頁面（不是 registry[10] 這種魔數索引）
# ---------------------------------------------------------------------------


def test_shell_uses_named_view_lookup():
    """Regression: 不可用 registry[<數字>] 當索引；頁面一律用 key 查（view_registry.index_of）。"""
    import re

    root = Path(__file__).resolve().parent.parent
    for rel in ("main.py", "app/shell/app_shell.py"):
        src = (root / rel).read_text(encoding="utf-8")
        no_doc = re.sub(r'"""[\s\S]*?"""', "", src)
        code_only = "\n".join(
            line for line in no_doc.split("\n") if not line.lstrip().startswith("#")
        )
        assert not re.search(r"registry\[\d+\]", code_only), (
            f"{rel}: registry[<數字>] must not be used as an index"
        )


def test_default_view_key_exists_in_registry():
    """預設首頁的 key 寫錯會讓啟動直接失敗：以測試取代原本 main.py 的執行期檢查。"""
    from app.view_registry import DEFAULT_VIEW_KEY, VIEW_SPECS

    assert DEFAULT_VIEW_KEY in {spec.key for spec in VIEW_SPECS}
