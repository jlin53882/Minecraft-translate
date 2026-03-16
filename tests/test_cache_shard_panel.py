"""app/views/cache_shard_panel.py 單元測試。

用途：驗證 CacheShardPanel 元件的功能正確性。
"""

import pytest
from app.views.cache_shard_panel import CacheShardPanel
from app.views.cache_manager.cache_state import CacheShardState


class _MockPage:
    def __init__(self):
        self.overlay = []
        self.updated = 0

    def update(self):
        self.updated += 1


def test_cache_shard_panel_initialization():
    """測試 CacheShardPanel 初始化"""
    page = _MockPage()
    state = CacheShardState()
    last_overview_data = {"types": {}}

    panel = CacheShardPanel(page, state, last_overview_data)

    assert panel.page is page
    assert panel.state is state
    assert panel.last_overview_data is last_overview_data


def test_cache_shard_panel_has_required_components():
    """測試 CacheShardPanel 有所需元件"""
    page = _MockPage()
    state = CacheShardState()
    last_overview_data = {"types": {}}

    panel = CacheShardPanel(page, state, last_overview_data)

    assert hasattr(panel, "tf_shard_key_filter")
    assert hasattr(panel, "shard_detail_key_list")
    assert hasattr(panel, "shard_src_field")
    assert hasattr(panel, "shard_dst_field")
    assert hasattr(panel, "btn_shard_dst_apply")


def test_cache_shard_panel_iter_type_states():
    """測試迭代類型狀態"""
    page = _MockPage()
    state = CacheShardState()
    last_overview_data = {
        "types": {
            "lang": {"entries_count": 100},
            "patchouli": {"entries_count": 50},
        }
    }

    panel = CacheShardPanel(page, state, last_overview_data)
    types = list(panel._iter_type_states(last_overview_data))

    assert len(types) == 2


def test_cache_shard_panel_render_empty():
    """測試渲染空狀態"""
    page = _MockPage()
    state = CacheShardState()
    last_overview_data = {"types": {}}

    panel = CacheShardPanel(page, state, last_overview_data)
    panel._render_query_type_shard_page()

    # 應該顯示沒有分類資料
    assert len(panel.query_type_shard_col.controls) >= 1


def test_cache_shard_panel_render_with_data():
    """測試渲染有資料狀態"""
    page = _MockPage()
    state = CacheShardState()
    last_overview_data = {
        "types": {
            "lang": {
                "entries_count": 100,
                "active_shard_id": "001",
                "is_dirty": False,
            },
        }
    }

    panel = CacheShardPanel(page, state, last_overview_data)
    panel._render_query_type_shard_page()

    # 應該有一個分類項目
    assert len(panel.query_type_shard_col.controls) >= 1


def test_cache_shard_panel_select_key():
    """測試選擇 key"""
    page = _MockPage()
    state = CacheShardState()
    state.selected_type = "lang"
    state.selected_file = "lang_001.json"
    state.keys = ["key1", "key2", "key3"]
    last_overview_data = {"types": {}}

    panel = CacheShardPanel(page, state, last_overview_data)
    panel._on_select_shard_key("key2")

    assert panel.state.selected_key == "key2"


def test_cache_shard_panel_key_filter():
    """測試 key 篩選"""
    page = _MockPage()
    state = CacheShardState()
    state.keys = ["apple", "banana", "apricot"]
    last_overview_data = {"types": {}}

    panel = CacheShardPanel(page, state, last_overview_data)

    # 設定篩選條件
    panel.tf_shard_key_filter.value = "ap"
    panel._on_shard_key_filter_change(None)

    # 應該會重置 page
    assert panel.state.page == 1
