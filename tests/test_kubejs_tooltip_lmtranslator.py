"""translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py 模組測試。

用途：測試 kubejs_tooltip_lmtranslator 模組的功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.kubejs import kubejs_tooltip_lmtranslator  # noqa: E402


def test_collect_items_from_mapping_basic(tmp_path: Path) -> None:
    """測試 collect_items_from_mapping 基本功能。"""
    mapping = {
        "tooltip.1": "Hello",
        "tooltip.2": "World",
    }
    
    items = kubejs_tooltip_lmtranslator.collect_items_from_mapping(
        mapping,
        file_hint="output/kubejs/test.json",
    )
    
    assert len(items) == 2
    assert items[0]["cache_type"] == "kubejs"
    assert items[0]["path"] == "tooltip.1"
    assert items[0]["source_text"] == "Hello"


def test_collect_items_from_mapping_filters_invalid(tmp_path: Path) -> None:
    """測試 collect_items_from_mapping 過濾無效項目。"""
    mapping = {
        "valid.key": "Valid text",
        123: "Invalid key",
        "empty.value": "",
    }

    items = kubejs_tooltip_lmtranslator.collect_items_from_mapping(
        mapping,
        file_hint="output/kubejs/test.json",
    )

    assert len(items) == 1


def test_collect_items_from_mapping_marks_skip_reason() -> None:
    """URL / 圖片等 skip_reason 項目應被明確標記。"""
    mapping = {
        "tooltip.url": "https://example.com",
        "tooltip.normal": "Hello &aWorld",
    }

    items = kubejs_tooltip_lmtranslator.collect_items_from_mapping(
        mapping,
        file_hint="output/kubejs/test.json",
    )

    skip_item = next(it for it in items if it["path"] == "tooltip.url")
    normal_item = next(it for it in items if it["path"] == "tooltip.normal")

    assert skip_item["_skip_reason"] == "url"
    assert skip_item["text"] == "https://example.com"
    assert normal_item.get("_skip_reason") is None
    assert normal_item["text"] != normal_item["source_text"]


def test_count_translatable_keys(tmp_path: Path) -> None:
    """測試 count_translatable_keys 計算數量。"""
    mapping = {
        "key1": "value1",
        "key2": "value2",
        "key3": "",  # 空值不計
        "key4": "   ",  # 空白不計
    }
    
    count = kubejs_tooltip_lmtranslator.count_translatable_keys(mapping)
    
    assert count == 2


def test_count_translatable_keys_non_string(tmp_path: Path) -> None:
    """測試 count_translatable_keys 處理非字串值。"""
    mapping = {
        "key1": "value1",
        "key2": 123,
        "key3": None,
    }
    
    count = kubejs_tooltip_lmtranslator.count_translatable_keys(mapping)
    
    assert count == 1


def test_dry_run_stats_default(tmp_path: Path) -> None:
    """測試 DryRunStats 預設值。"""
    stats = kubejs_tooltip_lmtranslator.DryRunStats()
    
    assert stats.files == 0
    assert stats.total_keys == 0
    assert stats.cache_hit == 0
    assert stats.cache_miss == 0
    assert stats.per_file is None


def test_dry_run_stats_with_values(tmp_path: Path) -> None:
    """測試 DryRunStats 帶值初始化。"""
    stats = kubejs_tooltip_lmtranslator.DryRunStats(
        files=3,
        total_keys=50,
        cache_hit=20,
        cache_miss=30,
    )
    
    assert stats.files == 3
    assert stats.total_keys == 50
    assert stats.cache_hit == 20
    assert stats.cache_miss == 30
