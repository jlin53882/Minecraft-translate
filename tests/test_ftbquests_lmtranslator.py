"""translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py 模組測試。

用途：測試 ftbquests_lmtranslator 模組的功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.ftbquests import ftbquests_lmtranslator


def test_map_to_items_basic(tmp_path: Path) -> None:
    """測試 map_to_items 基本功能。"""
    mapping = {
        "quest.1.title": "Hello",
        "quest.2.title": "World",
    }
    
    items = ftbquests_lmtranslator.map_to_items(
        mapping,
        cache_type="ftbquests",
        file_hint="config/ftbquests/quests/en_us/test.json",
    )
    
    assert len(items) == 2
    assert items[0]["cache_type"] == "ftbquests"
    assert items[0]["path"] == "quest.1.title"
    assert items[0]["source_text"] == "Hello"


def test_map_to_items_filters_invalid(tmp_path: Path) -> None:
    """測試 map_to_items 過濾無效項目。"""
    mapping = {
        "valid.key": "Valid text",
        123: "Invalid key",  # key 不是字串
        "empty.value": "",  # value 是空字串
    }
    
    items = ftbquests_lmtranslator.map_to_items(
        mapping,
        cache_type="ftbquests",
        file_hint="config/ftbquests/quests/test.json",
    )
    
    assert len(items) == 1
    assert items[0]["path"] == "valid.key"


def test_count_translatable_keys(tmp_path: Path) -> None:
    """測試 count_translatable_keys 計算可翻譯鍵數量。"""
    mapping = {
        "key1": "value1",
        "key2": "value2",
        "key3": "",  # 空值不計
        "key4": "   ",  # 只有空白不計
    }
    
    count = ftbquests_lmtranslator.count_translatable_keys(mapping)
    
    assert count == 2


def test_count_translatable_keys_non_string_values(tmp_path: Path) -> None:
    """測試 count_translatable_keys 處理非字串值。"""
    mapping = {
        "key1": "value1",
        "key2": 123,  # 不是字串
        "key3": ["list"],  # 不是字串
    }
    
    count = ftbquests_lmtranslator.count_translatable_keys(mapping)
    
    assert count == 1


def test_dry_run_stats_default(tmp_path: Path) -> None:
    """測試 DryRunStats 預設值。"""
    stats = ftbquests_lmtranslator.DryRunStats()
    
    assert stats.files == 0
    assert stats.total_keys == 0
    assert stats.cache_hit == 0
    assert stats.cache_miss == 0
    assert stats.per_file is None


def test_dry_run_stats_with_values(tmp_path: Path) -> None:
    """測試 DryRunStats 帶值初始化。"""
    stats = ftbquests_lmtranslator.DryRunStats(
        files=5,
        total_keys=100,
        cache_hit=30,
        cache_miss=70,
    )
    
    assert stats.files == 5
    assert stats.total_keys == 100
    assert stats.cache_hit == 30
    assert stats.cache_miss == 70
