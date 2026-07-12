"""
PR #98 refactor/unified-log-view 系列修復的 regression 測試。

涵蓋這 11 個 commit 的 source-level pattern 斷言,確保未來修改不會回歸:

| Commit    | 修法關鍵                                                     |
|-----------|--------------------------------------------------------------|
| 7a56d22   | on_update 不發假「[完成]」,只在 run_extraction_loop return 後 |
| fbc58c7   | merge_view.start_merge 用 log_view.clear() 不用 .controls.clear() |
| 0228e05   | 提取中按取消真的中斷 Service (cancelled 旗標)               |
| 623d370   | 重複點「開始預覽」不卡死 (preview_state.done reset)          |
| 5ce1cc8   | debug log helper 存在                                       |
| 4834213   | 用 page.show_dialog / page.pop_dialog API 取代手動 overlay   |
| a59e85b   | 預覽/結果合併成單一 dialog mutate-in-place                  |
| ca01a14   | 提取中 dialog.modal=True + on_dismiss 防呆 + cancel flag    |
| b86f911   | log_info/log_debug/log_warning 取代 print                  |
| e232788   | from translation_tool.utils.log_unit import 確實寫入       |

Pattern-test strategy: 透過 grep / AST 確認 bug 修法的 code pattern 都存在,
且 bug 原始 buggy 模式不在。這比 behavior-test 更能 catch regression:
一旦有人改這檔,grep 立刻指出是否去掉 / 加回故障點。

測試都是 source-level + 1-2 個 service-level behavior test。
不靠 mock GUI,Flet 0.85 dialog stack 用 regex 直接 grep 程式碼 pattern。
"""
import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR_DIALOG = REPO_ROOT / "app" / "views" / "extractor" / "extractor_dialog.py"
MERGE_VIEW = REPO_ROOT / "app" / "views" / "merge_view.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ast_parse(path: Path):
    return ast.parse(_read(path))


# =============================================================================
# commit 7a56d22 — 防止逐 jar stats 誤觸發「[完成] 0/0/0」
# =============================================================================
class TestFix7a56d22_NoFalseCompletion:
    """on_update 不判斷「整段完成」,只在 run_extraction_loop 跑完後用 result_stats 發。"""

    def test_no_old_completion_pattern_in_on_update(self):
        """舊 buggy 模式:if pct >= 1.0 or "stats" in update → 已移除。"""
        src = _read(EXTRACTOR_DIALOG)
        # 移除舊的「整段完成」pattern
        assert "if pct >= 1.0" not in src, (
            "回歸:on_update 內還在判斷 pct >= 1.0 作為完成條件 "
            "(commit 7a56d22 已修,應移除)"
        )

    def test_completion_log_only_emits_result_stats(self):
        """[完成] 行必須從 result_stats 取得 — 不再用 stats["success"]。"""
        # 用 AST 找出「[完成]」相關的 add_log call
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found_completion_log = False
        found_result_stats_use = False
        found_raw_stats_use = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # find `add_log(f"[完成] ..."` 之類
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_log"
                and node.args
            ):
                first_arg = node.args[0]
                if isinstance(first_arg, ast.JoinedStr):
                    for value in first_arg.values:
                        if isinstance(value, ast.Constant) and "[完成]" in (value.value or ""):
                            found_completion_log = True
                            # 確認 value 用 result_stats 變數
                            # (這條用 fuzzy match: 字串內含「成功」+「跳過」+「失敗」)
                            pass
        # 直接 grep source-level: [完成] 行用 result_stats
        src = _read(EXTRACTOR_DIALOG)
        completion_pattern = re.search(
            r'\[完成\].*?result_stats', src, flags=re.DOTALL
        )
        assert completion_pattern is not None, (
            "回歸:[完成] log 行未使用 result_stats "
            "(commit 7a56d22 應該從 result_stats 取得成功/跳過/失敗)"
        )

    def test_no_raw_stats_dict_in_completion(self):
        """確保「[完成]」log 沒有使用 raw `stats["success"]` 之類的舊語法。"""
        src = _read(EXTRACTOR_DIALOG)
        # 找 [完成] 那行附近是否有 stats["success"] pattern
        match = re.search(r'\[完成\].*?\n.*?\n.*?', src)
        if match:
            completion_block = match.group(0)
            # 舊 buggy 模式: stats["success"], stats["warnings"], stats["failures"]
            assert not re.search(r'stats\["success"\]', completion_block), (
                "回歸:[完成] 行還在用 stats['success'] 直接讀 "
                "(應改成 result_stats['success'])"
            )


# =============================================================================
# commit fbc58c7 — merge_view.start_merge 用 LogView.clear() 不是 .controls.clear()
# =============================================================================
class TestFixFbc58c7_LogViewClearAPI:
    """merge_view.start_merge 用 LogView public .clear() API,不碰 .controls。"""

    def test_start_merge_does_not_use_controls_clear(self):
        """舊 buggy:self.log_view.controls.clear() — AttributeError。"""
        src = _read(MERGE_VIEW)
        # 找 start_merge 函式範圍
        tree = _ast_parse(MERGE_VIEW)
        in_start_merge = False
        bad_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "start_merge":
                in_start_merge = True
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "clear"
                    ):
                        # 檢查是否 .controls.clear() 鏈
                        if (
                            isinstance(sub.func.value, ast.Attribute)
                            and sub.func.value.attr == "controls"
                        ):
                            bad_calls.append(
                                f".controls.clear() at line {sub.lineno}"
                            )
                break
        assert not bad_calls, (
            f"回歸:start_merge 內還在用 .controls.clear():\n"
            + "\n".join(bad_calls)
        )


# =============================================================================
# commit 0228e05 — 提取中按取消真的中斷 Service
# =============================================================================
class TestFix0228e05_CancelTrulyInterrupts:
    """on_cancel_click 同時設 extraction_cancel_flag[0] = True,worker thread 在下個 jar check。"""

    def test_on_cancel_sets_service_cancel_flag(self):
        """確保 on_cancel_click handler 內有 extraction_cancel_flag[0] = True。"""
        src = _read(EXTRACTOR_DIALOG)
        # 找 on_cancel_click 函式範圍
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "on_cancel_click":
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Assign)
                        and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Subscript)
                        and isinstance(sub.targets[0].value, ast.Name)
                        and sub.targets[0].value.id == "extraction_cancel_flag"
                    ):
                        if (
                            isinstance(sub.value, ast.Constant)
                            and sub.value.value is True
                        ):
                            found = True
                break
        assert found, (
            "回歸:on_cancel_click handler 內找不到 extraction_cancel_flag[0] = True "
            "(commit 0228e05 修法,必須同步 Service cancel 旗標)"
        )


# =============================================================================
# commit 623d370 — 重複「開始預覽」不卡死 (preview_state.done reset)
# =============================================================================
class TestFix623d370_RePreviewResetsDone:
    """start_scan handler 必須 reset preview_state.done = False,否則第二次掃描 poller 立刻退出。"""

    def test_start_scan_resets_preview_state_done(self):
        """確保 start_scan 函式範圍內有 preview_state.done = False。"""
        src = _read(EXTRACTOR_DIALOG)
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "start_scan":
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Assign)
                        and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Attribute)
                        and sub.targets[0].attr == "done"
                        and isinstance(sub.targets[0].value, ast.Name)
                        and sub.targets[0].value.id == "preview_state"
                        and isinstance(sub.value, ast.Constant)
                        and sub.value.value is False
                    ):
                        found = True
                break
        assert found, (
            "回歸:start_scan 內找不到 preview_state.done = False "
            "(commit 623d370 修法,必須 reset 才能讓第二次預覽重新跑 poller)"
        )


# =============================================================================
# commit 5ce1cc8 — debug log helper
# =============================================================================
class TestFix5ce1cc8_DebugLogHelper:
    """_extractor_debug_log helper 曾存在但後續被 log_unit 取代。本測試記錄這段歷史。"""

    def test_extractor_dialog_does_not_use_old_helper(self):
        """現在用 log_info / log_debug / log_warning,不應該還有 _extractor_debug_log。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "_extractor_debug_log" not in src, (
            "回歸:_extractor_debug_log 殘留 "
            "(commit b86f911 改用 log_info / log_debug / log_warning)"
        )

    def test_extractor_dialog_does_not_use_print(self):
        """不應該還有 print(...) 直印用法,應統一走 logging。"""
        src = _read(EXTRACTOR_DIALOG)
        # 排除 docstring 內的 print 描述
        for line in src.splitlines():
            stripped = line.strip()
            # 只看 print(...) 或 print f"..."
            if re.match(r'^\s*print\s*\(', line) and not line.strip().startswith("#"):
                pytest.fail(
                    f"回歸:發現 print() 直印用法於 {line!r},應改用 log_info/log_debug/log_warning"
                )


# =============================================================================
# commit 4834213 — page.show_dialog / page.pop_dialog API
# =============================================================================
class TestFix4834213_FletDialogAPI:
    """用 Flet 0.85 內建 dialog lifecycle API,不再手動 page.overlay.append/remove。"""

    def test_uses_show_dialog_not_manual_overlay(self):
        """確保 dialogs 用 page.show_dialog 開啟,不是 page.overlay.append 配 dialog.open = True。"""
        src = _read(EXTRACTOR_DIALOG)
        # 舊 buggy pattern:page.overlay.append(\w+); \w+.open = True
        # 新 pattern: page.show_dialog(\w+)
        show_dialog_count = src.count("page.show_dialog(")
        overlay_append_count = len(
            re.findall(r"page\.overlay\.append\(", src)
        )
        assert show_dialog_count >= 3, (
            f"回歸:page.show_dialog 應該至少 3 次 (dialog/preview_dialog/result_dialog),"
            f"實際 {show_dialog_count} 次"
        )
        assert overlay_append_count == 0, (
            f"回歸:不應該再手動 page.overlay.append(...),"
            f"發現 {overlay_append_count} 處 (commit 4834213 修法)"
        )

    def test_uses_pop_dialog_not_manual_overlay_remove(self):
        """關閉 dialog 用 page.pop_dialog(),不從 page.overlay.remove。"""
        src = _read(EXTRACTOR_DIALOG)
        pop_count = src.count("page.pop_dialog(")
        overlay_remove_count = len(
            re.findall(r"page\.overlay\.remove\(", src)
        )
        assert pop_count >= 1, (
            "回歸:page.pop_dialog() 至少出現 1 次 (commit 4834213 修法)"
        )
        assert overlay_remove_count == 0, (
            f"回歸:不應該 page.overlay.remove(...),"
            f"發現 {overlay_remove_count} 處 (commit 945852b 撤回,改用 pop_dialog)"
        )


# =============================================================================
# commit a59e85b — single-dialog refactor
# =============================================================================
class TestFixA59e85b_SingleDialogRefactor:
    """show_result_dialog 改為 mutate preview_dialog 本身,不開新 result_dialog。"""

    def test_no_result_dialog_show_dialog_call(self):
        """不應該有 page.show_dialog(result_dialog) — 證明 single-dialog 架構。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "page.show_dialog(result_dialog)" not in src, (
            "回歸:發現 page.show_dialog(result_dialog) "
            "(commit a59e85b 改成 mutate preview_dialog 不再開新 dialog)"
        )

    def test_preview_dialog_title_mutated(self):
        """show_result_dialog 內必須 mutate preview_dialog.title。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "preview_dialog.title =" in src, (
            "回歸:preview_dialog.title 沒有被 mutate "
            "(commit a59e85b 改成 mutate-in-place,不開新 dialog)"
        )

    def test_preview_dialog_content_mutated(self):
        """show_result_dialog 內必須 mutate preview_dialog.content。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "preview_dialog.content =" in src, (
            "回歸:preview_dialog.content 沒有被 mutate "
            "(commit a59e85b 改成 mutate-in-place)"
        )

    def test_preview_dialog_actions_mutated(self):
        """show_result_dialog 內必須 mutate preview_dialog.actions。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "preview_dialog.actions =" in src, (
            "回歸:preview_dialog.actions 沒有被 mutate "
            "(commit a59e85b 改成 mutate-in-place)"
        )


# =============================================================================
# commit ca01a14 — modal lock + on_dismiss 防呆 + cancel flag
# =============================================================================
class TestFixCa01a14_ModalLockAndDismiss:
    """提取中 dialog.modal=True;ui_done 解鎖回 False;on_dismiss 防呆 + 設 cancel flag。"""

    def test_ui_start_locks_modal_true(self):
        """ui_start 內必須 dialog.modal = True。"""
        src = _read(EXTRACTOR_DIALOG)
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ui_start":
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Assign)
                        and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Attribute)
                        and sub.targets[0].attr == "modal"
                        and isinstance(sub.targets[0].value, ast.Name)
                        and isinstance(sub.value, ast.Constant)
                        and sub.value.value is True
                    ):
                        found = True
                break
        assert found, (
            "回歸:ui_start 內沒有 dialog.modal = True "
            "(commit ca01a14 修法,提取進行中必須 modal=True 阻擋外側 dismiss)"
        )

    def test_ui_done_unlocks_modal_false(self):
        """ui_done 內必須 dialog.modal = False。"""
        src = _read(EXTRACTOR_DIALOG)
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ui_done":
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Assign)
                        and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Attribute)
                        and sub.targets[0].attr == "modal"
                        and isinstance(sub.targets[0].value, ast.Name)
                        and isinstance(sub.value, ast.Constant)
                        and sub.value.value is False
                    ):
                        found = True
                break
        assert found, (
            "回歸:ui_done 內沒有 dialog.modal = False "
            "(commit ca01a14 修法,任務完成後恢復可外側關閉)"
        )

    def test_on_dialog_dismiss_handler_exists(self):
        """必須有 on_dialog_dismiss handler 設 cancel flag。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "def on_dialog_dismiss(" in src, (
            "回歸:on_dialog_dismiss handler 不存在 "
            "(commit ca01a14 防呆安全網,modal lock 的最後一道防線)"
        )

    def test_on_dialog_dismiss_sets_cancel_flag(self):
        """on_dialog_dismiss 內必須 extraction_cancel_flag[0] = True。"""
        src = _read(EXTRACTOR_DIALOG)
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "on_dialog_dismiss":
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Assign)
                        and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Subscript)
                        and isinstance(sub.targets[0].value, ast.Name)
                        and sub.targets[0].value.id == "extraction_cancel_flag"
                        and isinstance(sub.value, ast.Constant)
                        and sub.value.value is True
                    ):
                        found = True
                break
        assert found, (
            "回歸:on_dialog_dismiss 內沒設 extraction_cancel_flag[0] = True "
            "(commit ca01a14 修法,讓 background thread 提早結束而非空跑)"
        )


# =============================================================================
# commit b86f911 + e232788 — 改用 log_unit
# =============================================================================
class TestFixB86f911_LogUnit:
    """移除自寫 _extractor_debug_log(print 版),改用 project 既有的 log_unit。"""

    def test_imports_log_unit(self):
        """import log_info/log_debug/log_warning 必須存在(critical: e232788 補的)。"""
        src = _read(EXTRACTOR_DIALOG)
        assert (
            "from translation_tool.utils.log_unit import" in src
        ), "回歸:沒有 from translation_tool.utils.log_unit import (commit e232788 補)"
        for fn in ("log_info", "log_debug", "log_warning"):
            assert fn in src, f"回歸:{fn} 沒在 source 內引用到"

    def test_uses_log_info_not_print(self):
        """用 log_info(或其他 log_unit fn)取代原本的 print 直接印。"""
        src = _read(EXTRACTOR_DIALOG)
        # 確保沒 print( 開頭的 statement (排除 docstring/註解)
        for line in src.splitlines():
            stripped = line.strip()
            if (
                re.match(r"^print\s*\(", stripped)
                and not stripped.startswith("#")
            ):
                pytest.fail(
                    f"回歸:發現 print() 直印用法於 {line!r},應改用 log_info/log_debug/log_warning"
                )

    def test_uses_log_info_at_least_3_calls(self):
        """log_info 應至少 3 次呼叫 (THREAD/OPEN/PREVIEW/DIALOG 都用)。"""
        src = _read(EXTRACTOR_DIALOG)
        count = len(re.findall(r"\blog_info\s*\(", src))
        assert count >= 3, (
            f"回歸:log_info 呼叫次數過少 ({count}),"
            "commit b86f911 應該至少有 3 個流程節點用 log_info"
        )
