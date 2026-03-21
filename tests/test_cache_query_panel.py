"""app/views/cache_query_panel.py 單元測試。

用途：驗證 CacheQueryPanel 元件的功能正確性。
"""

from app.views.cache_query_panel import CacheQueryPanel
from app.views.cache_manager.cache_state import CacheQueryState


class _MockPage:
    def __init__(self):
        self.overlay = []
        self.updated = 0

    def update(self):
        self.updated += 1


def test_cache_query_panel_initialization():
    """測試 CacheQueryPanel 初始化"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)

    assert panel.page is page
    assert panel.state is state
    assert panel.last_overview_data is last_overview_data


def test_cache_query_panel_has_required_components():
    """測試 CacheQueryPanel 有所需元件"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)

    assert hasattr(panel, "tf_query_input")
    assert hasattr(panel, "dd_query_mode")
    assert hasattr(panel, "dd_query_type")
    assert hasattr(panel, "btn_query_search")
    assert hasattr(panel, "query_result_list")


def test_cache_query_panel_refresh_type_options():
    """測試分類選項更新"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {
        "types": {
            "lang": {"entries_count": 100},
            "patchouli": {"entries_count": 50},
        }
    }

    panel = CacheQueryPanel(page, state, last_overview_data)
    panel.refresh_type_options()

    # 應有 ALL + lang + patchouli 三個選項
    assert len(panel.dd_query_type.options) == 3


def test_cache_query_panel_iter_type_states():
    """測試迭代類型狀態"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {
        "types": {
            "lang": {"entries_count": 100, "is_dirty": False},
            "patchouli": {"entries_count": 50, "is_dirty": True},
        }
    }

    panel = CacheQueryPanel(page, state, last_overview_data)
    types = list(panel._iter_type_states(last_overview_data))

    assert len(types) == 2
    assert types[0][0] == "lang"
    assert types[1][0] == "patchouli"


def test_cache_query_panel_type_dirty_text():
    """測試髒污狀態文字"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {
        "types": {
            "lang": {"is_dirty": True},
            "patchouli": {"is_dirty": False},
        }
    }

    panel = CacheQueryPanel(page, state, last_overview_data)

    assert panel._type_dirty_text("lang") == "dirty"
    assert panel._type_dirty_text("patchouli") == "clean"
    assert panel._type_dirty_text("unknown") == "-"


def test_cache_query_panel_active_shard_filename():
    """測試活躍分片檔名"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {
        "types": {
            "lang": {"active_shard_id": "001"},
            "patchouli": {"active_shard_id": None},
        }
    }

    panel = CacheQueryPanel(page, state, last_overview_data)

    assert panel._active_shard_filename("lang") == "lang_001.json"
    assert panel._active_shard_filename("patchouli") == "-"
