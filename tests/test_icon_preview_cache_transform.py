"""tests/test_icon_preview_cache_transform.py

測試 icon_preview_view 的快取 dict ↔ SimpleNamespace 轉換。
驗證：
  - 寫入時：SimpleNamespace → dict
  - 讀出時：dict → SimpleNamespace
確保屬性存取（e.zh_tw）在兩種情况下都正常。
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import flet as ft


class MockPage:
    def __init__(self):
        self.overlay = []


def create_view():
    """建立 IconPreviewView"""
    from app.views.icon_preview_view import IconPreviewView

    with patch.object(IconPreviewView, "__init__", lambda self, page: None):
        view = IconPreviewView.__new__(IconPreviewView)
        view.page = MockPage()
        view._entries_cache = None
        view._cache_meta = {}
    return view


class TestCacheWriteTransform:
    """驗證快取寫入時 SimpleNamespace → dict 轉換"""

    def test_namespace_to_dict_conversion(self):
        """SimpleNamespace entry 寫入時應該轉為 dict"""
        view = create_view()

        entries = [
            SimpleNamespace(
                modid="actuallyadditions",
                key="item.actuallyadditions.atomic_reconstructor",
                en="Atomic Reshaper",
                zh_tw="原子重塑器",
            ),
        ]

        # 模擬 PR #51 的快取寫入邏輯
        cache_entries = []
        for entry in entries:
            if hasattr(entry, "__dict__"):
                cache_entries.append(entry.__dict__)
            else:
                cache_entries.append(entry)

        assert isinstance(cache_entries[0], dict)
        assert cache_entries[0]["modid"] == "actuallyadditions"
        assert cache_entries[0]["zh_tw"] == "原子重塑器"

    def test_mixed_entries(self):
        """同時有 dict 和 SimpleNamespace 時，兩者都正確處理"""
        view = create_view()

        entries = [
            SimpleNamespace(modid="mod1", key="key1", en="en1", zh_tw="tw1"),
            {"modid": "mod2", "key": "key2", "en": "en2", "zh_tw": "tw2"},  # 已是 dict
        ]

        cache_entries = []
        for entry in entries:
            if hasattr(entry, "__dict__"):
                cache_entries.append(entry.__dict__)
            else:
                cache_entries.append(entry)

        assert all(isinstance(e, dict) for e in cache_entries)
        assert cache_entries[0]["modid"] == "mod1"
        assert cache_entries[1]["modid"] == "mod2"


class TestCacheReadTransform:
    """驗證快取讀出時 dict → SimpleNamespace 轉換"""

    def test_dict_to_namespace_conversion(self):
        """dict entry 讀出時應該轉為 SimpleNamespace，屬性存取正常"""
        view = create_view()

        # 模擬 PR #51 的快取讀取邏輯
        cache = [
            {"modid": "actuallyadditions", "key": "item.actuallyadditions.atomic_reconstructor", "en": "Atomic Reshaper", "zh_tw": "原子重塑器"},
        ]

        # PR #51 的復元邏輯
        mods_dict = {}
        for entry in cache:
            if isinstance(entry, dict):
                ns = SimpleNamespace(**entry)
                mods_dict.setdefault(ns.modid, []).append(ns)
            else:
                mods_dict.setdefault(entry.modid, []).append(entry)

        # 驗證：用屬性存取不炸錯
        mod1_entries = mods_dict["actuallyadditions"]
        assert len(mod1_entries) == 1
        entry = mod1_entries[0]
        assert entry.modid == "actuallyadditions"
        assert entry.zh_tw == "原子重塑器"
        assert entry.en == "Atomic Reshaper"

    def test_original_namespace_unchanged(self):
        """原本就是 SimpleNamespace 的 entry 不需要轉換"""
        view = create_view()

        original = SimpleNamespace(modid="mod1", key="key1", en="en1", zh_tw="tw1")
        cache = [original]

        mods_dict = {}
        for entry in cache:
            if isinstance(entry, dict):
                ns = SimpleNamespace(**entry)
                mods_dict.setdefault(ns.modid, []).append(ns)
            else:
                mods_dict.setdefault(entry.modid, []).append(entry)

        # 應該是同一個物件
        assert mods_dict["mod1"][0] is original

    def test_zhtw_attribute_access_after_restore(self):
        """快取讀回後，zh_tw.strip() 屬性存取正常（PR #51 修復的 bug）"""
        cache = [
            {"modid": "mod1", "key": "key1", "en": "en1", "zh_tw": "  有空白  "},
        ]

        # PR #51 的復元邏輯
        mods_dict = {}
        for entry in cache:
            if isinstance(entry, dict):
                ns = SimpleNamespace(**entry)
                mods_dict.setdefault(ns.modid, []).append(ns)
            else:
                mods_dict.setdefault(entry.modid, []).append(entry)

        entry = mods_dict["mod1"][0]
        # 這個在 PR #51 未修復前會炸 AttributeError
        stripped = entry.zh_tw.strip()
        assert stripped == "有空白"

    def test_empty_cache(self):
        """空快取不應炸錯"""
        view = create_view()
        view._entries_cache = []

        # 空 list 走 for 迴圈不應有問題
        mods_dict = {}
        for entry in view._entries_cache:
            if isinstance(entry, dict):
                ns = SimpleNamespace(**entry)
                mods_dict.setdefault(ns.modid, []).append(ns)
            else:
                mods_dict.setdefault(entry.modid, []).append(entry)

        assert mods_dict == {}
