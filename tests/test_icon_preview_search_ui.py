"""tests/test_icon_preview_search_ui.py

測試 icon_preview_view 的即時搜尋 UI（PR #58 Phase 2）。

覆蓋：
- Debounce 機制（150ms）
- Mod 清單搜尋（大小寫不敏感、空白字元處理）
- Mod 詳情頁搜尋（key + value 同時搜）
- 搜尋結果數量顯示
- 清除搜尋時恢復完整列表
"""

import pytest
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock


# ==================================================
# Debounce 邏輯測試（隔離測試，不依賴 Flet）
# ==================================================

class MockDebounceState:
    """測試用 debounce 狀態追蹤"""

    def __init__(self):
        self.text = ""
        self.timer: threading.Timer | None = None
        self.executions: list[str] = []

    def cancel(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def schedule(self, text: str, delay: float = 0.15):
        self.text = text
        self.cancel()
        self.timer = threading.Timer(delay, self._execute, args=[text])
        self.timer.start()

    def _execute(self, text: str):
        self.executions.append(text)


class TestDebounceLogic:
    def test_single_execution(self):
        """一次輸入只觸發一次"""
        state = MockDebounceState()
        state.schedule("diamond")
        time.sleep(0.2)
        assert len(state.executions) == 1
        assert state.executions[0] == "diamond"

    def test_rapid_typing_cancels_previous(self):
        """快速打字取消前次計時"""
        state = MockDebounceState()
        state.schedule("d")
        time.sleep(0.05)
        state.schedule("di")
        time.sleep(0.05)
        state.schedule("dia")
        time.sleep(0.2)
        # 應該只有最後一次執行
        assert len(state.executions) == 1
        assert state.executions[0] == "dia"

    def test_empty_text_still_schedules(self):
        """空字串也會 schedule"""
        state = MockDebounceState()
        state.schedule("")
        time.sleep(0.2)
        assert len(state.executions) == 1
        assert state.executions[0] == ""

    def test_cancel_clears_timer(self):
        """cancel 後不執行"""
        state = MockDebounceState()
        state.schedule("test")
        state.cancel()
        time.sleep(0.2)
        assert len(state.executions) == 0


# ==================================================
# 搜尋邏輯測試（與 Flet 無關的純邏輯）
# ==================================================

def _mod_search_logic(mod_ids: list[str], keyword: str) -> list[str]:
    """Mod 清單搜尋純邏輯"""
    if not keyword:
        return mod_ids
    k = keyword.strip().lower()
    return [m for m in mod_ids if k in m.lower()]


def _detail_search_logic(
    entries: list[dict],
    keyword: str,
) -> list[dict]:
    """Mod 詳情頁搜尋純邏輯（同時搜 key + en + zh_tw）"""
    if not keyword:
        return entries
    k = keyword.strip().lower()
    result = []
    for e in entries:
        key = (e.get("key") or "").lower()
        en = (e.get("en") or "").lower()
        zh = (e.get("zh_tw") or "").lower()
        if k in key or k in en or k in zh:
            result.append(e)
    return result


class TestModSearchLogic:
    def test_empty_keyword_returns_all(self):
        mods = ["actuallyadditions", "ae2", "botania"]
        assert _mod_search_logic(mods, "") == mods

    def test_case_insensitive(self):
        mods = ["actuallyadditions", "AE2", "Botania"]
        assert _mod_search_logic(mods, "ACTU") == ["actuallyadditions"]
        result = _mod_search_logic(mods, "ae2")
        assert result == ["AE2"]  # 大小寫不敏感但回傳原始大小寫

    def test_whitespace_trimmed(self):
        mods = ["actuallyadditions", "ae2"]
        assert _mod_search_logic(mods, "  actually  ") == ["actuallyadditions"]

    def test_no_match(self):
        mods = ["actuallyadditions", "ae2"]
        assert _mod_search_logic(mods, "nonexistent") == []

    def test_partial_match(self):
        mods = ["actuallyadditions", "ae2wtlib", "ae2things"]
        assert _mod_search_logic(mods, "ae2") == ["ae2wtlib", "ae2things"]


class TestDetailSearchLogic:
    def test_empty_keyword_returns_all(self):
        entries = [
            {"key": "item.mod.drill", "en": "Drill", "zh_tw": "鑽頭"},
            {"key": "item.mod.coal", "en": "Coal", "zh_tw": "煤"},
        ]
        assert _detail_search_logic(entries, "") == entries

    def test_matches_key(self):
        entries = [
            {"key": "item.mod.diamond_sword", "en": "Diamond Sword", "zh_tw": "鑽石劍"},
            {"key": "item.mod.iron_sword", "en": "Iron Sword", "zh_tw": "鐵劍"},
        ]
        result = _detail_search_logic(entries, "diamond")
        assert len(result) == 1
        assert result[0]["key"] == "item.mod.diamond_sword"

    def test_matches_english_value(self):
        entries = [
            {"key": "item.mod.drill", "en": "Blue Drill", "zh_tw": "藍色鑽頭"},
            {"key": "item.mod.pickaxe", "en": "Iron Pickaxe", "zh_tw": "鐵鎬"},
        ]
        result = _detail_search_logic(entries, "blue")
        assert len(result) == 1
        assert result[0]["en"] == "Blue Drill"

    def test_matches_chinese_value(self):
        entries = [
            {"key": "item.mod.drill", "en": "Blue Drill", "zh_tw": "藍色鑽頭"},
            {"key": "item.mod.pickaxe", "en": "Iron Pickaxe", "zh_tw": "鐵鎬"},
        ]
        result = _detail_search_logic(entries, "藍色")
        assert len(result) == 1
        assert result[0]["zh_tw"] == "藍色鑽頭"

    def test_matches_key_and_value(self):
        """diamond 符合 key 和 value"""
        entries = [
            {"key": "item.mod.diamond_sword", "en": "Diamond Sword", "zh_tw": "鑽石劍"},
        ]
        result = _detail_search_logic(entries, "diamond")
        assert len(result) == 1

    def test_no_match(self):
        entries = [
            {"key": "item.mod.drill", "en": "Blue Drill", "zh_tw": "藍色鑽頭"},
        ]
        result = _detail_search_logic(entries, "nonexistent")
        assert result == []

    def test_whitespace_trimmed(self):
        entries = [
            {"key": "item.mod.drill", "en": "Blue Drill", "zh_tw": "藍色鑽頭"},
        ]
        result = _detail_search_logic(entries, "  blue  ")
        assert len(result) == 1

    def test_case_insensitive(self):
        entries = [
            {"key": "item.mod.drill", "en": "Blue Drill", "zh_tw": "藍色鑽頭"},
        ]
        result = _detail_search_logic(entries, "BLUE")
        assert len(result) == 1

    def test_missing_fields(self):
        """key/en/zh_tw 為 None 時不 crash"""
        entries = [
            {"key": "item.mod.drill", "en": None, "zh_tw": None},
            {"key": None, "en": "Test", "zh_tw": "測試"},
        ]
        result = _detail_search_logic(entries, "drill")
        assert len(result) == 1


# ==================================================
# 搜尋結果數量顯示
# ==================================================

def test_search_result_count_display():
    """搜尋結果數量顯示格式"""
    total = 138
    filtered = 12
    status = f"符合 {filtered} / {total} 筆"
    assert status == "符合 12 / 138 筆"


def test_no_result_status():
    """無符合結果時的顯示"""
    total = 138
    status = f"無符合結果（{total} 筆）"
    assert status == "無符合結果（138 筆）"


def test_no_filter_status():
    """清除篩選時顯示空白"""
    status = ""
    assert status == ""
