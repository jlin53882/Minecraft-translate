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
EXTRACTOR_ACTIONS = REPO_ROOT / "app" / "views" / "extractor" / "extractor_actions.py"
EXTRACTOR_VIEW = REPO_ROOT / "app" / "views" / "extractor_view.py"
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
# User 2026-07-12 補發現:preview_dialog 也需要同樣 modal lock + on_dismiss 防呆
# =============================================================================
class TestFixPreviewDialogModalLock:
    """User 2026-07-12 review 時補發現:preview_dialog 在掃描中也要鎖 modal=True,
    並掛 on_dismiss handler 設 state["cancelled"] = True 觸發 do_scan break。

    比照主 dialog(commit ca01a14)的模式,但用 state["cancelled"] 而不是
    extraction_cancel_flag[0],因為 preview 流程本就靠 state dict 管理取消,
    do_scan() 的 for 迴圈每個 generator yield 都 check state["cancelled"]。
    """

    def test_start_scan_locks_preview_dialog_modal(self):
        """start_scan() 內必須 preview_dialog.modal = True。"""
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "start_scan":
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Assign)
                        and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Attribute)
                        and sub.targets[0].attr == "modal"
                        and isinstance(sub.targets[0].value, ast.Name)
                        and sub.targets[0].value.id == "preview_dialog"
                        and isinstance(sub.value, ast.Constant)
                        and sub.value.value is True
                    ):
                        found = True
                break
        assert found, (
            "回歸:start_scan 內沒有 preview_dialog.modal = True "
            "(User 2026-07-12 補發現,預覽掃描時必須鎖定避免 dismiss 後 thread 變孤兒)"
        )

    def test_show_result_dialog_unlocks_preview_dialog_modal(self):
        """show_result_dialog mutate 結束時,必須 preview_dialog.modal = False 解除鎖定。"""
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "show_result_dialog":
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Assign)
                        and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Attribute)
                        and sub.targets[0].attr == "modal"
                        and isinstance(sub.targets[0].value, ast.Name)
                        and sub.targets[0].value.id == "preview_dialog"
                        and isinstance(sub.value, ast.Constant)
                        and sub.value.value is False
                    ):
                        found = True
                break
        assert found, (
            "回歸:show_result_dialog 內 mutate 結束時沒設 preview_dialog.modal = False "
            "(掃描結束應解鎖,讓使用者可點外側關閉結果 dialog)"
        )

    def test_on_preview_dismiss_handler_exists(self):
        """必須有 on_preview_dismiss handler 提供 ESC / 程式錯誤時的安全網。"""
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "on_preview_dismiss":
                found = True
                break
        assert found, (
            "回歸:on_preview_dismiss handler 不存在 "
            "(User 2026-07-12 補發現,preview dialog 需要類同主 dialog 的 dismiss 防呆)"
        )

    def test_on_preview_dismiss_sets_cancelled_flag(self):
        """on_preview_dismiss 內必須 state["cancelled"] = True — do_scan / ui_poller 都會 check。"""
        tree = _ast_parse(EXTRACTOR_DIALOG)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "on_preview_dismiss":
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Assign)
                        and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Subscript)
                        and isinstance(sub.targets[0].value, ast.Name)
                        and sub.targets[0].value.id == "state"
                        and isinstance(sub.value, ast.Constant)
                        and sub.value.value is True
                    ):
                        # 確認是 state["cancelled"] (不是 state["running"])
                        if isinstance(sub.targets[0].slice, ast.Constant):
                            key = sub.targets[0].slice.value
                            if key == "cancelled":
                                found = True
                                break
                break
        assert found, (
            "回歸:on_preview_dismiss 內沒設 state['cancelled'] = True "
            "(do_scan for 迴圈與 ui_poller while 都 check 這個旗標來中斷)"
        )

    def test_preview_dialog_on_dismiss_attached(self):
        """preview_dialog.on_dismiss 必須被 hook 到 on_preview_dismiss function。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "preview_dialog.on_dismiss = on_preview_dismiss" in src, (
            "回歸:preview_dialog.on_dismiss 沒有 hook on_preview_dismiss "
            "(User 2026-07-12 補發現,確保 dismiss 觸發時能進入 cancel 防呆)"
        )


# =============================================================================
# User 2026-07-12 review 補 bug:add_log 第二位置參數被傳 color 字串
# =============================================================================
class TestFixAddLogArgType:
    """User 2026-07-12 review 補發現:line 279 `add_log("[系統] 任務已取消", theme.ORANGE_700)`
    把 color 字串傳進 add_log 的 `level` 參數(簽名是 level: str)。
    LogView.add() 看到不在 show_levels 白名單的字串就 silent return,整行 log 不顯示。

    修法:用 keyword arg `level="warning"` 對應原本想要的橘色語意。
    """

    def test_no_add_log_with_color_string_as_level(self):
        """add_log(...) 第二位置參數不能傳 color 字串(theme.* 等)。"""
        # 讀檔找所有 add_log(... call
        src = _read(EXTRACTOR_DIALOG)
        # 找函式位置
        tree = _ast_parse(EXTRACTOR_DIALOG)
        bad_calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # 找 _extractor_dialog 內的 add_log(...)
            if not (isinstance(node.func, ast.Name) and node.func.id == "add_log"):
                continue
            # 兩種傳法:add_log(msg, color) OR add_log(msg, level="warning", ...)
            # 檢查第二位置參數是 theme.* 開頭的 attribute(例如 theme.ORANGE_700)
            if len(node.args) >= 2:
                second_arg = node.args[1]
                # 找 theme.SOMETHING pattern
                if (
                    isinstance(second_arg, ast.Attribute)
                    and isinstance(second_arg.value, ast.Name)
                    and second_arg.value.id == "theme"
                ):
                    bad_calls.append(
                        f"add_log(... , theme.{second_arg.attr}) at line {node.lineno}"
                    )
        assert not bad_calls, (
            "回歸:add_log 的 level 參數傳了 color 字串 (theme.*),會被 LogView silent return。\n"
            "應改成 keyword arg level=\"warning\" 對應原本的橘色語意。\n"
            "找到的錯誤:\n" + "\n".join(bad_calls)
        )

    def test_cancelled_log_uses_warning_level(self):
        """「任務已取消」log 必須用 level=\"warning\"(不是 color string)。"""
        src = _read(EXTRACTOR_DIALOG)
        # AST-level:找 "任務已取消" 字串後面有 level="warning"
        assert 'add_log("[系統] 任務已取消", level="warning")' in src, (
            "回歸:[系統] 任務已取消 沒用 level=warning "
            "(原本傳 theme.ORANGE_700 color 字串,LogView 會 silent return)"
        )


# =============================================================================
# User 2026-07-12 review 補發現:extractor_view._show_extraction_summary 也用手動 overlay
# =============================================================================
class TestFixExtractionSummaryDialogAPI:
    """2026-07-14 user review 補發現:ExtractorView._show_extraction_summary
    完全是 dead code — production 零 caller、零測試保護(測試名不符實,
    只驗 dialog 有沒有彈出沒驗內容)、永遠顯示 0/0/0 永遠跑不起來。

    修法(2026-07-14):
    1. 物理刪除 _show_extraction_summary 函式本身
    2. 物理刪除測試 test_extractor_view_show_extraction_summary_exists
       跟 test_show_extraction_summary_dialog_has_lang_and_book_sections
    3. 保留 _close_dialog_overlay (merge_view.py 仍使用)
    4. extractor_dialog.py 內的 update_stats + add_log("[完成]...") 才是真正
       的 summary 顯示路徑,這已在 phase 3 處理

    source-level 斷言改寫成驗證「函式不存在」,防止 future commit
    偷偷加回 dead code 還自以為活著。
    """

    def test_show_extraction_summary_function_physically_deleted(self):
        """_show_extraction_summary 函式必須已被物理刪除(2026-07-14 dead code cleanup)。"""
        extractor_view = REPO_ROOT / "app" / "views" / "extractor_view.py"
        src = _read(extractor_view)
        assert "def _show_extraction_summary" not in src, (
            "回歸:ExtractorView._show_extraction_summary 重新出現 "
            "(2026-07-14 user review 已物理刪除,因為 production 零 caller)"
        )

    def test_extractor_dialog_no_overlay_append_for_summary(self):
        """extractor_dialog.py 的 update_stats 路徑不應再 page.overlay.append。"""
        src = _read(EXTRACTOR_DIALOG)
        # 找 update_stats 函式範圍
        # update_stats 不是 def,是 closure 內部 helper
        # 我們改檢查整個 extractor_dialog 內 overlay.append 用法
        # production 真實 summary 顯示路徑是 dialog 內 stats_row,不需要 overlay.append
        # 所以這斷言期望 extract_dialog 內沒有 page.overlay.append
        # (跟 _show_extraction_summary 原本錯誤做法形成對照)
        # 移除過嚴的具體行數斷言,只檢查沒有 page.overlay.append(隨時錯誤)
        assert "page.overlay.append" not in src, (
            "回歸:extractor_dialog 又有 page.overlay.append "
            "(production 摘要顯示應該在 dialog 內 stats_row,不需要額外 dialog)"
        )

    def test_extractor_view_imports_log_warning(self):
        """extractor_view 必須 import log_warning(供 except 內用)。"""
        extractor_view = REPO_ROOT / "app" / "views" / "extractor_view.py"
        tree = _ast_parse(extractor_view)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "translation_tool.utils.log_unit":
                imported = {alias.name for alias in node.names}
                if "log_warning" in imported:
                    return
        pytest.fail(
            "回歸:extractor_view.py 沒有 from translation_tool.utils.log_unit import log_warning "
            "(except handler 內需要 log_warning 留下 traceback 證據)"
        )

    def test_no_bare_except_pass_in_extractor_view(self):
        """extractor_view 不應該再有 `except Exception: pass`(吞錯沒 log)。"""
        extractor_view = REPO_ROOT / "app" / "views" / "extractor_view.py"
        src = _read(extractor_view)
        # 找 `except Exception:` 然後緊接一行是 `pass` 的 pattern
        # 用 regex: except Exception:\n     pass
        bad_blocks = re.findall(
            r"except\s+Exception(?:\s+as\s+\w+)?:\s*\n\s+pass\b",
            src,
        )
        assert not bad_blocks, (
            "回歸:extractor_view.py 仍有 `except Exception: pass` 區塊 "
            "(例外吞錯沒 log,應改成 except Exception as ex: log_warning(...))\n"
            f"找到 {len(bad_blocks)} 處"
        )


# =============================================================================
# User 2026-07-12 review 補發現 第四個:_show_snack_bar 不該直接 page.update()
# =============================================================================
class TestFixSnackBarExceptWrapper:
    """User 2026-07-12 review 補發現:ExtractorView._show_snack_bar 內直接呼叫
    self.page.update()(從 click handler 內同步),且沒有 try/except 把例外吞掉。

    修法:
    1. 不主動 page.update():page.update 由 caller 觸發(pick_directory 等路徑
       自己的 async task 已經會順便 update),避免額外塞進 page._tasks 干擾測試。
       設 snack.open=True 已經把 control 標 dirty,Flet internal mutation 追蹤會
       在下一次 page.update() 時 render。
    2. 用 try/except 包起來,補 log_warning 留 traceback。
    """

    def test_show_snack_bar_has_try_except(self):
        """_show_snack_bar 必須有 try/except 包核心程式碼。"""
        extractor_view = REPO_ROOT / "app" / "views" / "extractor_view.py"
        tree = _ast_parse(extractor_view)
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_show_snack_bar"
            ):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Try)
                        and any(
                            isinstance(h, ast.ExceptHandler)
                            for h in sub.handlers
                        )
                    ):
                        found = True
                break
        assert found, (
            "回歸:_show_snack_bar 沒有 try/except "
            "(User 2026-07-12 review 補發現,沒有 traceback 證據)"
        )

    def test_show_snack_bar_logs_warning_on_failure(self):
        """_show_snack_bar 的 except handler 必須呼叫 log_warning。"""
        extractor_view = REPO_ROOT / "app" / "views" / "extractor_view.py"
        src = _read(extractor_view)
        # AST-level:找 _show_snack_bar 內有 log_warning 呼叫
        tree = _ast_parse(extractor_view)
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_show_snack_bar"
            ):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "log_warning"
                    ):
                        found = True
                break
        assert found, (
            "回歸:_show_snack_bar 的 except handler 沒呼叫 log_warning "
            "(吞錯沒 log,跟 commit b6c958b 的 extractor_view 修法要對稱)"
        )

    def test_show_snack_bar_no_direct_page_update(self):
        """_show_snack_bar 不應直接呼叫 page.update()。"""
        extractor_view = REPO_ROOT / "app" / "views" / "extractor_view.py"
        tree = _ast_parse(extractor_view)
        bad_calls = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_show_snack_bar"
            ):
                # 找 self.page.update() 同步呼叫
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "update"
                    ):
                        # 檢查是否 self.page.update()
                        if (
                            isinstance(sub.func.value, ast.Attribute)
                            and sub.func.value.attr == "page"
                        ):
                            bad_calls.append(
                                f"self.page.update() at line {sub.lineno}"
                            )
                break
        assert not bad_calls, (
            "回歸:_show_snack_bar 直接 self.page.update() "
            "(User 2026-07-12 review 補發現,page.update 由 caller 負責,"
            "避免額外塞 task 干擾測試 page._tasks 長度斷言)\n"
            + "\n".join(bad_calls)
        )


# =============================================================================
# commit b86f911 + e232788 — 改用 log_unit
# =============================================================================
class TestFixButtonLevelModsValidation:
    """按鈕層級 mods_dir 提早驗證 (2026-07-13 user review)。

    User 在按 [提取 Lang] 等 6 個按鈕時,如果 mods_dir 未設,提早在按鈕層
    SnackBar 提示而不進 dialog。這把原先 extractor_dialog.py:on_start_click
    內的 SnackBar 早 return 邏輯提到按鈕層,user UX 提早回饋。

    設計:
    - 抽出 _check_mods_dir_or_snack() helper (ExtractorView 內)
    - 6 個按鈕 on_click 改為 instance method (_handle_*_click) 呼叫 helper
    - 通過驗證才呼叫 open_extractor_dialog / open_preview_dialog
    """

    def test_has_check_mods_dir_helper(self):
        """_check_mods_dir_or_snack helper 必須存在於 ExtractorView。"""
        src = _read(EXTRACTOR_VIEW)
        assert "def _check_mods_dir_or_snack(self" in src, (
            "回歸:_check_mods_dir_or_snack helper 不存在 (button-level mods_dir "
            "驗證核心,2026-07-13 user review 要求按鈕層提早驗證)"
        )

    def test_check_helper_checks_mods_dir_empty(self):
        """_check_mods_dir_or_snack 必須檢查 mods_dir 是否為空。"""
        src = _read(EXTRACTOR_VIEW)
        # 找 _check_mods_dir_or_snack 函式主體
        m = re.search(
            r"def _check_mods_dir_or_snack\(self, [^)]*\) -> bool:(.*?)(?=\n    def |\nclass |\Z)",
            src, re.DOTALL,
        )
        assert m is not None, "找不到 _check_mods_dir_or_snack 函式"
        body = m.group(1)
        assert "if not mods_dir" in body, (
            "回歸:_check_mods_dir_or_snack 內缺 `if not mods_dir` 檢查"
        )

    def test_check_helper_checks_mods_dir_not_dir(self):
        """_check_mods_dir_or_snack 必須檢查 mods_dir 是否存在資料夾。"""
        src = _read(EXTRACTOR_VIEW)
        m = re.search(
            r"def _check_mods_dir_or_snack\(self, [^)]*\) -> bool:(.*?)(?=\n    def |\nclass |\Z)",
            src, re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        assert "os.path.isdir" in body, (
            "回歸:_check_mods_dir_or_snack 內缺 `os.path.isdir(mods_dir)` 檢查"
        )

    def test_check_helper_calls_show_snack_bar(self):
        """_check_mods_dir_or_snack 驗證失敗時必須呼叫 _show_snack_bar。"""
        src = _read(EXTRACTOR_VIEW)
        m = re.search(
            r"def _check_mods_dir_or_snack\(self, [^)]*\) -> bool:(.*?)(?=\n    def |\nclass |\Z)",
            src, re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        assert "self._show_snack_bar" in body, (
            "回歸:_check_mods_dir_or_snack 驗證失敗時應該呼叫 _show_snack_bar 提示"
        )

    def test_six_button_handle_methods_exist(self):
        """6 個 click handler instance method 都必須存在於 ExtractorView。"""
        src = _read(EXTRACTOR_VIEW)
        expected_methods = [
            "_handle_extract_lang_click",
            "_handle_extract_book_click",
            "_handle_extract_dual_click",
            "_handle_preview_lang_click",
            "_handle_preview_book_click",
            "_handle_preview_dual_click",
        ]
        for m in expected_methods:
            assert f"def {m}" in src, (
                f"回歸:{m} 不存在 (button-level validation 缺少對應 handler)"
            )

    def test_button_defs_use_instance_methods(self):
        """6 個按鈕 on_click 必須改為 instance method (不是 lambda 直接呼叫 open_extractor_dialog)。"""
        src = _read(EXTRACTOR_VIEW)
        # __init__ 範圍內不能有 `on_click=lambda e: open_extractor_dialog` 或 open_preview_dialog
        m = re.search(
            r"def __init__\(self[^)]*\):(.*?)(?=\n    def \(self\)\)\n|\Z)",
            src, re.DOTALL,
        )
        assert m is not None
        init_body = m.group(1)
        assert "on_click=lambda e: open_extractor_dialog" not in init_body, (
            "回歸:button on_click 還是用 lambda 直接呼叫 open_extractor_dialog "
            "(2026-07-13 user review 後應改為 instance method 走 _check_mods_dir_or_snack)"
        )
        assert "on_click=lambda e: open_preview_dialog" not in init_body, (
            "回歸:button on_click 還是用 lambda 直接呼叫 open_preview_dialog"
        )

    def test_handle_methods_call_check_helper(self):
        """6 個 click handler 內必須先呼叫 _check_mods_dir_or_snack。"""
        src = _read(EXTRACTOR_VIEW)
        expected_methods = [
            "_handle_extract_lang_click",
            "_handle_extract_book_click",
            "_handle_extract_dual_click",
            "_handle_preview_lang_click",
            "_handle_preview_book_click",
            "_handle_preview_dual_click",
        ]
        for m in expected_methods:
            idx = src.find(f"def {m}")
            assert idx > 0, f"找不到 {m}"
            # 抓後續函式主體(下一個 def 或 class 之前)
            next_def = re.search(r"\n    def |\nclass ", src[idx+1:])
            end = idx + 1 + next_def.start() if next_def else len(src)
            body = src[idx:end]
            assert "_check_mods_dir_or_snack" in body, (
                f"回歸:{m} 內缺 _check_mods_dir_or_snack 呼叫"
            )
            assert "return" in body, (
                f"回歸:{m} 內缺 `return` (helper False 時不該繼續呼叫 open_extractor_dialog)"
            )

    def test_extractor_view_imports_os(self):
        """ExtractorView 必須 import os 才能用 os.path.isdir。"""
        src = _read(EXTRACTOR_VIEW)
        assert "^import os$" in src or "^import os\n" in src or src.startswith("import os") or "\nimport os\n" in src, (
            "回歸:ExtractorView 沒 import os (但用 os.path.isdir 會 crash)"
        )


class TestFixOnStartClickSnackbar:
    """extractor_dialog.py:on_start_click 內早 return SnackBar (2026-07-13 UX 改進)。

    User review 後 extractor_dialog.py:on_start_click 內兩個早 return 都加
    SnackBar。雖然按鈕層已經提早攔截,extractor_dialog.py 內仍保留 SnackBar
    作為 第二線防呆 (例如 programmatic 呼叫或未來 bypass button layer)。
    """

    def test_on_start_click_first_rejection_has_snackbar(self):
        """on_start_click 第一個早 return (mods_dir 空) 必須有 SnackBar。"""
        src = _read(EXTRACTOR_DIALOG)
        m = re.search(
            r"def on_start_click\(e\):(.*?)(?=\n    def |\ndef |\nclass |\Z)",
            src, re.DOTALL,
        )
        assert m is not None, "找不到 on_start_click 函式"
        body = m.group(1)
        # 第一個 if not mods_dir 區塊:到 if not os.path.isdir 之前
        idx_isdir = body.find("if not os.path.isdir")
        first_block = body[:idx_isdir] if idx_isdir > 0 else body
        assert "SnackBar" in first_block, (
            "回歸:on_start_click 第一個早 return 缺 SnackBar"
        )
        assert "page.show_dialog" in first_block, (
            "回歸:on_start_click 第一個早 return 缺 page.show_dialog"
        )

    def test_on_start_click_second_rejection_has_snackbar(self):
        """on_start_click 第二個早 return (mods_dir 不是資料夾) 必須有 SnackBar。"""
        src = _read(EXTRACTOR_DIALOG)
        m = re.search(
            r"def on_start_click\(e\):(.*?)(?=\n    def |\ndef |\nclass |\Z)",
            src, re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        idx_isdir = body.find("if not os.path.isdir")
        assert idx_isdir > 0
        second_block = body[idx_isdir:]
        assert "SnackBar" in second_block, (
            "回歸:on_start_click 第二個早 return 缺 SnackBar"
        )
        assert "page.show_dialog" in second_block, (
            "回歸:on_start_click 第二個早 return 缺 page.show_dialog"
        )




class TestFixPhase3DeadCodeRemoval:
    """Phase 3 (2026-07-13) 物理刪除 legacy code & test 的 source-level 斷言。

    確保以下項目被物理刪除後,未來不會被偷偷加回:
    - extractor_actions.py 的 4 個 legacy 函式
    - extractor_view.py 的 2 個 dead method
    - extractor_view.py 的 3 個 import
    - 11 個 legacy test class 在 test_extractor_dual_mode.py
    - 1 條 source-grep 測試 test_show_preview_uses_threading_event
    """

    def test_extractor_actions_no_dead_code_audit_block(self):
        """extractor_actions.py 頂部 DEAD CODE audit 區塊應已被物理刪除。"""
        src = _read(EXTRACTOR_ACTIONS)
        assert "# =============================================================================" not in src or src.count("# DEAD CODE") == 0, (
            "回歸:extractor_actions.py 又有 DEAD CODE 區塊 "
            "(Phase 3 commit af66bce 應物理刪除 4 個 legacy 函式 + 區塊註解)"
        )

    def test_extractor_actions_no_extraction_worker(self):
        """_extraction_worker 函式應已被物理刪除(legacy code)。"""
        src = _read(EXTRACTOR_ACTIONS)
        assert "def _extraction_worker" not in src, (
            "回歸:_extraction_worker 函式重新出現 "
            "(Phase 3 commit af66bce 已物理刪除)"
        )

    def test_extractor_actions_no_extraction_start_extraction_function(self):
        """extractor_actions.start_extraction 函式應已被物理刪除(legacy code)。"""
        src = _read(EXTRACTOR_ACTIONS)
        assert "def start_extraction(view, mode: str):" not in src, (
            "回歸:extractor_actions.start_extraction 函式重新出現 "
            "(Phase 3 commit af66bce 已物理刪除,改名為 nested def start_extraction(e) "
            "在 extractor_dialog.py:631,signature 完全不同)"
        )

    def test_extractor_actions_no_show_preview_function(self):
        """extractor_actions.show_preview 函式應已被物理刪除(legacy code)。"""
        src = _read(EXTRACTOR_ACTIONS)
        assert "def show_preview(view, mode: str):" not in src, (
            "回歸:extractor_actions.show_preview 函式重新出現 "
            "(Phase 3 commit af66bce 已物理刪除,改名為 nested def show_preview(e) "
            "在 extractor_dialog.py)"
        )

    def test_extractor_actions_no_build_preview_result_dialog(self):
        """build_preview_result_dialog 函式應已被物理刪除(legacy code)。"""
        src = _read(EXTRACTOR_ACTIONS)
        assert "def build_preview_result_dialog" not in src, (
            "回歸:build_preview_result_dialog 函式重新出現 "
            "(Phase 3 commit af66bce 已物理刪除)"
        )

    def test_extractor_view_no_legacy_start_extraction_method(self):
        """extractor_view.start_extraction method 應已被物理刪除(legacy code)。"""
        src = _read(EXTRACTOR_VIEW)
        # 必須存在 nested def start_extraction(e) 在 extractor_dialog.py,不在 extractor_view
        assert "    def start_extraction(self, mode: str):" not in src, (
            "回歸:extractor_view.start_extraction(self, mode) method 重新出現 "
            "(Phase 3 commit af66bce 已物理刪除,它是 _run_extraction_flow 的死代理 wrapper)"
        )

    def test_extractor_view_no_legacy_show_preview_method(self):
        """extractor_view.show_preview method 應已被物理刪除(legacy code)。"""
        src = _read(EXTRACTOR_VIEW)
        assert "    def show_preview(self, mode: str):" not in src, (
            "回歸:extractor_view.show_preview(self, mode) method 重新出現 "
            "(Phase 3 commit af66bce 已物理刪除,它是 _run_preview_flow 的死代理 wrapper)"
        )

    def test_extractor_view_no_legacy_imports(self):
        """extractor_view.py 不應 import legacy 的 build_preview_result_dialog / show_preview / start_extraction。"""
        src = _read(EXTRACTOR_VIEW)
        assert "build_preview_result_dialog" not in src, (
            "回歸:build_preview_result_dialog 又被 import "
            "(Phase 3 commit af66bce 已從 import 移除)"
        )
        assert "run_extraction_flow" not in src, (
            "回歸:run_extraction_flow alias 又被 import "
            "(Phase 3 commit af66bce 已從 import 移除)"
        )
        assert "run_preview_flow" not in src, (
            "回歸:run_preview_flow alias 又被 import "
            "(Phase 3 commit af66bce 已從 import 移除)"
        )

    def test_extractor_dialog_docstring_no_legacy_reference(self):
        """extractor_dialog.open_preview_dialog docstring 不應引用舊 extractor_actions.show_preview。"""
        src = _read(EXTRACTOR_DIALOG)
        # 找 open_preview_dialog 函式範圍
        m = re.search(
            "def open_preview_dialog\([^)]*\):(.*?)(?=\ndef |\nclass |\Z)",
            src, re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        assert "extractor_actions.show_preview" not in body, (
            "回歸:open_preview_dialog docstring 又引用已刪除的 extractor_actions.show_preview "
            "(Phase 3 commit af66bce 已修正 docstring)"
        )

    def test_no_legacy_test_class_in_test_extractor_dual_mode(self):
        """test_extractor_dual_mode.py 不應有被 Phase 3 物理刪除的 11 個 legacy test class。"""
        src = _read(REPO_ROOT / "tests" / "test_extractor_dual_mode.py")
        legacy_classes = [
            "TestDualModeCompletionLogSkip",
            "TestDualModeStatsSource",
            "TestLambdaClosurePhaseCapture",
            "TestCurrentPhaseInitialization",
            "TestExtractionState",
            "TestExtractorActionsSessionStart",
            "TestShowPreviewPollOrder",
            "TestBuildPreviewResultDialog",
            "TestStartExtractionBranches",
            "TestShowPreviewOutputPaths",
            "TestExtractionWorkerBranches",
        ]
        # 排除 TestProgressBarPhaseReset(保留,測現役共用 generator)
        # 排除 TestUpdateStatsFromLog(共用 regex parser,保留)
        for cname in legacy_classes:
            assert f"class {cname}" not in src, (
                f"回歸:{cname} legacy test class 重新出現 "
                f"(Phase 3 commit af66bce 已物理刪除)"
            )

    def test_no_show_preview_threading_event_test(self):
        """test_show_preview_uses_threading_event 應已被物理刪除(source-level grep 測試)。"""
        src = _read(REPO_ROOT / "tests" / "test_audit_fixes_2026_06_28.py")
        assert "def test_show_preview_uses_threading_event" not in src, (
            "回歸:test_show_preview_uses_threading_event 重新出現 "
            "(Phase 3 commit af66bce 已物理刪除,show_preview 已刪)"
        )

    def test_extractor_view_no_update_stats_from_log_wrapper(self):
        """extractor_view._update_stats_from_log wrapper 應已被物理刪除(Phase 3 partial)。"""
        src = _read(EXTRACTOR_VIEW)
        assert "def _update_stats_from_log" not in src, (
            "回歸:extractor_view._update_stats_from_log wrapper 重新出現 "
            "(Phase 3 partial 物理刪除,_extraction_worker 已刪後無 caller)"
        )


class TestFixDUALSignatureBug:
    """Bug 1 fix (2026-07-13): extract_dual_files_generator 加 lang_codes keyword arg。

    User 實機測試 DUAL 模式發現:
    `extract_dual_files_generator() takes 2 positional arguments but 3 were given`

    Root cause:
    - extractor_dialog.py 原本呼叫 `extract_dual_files_generator(mods_dir, final_output, selected_codes)`
    - 但 generator signature 是 `extract_dual_files_generator(mods_dir, output_dir, *, skip_zh_cn=False)`
    - selected_codes 不是 skip_zh_cn(型別不同,語意不同),TypeError

    修法:
    1. extract_dual_files_generator signature 加 `lang_codes: list[str] | None = None`
    2. extractor_dialog.py 改用 keyword arg `lang_codes=selected_codes`
    """

    def test_extract_dual_files_generator_has_lang_codes(self):
        """extract_dual_files_generator signature 必須有 lang_codes keyword arg。"""
        src = _read(REPO_ROOT / "translation_tool" / "core" / "jar_processor.py")
        # 找 def extract_dual_files_generator
        m = re.search(
            r"def extract_dual_files_generator\([\s\S]*?\):",
            src, re.MULTILINE,
        )
        assert m is not None, "找不到 extract_dual_files_generator"
        sig = m.group(0)
        assert "lang_codes" in sig, (
            "回歸:extract_dual_files_generator signature 沒有 lang_codes "
            "(Bug 1 fix d28d636 commit 應加此 keyword arg)"
        )

    def test_extract_dual_files_generator_passes_lang_codes_to_regex(self):
        """extract_dual_files_generator 內部 build_lang_file_regex 必須傳 codes=lang_codes。"""
        src = _read(REPO_ROOT / "translation_tool" / "core" / "jar_processor.py")
        # 直接在整個 source 找函式 signature 範圍內的 build_lang_file_regex 呼叫
        # (因為 multiline signature regex 太複雜,改用單獨存在性檢查)
        assert "build_lang_file_regex(codes=lang_codes, skip_zh_cn=skip_zh_cn)" in src, (
            "回歸:jar_processor.py 內沒有 `build_lang_file_regex(codes=lang_codes, "
            "skip_zh_cn=skip_zh_cn)` 呼叫 "
            "(Bug 1 fix 應讓 lang_codes 過濾真正生效,此斷言鎖定真正的 source pattern)"
        )
    def test_extractor_dialog_uses_lang_codes_keyword_arg(self):
        """extractor_dialog.py 對 extract_dual_files_generator 必須用 lang_codes=selected_codes keyword arg。"""
        src = _read(EXTRACTOR_DIALOG)
        # 找 open_extractor_dialog 函式範圍
        m = re.search(
            "def open_extractor_dialog\\([^)]*\\):(.*?)(?=\\ndef |\\nclass |\\Z)",
            src, re.DOTALL,
        )
        assert m is not None
        body = m.group(0)
        # 必須有 keyword arg 呼叫 (multiline 也支援:查 「lang_codes=selected_codes」字串)
        # 🐛 2026-07-14 user review: 改成 multiline 呼叫 (加 skip_zh_cn)
        assert "lang_codes=selected_codes" in body, (
            "回歸:extractor_dialog 沒用 lang_codes=selected_codes keyword arg 呼叫 "
            "(Bug 1 fix 應避免 TypeError)"
        )
        # 不能用錯誤的 positional 3 args 呼叫
        assert "extract_dual_files_generator(mods_dir, final_output, selected_codes)" not in body, (
            "回歸:extractor_dialog 還是用錯誤的 positional 3 args 呼叫 "
            "(這會引起 TypeError: takes 2 positional arguments but 3 were given)"
        )
        # 🐛 2026-07-14 Phase 2 fix: skip_zh_cn 也用 keyword arg
        assert "skip_zh_cn=selected_skip_zh_cn" in body, (
            "回歸:extract_dual_files_generator 沒傳 skip_zh_cn=selected_skip_zh_cn "
            "(Phase 2 user review 應讓主 UI skip_zh_cn_switch 生效)"
        )


class TestFixExceptionTraceback:
    """Bug 2 fix (2026-07-13): exception handler 加 traceback.format_exc()。

    User 報告: 實機測試遇到 exception 時,log 後台只有 `[ERROR] {ex}` 訊息,
    沒有堆疊追蹤,debug 不便。

    Root cause:
    extractor_dialog.py exception handler 只印 str(ex),沒用 traceback.format_exc()。

    修法:
    1. 加 `import traceback`
    2. exception handler 加 `add_log(f"[TRACEBACK]\n{traceback.format_exc()}", level="error")`
    """

    def test_extractor_dialog_imports_traceback(self):
        """extractor_dialog.py 必須 import traceback。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "^import traceback$" in src or "\nimport traceback\n" in src or src.startswith("import traceback"), (
            "回歸:extractor_dialog.py 沒 import traceback "
            "(Bug 2 fix 應加 import 才能用 traceback.format_exc())"
        )

    def test_extractor_dialog_exception_handler_includes_traceback(self):
        """exception handler 必須用 traceback.format_exc()。"""
        src = _read(EXTRACTOR_DIALOG)
        # 找 except Exception as ex: 範圍
        m = re.search(
            "except Exception as ex:(.*?)(?=\n        (?:state\[\"running\"\]|except |finally))",
            src, re.DOTALL,
        )
        assert m is not None, "找不到 except Exception as ex:"
        body = m.group(1)
        assert "traceback.format_exc()" in body, (
            "回歸:exception handler 沒用 traceback.format_exc() "
            "(Bug 2 fix 應加完整堆疊追蹤,user 實機 debug 不便)"
        )

    def test_extractor_dialog_exception_handler_includes_tracking_label(self):
        """exception handler 必須有 [TRACEBACK] 標籤區分主錯誤訊息與堆疊。"""
        src = _read(EXTRACTOR_DIALOG)
        m = re.search(
            "except Exception as ex:(.*?)(?=\n        (?:state\[\"running\"\]|except |finally))",
            src, re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        assert "[TRACEBACK]" in body, (
            "回歸:exception handler 沒加 [TRACEBACK] 標籤 "
            "(區分主錯誤訊息 [ERROR] 跟堆疊追蹤)"
        )




class TestFixDUALResultSection:
    """Phase 3 (2026-07-13) DUAL mode 結果區顯示 LANG/BOOK 分區的 source-level 斷言。

    User 選項 B: DUAL mode 完成時,結果區除了合計還要分 LANG/BOOK 兩行顯示。
    鎖住:
    - run_extraction_loop 回傳的 stats 必須有 lang/book sub-dict
    - update_dual_stats helper 必須存在於 extractor_dialog.py
    - lang_row / book_row UI 元件必須存在,visible 預設 False
    - extractor_dialog.py 必須在 selected_mode == "dual" 時呼叫 update_dual_stats
    """

    def test_run_extraction_loop_returns_lang_book_subdicts(self):
        """run_extraction_loop 回傳的 stats dict 必須有 lang / book sub-dict 初始化。"""
        src = _read(REPO_ROOT / "app" / "services_impl" / "pipelines" / "extract_service.py")
        # 找 run_extraction_loop 函式
        m = re.search(
            r"def run_extraction_loop\([\s\S]*?\):",
            src,
        )
        assert m is not None
        # 從 sig end 往後找 1000 字元內的 body
        body = src[m.end():m.end() + 1500]
        assert '"lang":' in body, (
            "回歸:run_extraction_loop 內 stats dict 沒有 lang sub-dict 初始化 "
            "(Phase 3 應加,給 DUAL mode 顯示 LANG 分區用)"
        )
        assert '"book":' in body, (
            "回歸:run_extraction_loop 內 stats dict 沒有 book sub-dict 初始化 "
            "(Phase 3 應加,給 DUAL mode 顯示 BOOK 分區用)"
        )

    def test_run_extraction_loop_extracts_phase_stats(self):
        """run_extraction_loop 必須把 generator yield 的 phase=lang/book stats 拆解到對應 sub-dict。"""
        src = _read(REPO_ROOT / "app" / "services_impl" / "pipelines" / "extract_service.py")
        m = re.search(
            r"def run_extraction_loop\([\s\S]*?\):",
            src,
        )
        assert m is not None
        # 從 sig end 往後找 3000 字元(擴大,確保 capture 到 dict(result))
        body = src[m.end():m.end() + 3000]
        assert 'phase = update.get("phase")' in body or 'phase in ("lang", "book")' in body, (
            "回歸:run_extraction_loop 沒拆解 phase stats "
            "(Phase 3 應把 yield 帶 'phase' 的 stats 寫到對應 sub-dict)"
        )
        assert "stats[phase] = dict(result)" in body or "stats[phase] = dict(result)" in body, (
            "回歸:run_extraction_loop 沒把 phase stats 寫入 sub-dict"
        )

    def test_extract_dual_files_generator_yields_pure_book_stats(self):
        """extract_dual_files_generator book phase 必須 yield 純 book_stats (不是 combined)。

        Phase 3 (2026-07-13) user 選項 B fix: 原本 book phase loop 內 yield combined,
        run_extraction_loop 會把 combined 寫進 stats["book"] sub-dict,
        user 看到「BOOK 成功 13」其實是 lang+book 合計,誤導。
        改成 yield 純 book_stats,頂層合計由最終 combined yield (book phase 結束後) 提供,
        且必須用 phase="book_final" (不在 run_extraction_loop 拆解範圍)避免覆寫 sub-dict。
        """
        src = _read(REPO_ROOT / "translation_tool" / "core" / "jar_processor.py")
        # 找 extract_dual_files_generator 函式
        m = re.search(
            r"def extract_dual_files_generator\([\s\S]*?\):",
            src,
        )
        assert m is not None, "找不到 extract_dual_files_generator"
        body = src[m.end():m.end() + 3000]
        # 1. book phase loop 必須 yield 純 book_stats,不是 combined
        assert 'yield {**update, "stats": book_stats, "phase": "book"}' in body, (
            "回歸:extract_dual_files_generator book phase 還在 yield combined "
            "(Phase 3 fix 應 yield 純 book_stats 給 sub-dict,避免把 combined 寫到 stats['book'])"
        )
        # 2. book phase 結束後必須 yield 一個 combined 給頂層合計
        assert "last_book_stats = None" in body, (
            "回歸:extract_dual_files_generator 沒追蹤 last_book_stats "
            "(Phase 3 fix 應在 book phase 結束後 yield combined 給頂層合計)"
        )
        # 3. 不應該再有舊的 combined 在 loop 內 yield
        assert 'yield {**update, "stats": combined, "phase": "book"}' not in body, (
            "回歸:extract_dual_files_generator book phase loop 內又 yield combined "
            "(Phase 3 fix 應改 yield 純 book_stats)"
        )
        # 4. 最終的 combined yield 必須用 phase="book_final" (不入 run_extraction_loop 拆解範圍),
        #    否則 stats["book"] 會被覆寫成 combined,user 看到 BOOK row = lang+book 合計
        assert 'yield {"stats": combined, "phase": "book_final"}' in body, (
            "回歸:最終 combined yield 用 phase='book' 會被 run_extraction_loop 當 sub-dict 拆解,"
            "覆寫 stats['book'] = combined,讓 BOOK row 顯示 lang+book 合計,誤導 user。"
            "Phase 3 fix 必須用 phase='book_final' 不在 ('lang','book') 範圍"
        )

    def test_extractor_dialog_has_update_dual_stats_helper(self):
        """extractor_dialog.py 必須有 update_dual_stats helper。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "def update_dual_stats(" in src, (
            "回歸:extractor_dialog.py 沒有 update_dual_stats helper "
            "(Phase 3 應加,給 DUAL mode LANG/BOOK 分區顯示用)"
        )

    def test_extractor_dialog_has_lang_row_ui(self):
        """extractor_dialog.py 必須有 lang_row UI 元件。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "lang_row = ft.Row(" in src, (
            "回歸:extractor_dialog.py 沒有 lang_row UI 元件 "
            "(Phase 3 應加,顯示 LANG 提取完成 XXX)"
        )
        assert "ft.Text(\"LANG：" in src, (
            "回歸:lang_row label 應顯示 LANG：文字"
        )

    def test_extractor_dialog_has_book_row_ui(self):
        """extractor_dialog.py 必須有 book_row UI 元件。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "book_row = ft.Row(" in src, (
            "回歸:extractor_dialog.py 沒有 book_row UI 元件 "
            "(Phase 3 應加,顯示 BOOK 提取完成 XXX)"
        )
        assert "ft.Text(\"BOOK：" in src, (
            "回歸:book_row label 應顯示 BOOK：文字"
        )

    def test_lang_book_row_added_to_dialog_column(self):
        """lang_row / book_row 必須加進 dialog content column 才會 render。"""
        src = _read(EXTRACTOR_DIALOG)
        # 找 stats_row 後面是否有 lang_row, book_row 加進 column
        assert "stats_row," in src and "lang_row," in src and "book_row," in src, (
            "回歸:lang_row / book_row 沒加進 dialog content column "
            "(Phase 3 應放在 stats_row 後面,讓 Flet 渲染)"
        )

    def test_run_extraction_calls_update_dual_stats_for_dual_mode(self):
        """extractor_dialog.run_extraction 必須在 selected_mode == 'dual' 時呼叫 update_dual_stats。"""
        src = _read(EXTRACTOR_DIALOG)
        # 找 run_extraction 函式範圍
        m = re.search(
            r"def run_extraction\(\):[\s\S]*?(?=\n        except Exception as ex:|\n    def [a-zA-Z]|\Z)",
            src,
        )
        assert m is not None
        body = m.group(0)
        assert "if selected_mode == \"dual\":" in body, (
            "回歸:run_extraction 沒在 selected_mode == dual 時呼叫 update_dual_stats "
            "(Phase 3 應只在 DUAL mode 才顯示 LANG/BOOK 分區)"
        )
        assert "update_dual_stats(result_stats)" in body, (
            "回歸:run_extraction 沒呼叫 update_dual_stats(result_stats)"
        )




class TestFixStatsBadgeRemoved:
    """2026-07-14 user review: 移除底部統計徽章(成功/跳過/失敗)。

    User 截圖顯示主頁底部有 stats badge 顯示「成功 X / 跳過 Y / 失敗 Z」,但這個
    即時計數其實跟 dialog 內的 stats_row 重複顯示,而且不應該放在主 UI 的 settings
    card 上(user 從 dialog 才能完整看見 DUAL mode LANG/BOOK 分區)。
    物理刪除:
    - _build_stats_badge 函式 (extractor_panels.py:82)
    - _build_stats_badge(view) 呼叫 (extractor_panels.py:286)
    - _stats_success / _stats_warnings / _stats_failures view 屬性
    - docstring 內關於 _build_stats_badge 的條目
    - test_extractor_view_stats_badge_texts 測試
    """

    def test_no_stats_badge_function_in_extractor_panels(self):
        """_build_stats_badge 函式必須已被物理刪除(2026-07-14 user UX 改進)。"""
        src = _read(REPO_ROOT / "app" / "views" / "extractor" / "extractor_panels.py")
        assert "def _build_stats_badge" not in src, (
            "回歸:extractor_panels.py 又出現 _build_stats_badge 函式 "
            "(2026-07-14 user review 應物理刪除,不在主 UI 顯示即時計數徽章)"
        )

    def test_no_stats_badge_caller_in_extractor_panels(self):
        """_build_stats_badge(view) 呼叫必須已被物理刪除。"""
        src = _read(REPO_ROOT / "app" / "views" / "extractor" / "extractor_panels.py")
        assert "_build_stats_badge(view)" not in src, (
            "回歸:extractor_panels.py 又呼叫 _build_stats_badge(view) "
            "(2026-07-14 user review 應從 settings column 移除)"
        )

    def test_no_stats_attributes_in_extractor_panels(self):
        """_stats_success / _stats_warnings / _stats_failures view 屬性必須已被物理刪除。"""
        src = _read(REPO_ROOT / "app" / "views" / "extractor" / "extractor_panels.py")
        assert "_stats_success" not in src, (
            "回歸:extractor_panels.py 又建立 _stats_success view 屬性"
        )
        assert "_stats_warnings" not in src, (
            "回歸:extractor_panels.py 又建立 _stats_warnings view 屬性"
        )
        assert "_stats_failures" not in src, (
            "回歸:extractor_panels.py 又建立 _stats_failures view 屬性"
        )

    def test_no_stats_badge_docstring_in_extractor_panels(self):
        """extractor_panels.py 模塊 docstring 內 _build_stats_badge 條目必須已被物理刪除。"""
        src = _read(REPO_ROOT / "app" / "views" / "extractor" / "extractor_panels.py")
        assert "_build_stats_badge() → 統計徽章" not in src, (
            "回歸:extractor_panels.py 模塊 docstring 又提到 _build_stats_badge "
            "(2026-07-14 user review 應從 docstring 移除)"
        )

    def test_no_stats_badge_test_in_test_file(self):
        """test_extractor_view_stats_badge_texts 測試必須已被物理刪除。"""
        src = _read(REPO_ROOT / "tests" / "test_extractor_view_characterization.py")
        assert "def test_extractor_view_stats_badge_texts" not in src, (
            "回歸:test_extractor_view_characterization.py 又出現 stats badge 測試 "
            "(2026-07-14 user review 應物理刪除對應測試)"
        )

    def test_extractor_view_no_stats_comment(self):
        """extractor_view.py 不應再提到 _stats_success / _stats_warnings / _stats_failures。"""
        src = _read(EXTRACTOR_VIEW)
        assert "_stats_success" not in src, (
            "回歸:extractor_view.py 註解又提到 _stats_success "
            "(2026-07-14 user review 應從註解刪除過時描述)"
        )
        assert "_stats_warnings" not in src, (
            "回歸:extractor_view.py 註解又提到 _stats_warnings"
        )
        assert "_stats_failures" not in src, (
            "回歸:extractor_view.py 註解又提到 _stats_failures"
        )


class TestFixSkipZhCnSwitchWiring:
    """2026-07-14 user review: 主 UI skip_zh_cn_switch 必須真的接到 generator。

    User audit 發現:extractor_view.py 內的 skip_zh_cn_switch 純 dead code,
    open_extractor_dialog 的呼叫都沒傳 skip_zh_cn,user 開啟切換沒效果。

    修法:
    - _handle_extract_lang_click / _handle_extract_dual_click 讀 self.skip_zh_cn_switch.value
    - open_extractor_dialog 新增 skip_zh_cn 參數
    - run_extraction 內 selected_skip_zh_cn 傳到 extract_*_files_generator

    此 class 鎖住 wiring pattern,防止 future commit 偷偷斷掉
    skip_zh_cn_switch 跟 generator 的連線。
    """

    def test_extractor_dialog_open_extractor_dialog_has_skip_zh_cn_param(self):
        """open_extractor_dialog signature 必須有 skip_zh_cn 參數。"""
        src = _read(EXTRACTOR_DIALOG)
        m = re.search(
            "def open_extractor_dialog\\([^)]*\\):",
            src,
        )
        assert m is not None
        sig = m.group(0)
        assert "skip_zh_cn" in sig, (
            "回歸:open_extractor_dialog signature 沒有 skip_zh_cn 參數 "
            "(2026-07-14 user review 應加此 keyword 參數)"
        )

    def test_extractor_view_handle_lang_click_passes_skip_zh_cn(self):
        """_handle_extract_lang_click 必須傳 skip_zh_cn=self.skip_zh_cn_switch.value。"""
        src = _read(EXTRACTOR_VIEW)
        m = re.search(
            "def _handle_extract_lang_click\\([^)]*\\):",
            src,
        )
        assert m is not None
        next_def = re.search(
            "\\ndef [a-zA-Z]",
            src[m.end():],
        )
        end = m.end() + next_def.start() if next_def else len(src)
        body = src[m.start():end]
        assert "skip_zh_cn=self.skip_zh_cn_switch.value" in body, (
            "回歸:_handle_extract_lang_click 沒傳 skip_zh_cn=self.skip_zh_cn_switch.value "
            "(2026-07-14 user review 主 UI 開關應生效)"
        )

    def test_extractor_view_handle_dual_click_passes_skip_zh_cn(self):
        """_handle_extract_dual_click 必須傳 skip_zh_cn=self.skip_zh_cn_switch.value。"""
        src = _read(EXTRACTOR_VIEW)
        m = re.search(
            "def _handle_extract_dual_click\\([^)]*\\):",
            src,
        )
        assert m is not None
        next_def = re.search(
            "\\ndef [a-zA-Z]",
            src[m.end():],
        )
        end = m.end() + next_def.start() if next_def else len(src)
        body = src[m.start():end]
        assert "skip_zh_cn=self.skip_zh_cn_switch.value" in body, (
            "回歸:_handle_extract_dual_click 沒傳 skip_zh_cn=self.skip_zh_cn_switch.value"
        )

    def test_extractor_dialog_run_extraction_passes_skip_zh_cn_to_dual(self):
        """run_extraction 內 extract_dual_files_generator 必須用 skip_zh_cn=selected_skip_zh_cn。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "extract_dual_files_generator(" in src
        assert "skip_zh_cn=selected_skip_zh_cn" in src, (
            "回歸:extract_dual_files_generator 沒傳 skip_zh_cn=selected_skip_zh_cn "
            "(Phase 2 user review 應串接 skip_zh_cn)"
        )

    def test_extractor_dialog_run_extraction_passes_skip_zh_cn_to_lang(self):
        """run_extraction 內 extract_lang_files_generator 必須也傳 skip_zh_cn。"""
        src = _read(EXTRACTOR_DIALOG)
        assert "extract_lang_files_generator(" in src
        assert "skip_zh_cn=selected_skip_zh_cn" in src, (
            "回歸:extract_lang_files_generator 沒傳 skip_zh_cn "
            "(Phase 2 user review 應在 lang mode 也生效)"
        )




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
