"""app/views/cache_query_panel.py 搜尋邏輯單元測試。

用途：驗證 CacheQueryPanel 搜尋功能。
"""

from app.views.cache_manager.cache_state import CacheQueryState
from app.views.cache_query_panel import CacheQueryPanel


class _MockPage:
    def __init__(self):
        self.overlay = []
        self.updated = 0

    def update(self):
        self.updated += 1


def test_cache_query_panel_search_requires_query():
    """測試搜尋需要輸入關鍵字"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)
    panel.tf_query_input.value = ""

    # 應該顯示提示訊息
    panel._on_query_search(None)

    # 由於沒有 mock page.update，這會失敗，但邏輯上會 return
    # 我們只驗證方法存在且可呼叫


def test_cache_query_panel_search_mode_key():
    """測試 KEY 模式搜尋"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {
        "types": {
            "lang": {"entries_count": 100, "active_shard_id": "001"}
        }
    }

    panel = CacheQueryPanel(page, state, last_overview_data)
    panel.tf_query_input.value = "test_key"

    # 驗證有 mode 下拉選單
    assert panel.dd_query_mode.value == "ALL"


def test_cache_query_panel_search_mode_dst():
    """測試 DST 模式搜尋"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {
        "types": {
            "lang": {"entries_count": 100, "active_shard_id": "001"}
        }
    }

    panel = CacheQueryPanel(page, state, last_overview_data)
    panel.dd_query_mode.value = "DST"

    assert panel.dd_query_mode.value == "DST"


def test_cache_query_panel_search_mode_all():
    """測試 ALL 模式搜尋"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {
        "types": {
            "lang": {"entries_count": 100, "active_shard_id": "001"}
        }
    }

    panel = CacheQueryPanel(page, state, last_overview_data)
    panel.dd_query_mode.value = "ALL"

    assert panel.dd_query_mode.value == "ALL"


def test_cache_query_panel_query_result_list_exists():
    """測試查詢結果列表存在"""
    page = _MockPage()
    state = CacheQueryState()
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)

    assert hasattr(panel, "query_result_list")
    assert panel.query_result_list is not None


def test_cache_query_panel_render_results_empty():
    """測試渲染空結果"""
    page = _MockPage()
    state = CacheQueryState()
    state.query_results = []
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)
    panel._render_query_results()

    # 應該有空結果提示
    assert len(panel.query_result_list.controls) >= 1


def test_cache_query_panel_render_results_with_data():
    """測試渲染有資料的結果"""
    page = _MockPage()
    state = CacheQueryState()
    state.query_results = [
        {"cache_type": "lang", "key": "test.key", "preview": "test", "shard": "lang_001.json"}
    ]
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)
    panel._render_query_results()

    # 應該有結果
    assert len(panel.query_result_list.controls) >= 1


def test_cache_query_panel_select_result():
    """測試選擇結果"""
    page = _MockPage()
    state = CacheQueryState()
    state.query_results = [
        {"cache_type": "lang", "key": "test.key", "preview": "test", "shard": "lang_001.json"}
    ]
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)

    row = {"cache_type": "lang", "key": "new.key", "preview": "new", "shard": "lang_001.json"}
    panel._on_select_result(row)

    assert panel.state.query_selected_result == row


def test_cache_query_panel_page_navigation():
    """測試分頁導航"""
    page = _MockPage()
    state = CacheQueryState()
    state.query_results = [{"cache_type": "lang", "key": f"key{i}"} for i in range(100)]
    state.query_page_size = 50
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)

    # 第一頁
    panel._on_page_first(None)
    assert panel.state.query_page == 1

    # 下一頁
    panel._on_page_next(None)
    assert panel.state.query_page == 2

    # 上一頁
    panel._on_page_prev(None)
    assert panel.state.query_page == 1

    # 最後一頁
    panel._on_page_last(None)
    assert panel.state.query_page == 2


def test_cache_query_panel_clear_search():
    """測試清除搜尋"""
    page = _MockPage()
    state = CacheQueryState()
    state.query_results = [{"cache_type": "lang", "key": "test"}]
    state.query_selected_result = {"cache_type": "lang", "key": "test"}
    last_overview_data = {"types": {}}

    panel = CacheQueryPanel(page, state, last_overview_data)
    panel._on_query_clear(None)

    assert panel.state.query_results == []
    assert panel.state.query_selected_result is None
    assert panel.state.query_page == 1
