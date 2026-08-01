"""Regression tests for extractor_dialog 的「逐 jar 誤觸發完成」 bug。

背景:
    PR refactor/extractor-view-architecture (PR #97) 之前,extractor_dialog.py 的
    on_update callback 內有這段:

        if pct >= 1.0 or "stats" in update:
            state["done"] = True
            add_log(f"[完成] 成功 {stats['success']} / 跳過 {stats['warnings']} / ...")
            update_progress(1.0, "任務完成")
            update_stats(stats["success"], stats["warnings"], ...)

    但 Generator (translation_tool.core.jar_processor.extract_*_files_generator) 在
    「逐 jar 完成」時就會 yield:
        {"log": "[393/393] xxx.jar", "stats": {success: 0, warnings: 0, failures: 0}}
    而「整段任務完成」時也會 yield 一個最終 stats:
        {"stats": {success: 3129, warnings: 2, failures: 0}, "phase": "lang"}

    兩種 yield 都帶 "stats" key。舊的 on_update 用 `pct >= 1.0 or "stats" in update`
    判斷,造成逐 jar 誤觸發「[完成] 成功 0 / 跳過 0 / 失敗 0」假訊息,
    而真正的彙總 (3129 / 2 / 0) 要等到 run_extraction_loop 結束後才會被發出去。

修法:
    1. 移除 on_update 內的「[完成] 假完成行 / 假 update_stats」
    2. 「[完成] 成功 X / 跳過 Y / 失敗 Z」只發一次,放在 run_extraction_loop() return
       之後,用 Service 回傳的 result_stats (整段累計)。
    3. except branch 也需要 state["done"] = True + update_stats(0, 0, 1),
       因為 run_extraction_loop 拋例外時 result_stats 不存在。

這些測試:
    - 程式碼層驗證: 確認 bug pattern 不再出現
    - 行為層驗證: 透過 run_extraction_loop 模擬 dual-mode scenario,驗證 Service
      回傳的 stats 等於最終累計 (不會被中繼 stats 覆寫)。

注意:
    extractor_dialog.py 的 run_extraction 是 closure,無法直接呼叫進行整合測試,
    因此「行為層」測試以 Service (run_extraction_loop) 為切入點,確保 Service 端的
    累計語義正確 (即對話框若用 result_stats 印最終行,數字就會對)。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# =============================================================================
# 程式碼層驗證 — 確認 bug pattern 已移除
# =============================================================================

# 從這個 test 檔往回找 extractor_dialog.py 的相對路徑
_DIALOG_PATH = (
    Path(__file__).parent.parent
    / "app"
    / "views"
    / "extractor"
    / "extractor_dialog.py"
)


class TestBugPatternRemoved:
    """確認 [完成] 完成邏輯不再依賴 on_update 內部判斷。"""

    def _read_dialog_source(self) -> str:
        assert _DIALOG_PATH.exists(), f"extractor_dialog.py 不存在於 {_DIALOG_PATH}"
        return _DIALOG_PATH.read_text(encoding="utf-8")

    def _read_dialog_code(self) -> str:
        """只回傳「程式碼行」,移除所有 docstring / 區塊註解 / 行內註解,避免誤判。"""
        import ast
        source = self._read_dialog_source()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        mask_lines = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    docstring_node = node.body[0]
                    for ln in range(docstring_node.lineno, docstring_node.end_lineno + 1):
                        mask_lines.add(ln)

        out = []
        for i, line in enumerate(source.splitlines(), start=1):
            if i in mask_lines:
                continue
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            out.append(line)
        return "\n".join(out)

    def test_false_completion_pattern_is_gone(self):
        """不再出現 `pct >= 1.0 or "stats" in update` 這個誤觸發條件。"""
        code = self._read_dialog_code()
        assert 'pct >= 1.0 or "stats" in update' not in code, (
            "extractor_dialog.py 的 on_update callback 還有誤觸發 pattern,逐 jar 完成會再次出現 0/0/0。"
            "應將「[完成] 成功 X/跳過 Y/失敗 Z」統一移到 run_extraction_loop() 返回後。"
        )

    def test_completion_line_uses_result_stats_not_local_stats(self):
        """[完成] 成功 X / 跳過 Y / 失敗 Z 行必須從 result_stats 讀,不能從 dialog 本地 stats 讀。

        注意:此測試只在意「統計完成行」(成功/跳過/失敗 三個關鍵字同時出現)，
        不在意其他無關的 [完成] add_log (例如「[完成] 找到 N 筆結果」預覽行)。
        """
        code = self._read_dialog_code()
        pattern = re.compile(
            r'add_log\(\s*\n?\s*f(?:\"|\\")?\[完成\][^)]*?(?:成功|跳過|失敗)[^)]*?(?:成功|跳過|失敗)[^)]*?(?:成功|跳過|失敗)[^)]*\)',
            re.DOTALL,
        )
        completion_lines = pattern.findall(code)
        assert completion_lines, (
            "extractor_dialog.py 沒有任何「[完成] 成功 X / 跳過 Y / 失敗 Z」統計 add_log 行,"
            "請確認 fix 還在。"
        )
        for line in completion_lines:
            assert ("result_stats['success']" in line
                    or 'result_stats["success"]' in line), (
                "❌ 「[完成] 成功 X/跳過 Y/失敗 Z」行讀的是 dialog 本地的 stats dict (永遠是 0/0/0),\n"
                "應改成 Service 回傳的 result_stats['success']。\n"
                f"實際行:{line[:400]}"
            )
            assert not (re.search(r'(?<!result_)(?<![a-zA-Z_])stats\[[\'\"]success[\'\"]\]', line)), (
                "❌ 「[完成] 成功 X」行含有裸的 stats['success'] (非 result_stats),"
                "這代表又在讀永遠 0/0/0 的本地 dict。\n"
                f"實際行:{line[:400]}"
            )

    def test_init_stats_dict_is_removed(self):
        """對話框不應再保留永遠 0/0/0 的裸 stats = {"success": 0, ...} 初始化。

        允許 state["stats"] = {...} (那是給外部 callback on_complete 讀的);
        但裸的 `stats = {"success": 0, ...}` 必須被移除。
        """
        code = self._read_dialog_code()
        assert not re.search(
            r'^        stats = \{"success":\s*0',
            code,
            flags=re.MULTILINE,
        ), "extractor_dialog.py 還保留裸 stats 初始化,但此 dict 從未被正確累加。"

    def test_completion_invoked_exactly_once_in_run_extraction(self):
        """[完成] add_log 與 update_stats(整段累計) 應同時出現在 try block 結尾後。"""
        code = self._read_dialog_code()
        pattern = re.compile(
            r'add_log\(\s*\n?\s*f(?:\"|\\")?\[完成\][^)]*?(?:成功|跳過|失敗)[^)]*?(?:成功|跳過|失敗)[^)]*?(?:成功|跳過|失敗)[^)]*\)',
            re.DOTALL,
        )
        matches = list(pattern.finditer(code))
        assert matches, "找不到 [完成] 統計行"

        m = matches[0]
        line = m.group(0)
        assert "result_stats" in line, (
            "[完成] 統計行沒有 result_stats"
        )

        after_window = code[m.end(): m.end() + 800]
        assert (
            "update_stats(result_stats['success']" in after_window
            or 'update_stats(result_stats["success"]' in after_window
        ), (
            "[完成] 行後必須緊接 update_stats(result_stats[...], ...) 用最終累計更新 UI 統計徽章"
        )


# =============================================================================
# 行為層驗證 — Service 累計語義
# =============================================================================

class TestServiceAccumulatesFinalStats:
    """Service run_extraction_loop 應正確累加,中繼 stats 不會讓最終累計失真。"""

    def test_intermediate_stats_overwrite_run_extraction_loop_returns_last_seen(self):
        """模擬逐 jar yield 中繼 stats 的場景。

        Generator yield 順序:
          1. mid-jar stats: {success: 0, warnings: 0, failures: 0}  ← 逐 jar
          2. final stats:   {success: 3129, warnings: 2, failures: 0}  ← 整段結束

        run_extraction_loop 內部會把 stats dict 覆寫成每次 yield 的值 (NOT 累加),
        所以最終回傳等於「最後一次 yield 的 stats」,這正是 extractor_dialog 需要的
        「整段累計」語義。

        這個測試確保 extractor_dialog 修法跟 Service 的累計語義對齊:
            result_stats = run_extraction_loop(...)
            add_log(f"[完成] {result_stats['success']}...")  ← 用最後一次覆寫後的值
        """
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        def gen():
            yield {"progress": 0.5, "log": "[1/2] first.jar", "stats": {"success": 0, "warnings": 0, "failures": 0}}
            yield {"progress": 1.0, "log": "[2/2] last.jar",  "stats": {"success": 0, "warnings": 0, "failures": 0}}
            yield {"phase": "lang", "stats": {"success": 3129, "warnings": 2, "failures": 0}}

        received_per_call = []

        def on_update(update):
            received_per_call.append(update.get("stats"))

        stats = run_extraction_loop(gen(), on_update=on_update)

        assert len(received_per_call) == 3
        assert received_per_call[0] == {"success": 0, "warnings": 0, "failures": 0}
        assert received_per_call[1] == {"success": 0, "warnings": 0, "failures": 0}
        assert received_per_call[2] == {"success": 3129, "warnings": 2, "failures": 0}
        # Phase 3 (2026-07-13) user 選項 B: lang/book sub-dict 加入 stats
        assert stats["success"] == 3129
        assert stats["warnings"] == 2
        assert stats["failures"] == 0
        # 此測試 yield "phase": "lang" line 200, 所以 lang sub-dict 應被填入
        assert stats["lang"] == {"success": 3129, "warnings": 2, "failures": 0}
        # 沒 yield "phase": "book",book sub-dict 維持 default
        assert stats["book"] == {"success": 0, "warnings": 0, "failures": 0}

    def test_dual_mode_two_phases_accumulate_correctly(self):
        """Dual 模式 (lang phase + book phase) 最終 stats 為兩 phase 合計。

        Generator (extract_dual_files_generator) 在 jar_processor.py 內部會
        把兩個 phase 的 stats 合計成 combined yield:

            yield {"phase": "lang", "stats": lang_stats}                  ← L-only
            yield {"phase": "book", "stats": lang_stats + book_stats}     ← combined

        run_extraction_loop 最終回傳的就是 combined (整段累計)。
        """
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        LANG_FINAL = {"success": 3000, "warnings": 2, "failures": 0}
        BOOK_FINAL = {"success": 129, "warnings": 0, "failures": 0}
        COMBINED = {
            "success": LANG_FINAL["success"] + BOOK_FINAL["success"],
            "warnings": LANG_FINAL["warnings"] + BOOK_FINAL["warnings"],
            "failures": LANG_FINAL["failures"] + BOOK_FINAL["failures"],
        }

        def gen():
            for i in range(2):
                yield {
                    "progress": (i + 1) / 3.0,
                    "log": f"jar {i}",
                    "stats": {"success": 0, "warnings": 0, "failures": 0},
                }
            yield {"phase": "lang", "stats": LANG_FINAL}
            yield {"phase": "book", "stats": COMBINED}

        stats = run_extraction_loop(gen())
        assert stats["success"] == 3129
        assert stats["warnings"] == 2
        assert stats["failures"] == 0
        # Phase 3 (2026-07-13) user 選項 B: DUAL mode 拆解 phase stats
        # lang sub-dict 應跟 LANG_FINAL 一致 (此測試 yield {"phase": "lang", "stats": LANG_FINAL})
        assert stats["lang"] == LANG_FINAL
        # book sub-dict 應等於 generator yield {"phase": "book", "stats": COMBINED} 內的 stats
        assert stats["book"] == COMBINED

    def test_empty_generator_returns_zero_stats(self):
        """Generator 一個 yield 都沒有 → 回傳 {0, 0, 0},不會讓 dialog 崩潰。"""
        from app.services_impl.pipelines.extract_service import run_extraction_loop

        stats = run_extraction_loop(iter([]))
        assert stats["success"] == 0
        assert stats["warnings"] == 0
        assert stats["failures"] == 0
        # Phase 3 (2026-07-13) sub-dict default
        assert stats["lang"] == {"success": 0, "warnings": 0, "failures": 0}
        assert stats["book"] == {"success": 0, "warnings": 0, "failures": 0}


# =============================================================================
# 文件路徑常量確認 — 不允許絕對路徑 (PR 教訓)
# =============================================================================

class TestPathConvention:
    """本檔案不應寫死 Windows 使用者目錄絕對路徑。"""

    def test_no_hardcoded_user_path(self):
        r"""確保測試不帶絕對路徑 (2026-06-29 明確糾正:「改成 相對路徑不能使用 絕對路徑」)。

        [作法] 用 AST 把模組 docstring、class docstring、function docstring 的行號遮罩掉,
        只檢查實際「程式碼行」是否含絕對路徑。
        """
        import re
        import ast
        text = Path(__file__).read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return

        mask_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    doc = node.body[0]
                    for ln in range(doc.lineno, doc.end_lineno + 1):
                        mask_lines.add(ln)

        forbidden_user_paths = [
            re.compile(r"[A-Za-z]:\\\\Users"),
            re.compile(r"/Users/[A-Za-z]"),
            re.compile(r"/home/[A-Za-z]"),
        ]

        code_lines = []
        for i, line in enumerate(text.splitlines(), start=1):
            if i in mask_lines:
                continue
            if line.lstrip().startswith("#"):
                continue
            code_lines.append(line)
        code_text = "\n".join(code_lines)

        for pattern in forbidden_user_paths:
            matches = pattern.findall(code_text)
            assert not matches, (
                f"測試碼不應寫死絕對使用者路徑 {pattern.pattern}。\n"
                f"命中:{matches[:3]}\n請改用 Path(__file__).parent / ..."
            )
